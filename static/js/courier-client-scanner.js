(function () {
  function submitClientValidation(form) {
    var qr = form.querySelector(".courier-client-qr-input");
    if (qr && qr.value && window.courierInstantValidate) {
      return window.courierInstantValidate(form, "client", "").then(function () {
        /* QR path handled server-side via client_qr_payload in a dedicated call */
      });
    }
  }

  function stopScanner(deliveryId) {
    var entry = scanners[deliveryId];
    if (!entry) return;
    entry.scanner
      .stop()
      .catch(function () {})
      .finally(function () {
        entry.panel.classList.add("hidden");
        delete scanners[deliveryId];
      });
  }

  var scanners = {};

  function getForm(deliveryId) {
    return (
      document.getElementById("delivery-form-" + deliveryId) ||
      document.getElementById("delivery-form-dash-" + deliveryId)
    );
  }

  function validateQrScan(form) {
    var fd = new FormData(form);
    fd.set("action", "validate_client_code");
    setFeedbackLoading(form);
    return fetch(form.getAttribute("action") || window.location.href, {
      method: "POST",
      body: fd,
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (window.courierInstantValidate) {
          /* reuse UI helpers via a synthetic success path */
          if (data.ok) {
            form.setAttribute("data-client-code-validated", "1");
          }
        }
        if (data.ok) {
          var fb = form.querySelector("[data-cm-otp-feedback]");
          if (fb) {
            fb.classList.remove("hidden", "cm-otp-feedback--err", "cm-otp-feedback--wait");
            fb.classList.add("cm-otp-feedback--ok");
            fb.textContent = data.message || "Code valide";
          }
          var banner = form.querySelector("[data-cm-validated-banner]");
          if (banner) banner.classList.remove("hidden");
        }
        return data;
      });
  }

  function setFeedbackLoading(form) {
    var fb = form.querySelector("[data-cm-otp-feedback]");
    if (!fb) return;
    fb.classList.remove("hidden", "cm-otp-feedback--ok", "cm-otp-feedback--err");
    fb.classList.add("cm-otp-feedback--wait");
    fb.textContent = "Vérification en cours…";
  }

  document.querySelectorAll(".courier-client-scan-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (typeof Html5Qrcode === "undefined") {
        alert("Scanner indisponible — rechargez la page.");
        return;
      }
      var deliveryId = btn.getAttribute("data-delivery-id");
      var panel = document.querySelector(
        '[data-client-scanner-panel="' + deliveryId + '"]'
      );
      var mountId = "client-scanner-" + deliveryId;
      if (!panel || !document.getElementById(mountId)) return;

      if (scanners[deliveryId]) {
        stopScanner(deliveryId);
        return;
      }

      panel.classList.remove("hidden");
      var scanner = new Html5Qrcode(mountId);
      scanners[deliveryId] = { scanner: scanner, panel: panel };

      scanner
        .start(
          { facingMode: "environment" },
          { fps: 8, qrbox: { width: 220, height: 220 } },
          function (decoded) {
            var form = getForm(deliveryId);
            if (!form) return;
            var hidden = form.querySelector(".courier-client-qr-input");
            if (hidden) hidden.value = decoded;
            stopScanner(deliveryId);
            validateQrScan(form).then(function (data) {
              if (!data.ok) alert(data.message || "QR invalide");
            });
          },
          function () {}
        )
        .catch(function () {
          alert("Impossible d'accéder à la caméra.");
          stopScanner(deliveryId);
        });
    });
  });

  document.querySelectorAll(".courier-client-scan-close").forEach(function (btn) {
    btn.addEventListener("click", function () {
      stopScanner(btn.getAttribute("data-delivery-id"));
    });
  });

  document.querySelectorAll(".courier-delivery-form").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      var submitter = e.submitter;
      var action = submitter && submitter.name === "action" ? submitter.value : "advance";
      if (action !== "advance") return;
      var qr = form.querySelector(".courier-client-qr-input");
      var code = form.querySelector('[name="validation_code"]');
      var manual = form.querySelector('[name="validation_code_manual"]');
      var hasQr = qr && qr.value;
      var hasCode = code && code.value && code.value.trim();
      var hasManual = manual && manual.value && manual.value.trim();
      var prevalidated = form.getAttribute("data-client-code-validated") === "1";
      if (!hasQr && !hasCode && !hasManual && !prevalidated) {
        e.preventDefault();
        var fb = form.querySelector("[data-cm-otp-feedback]");
        if (fb) {
          fb.classList.remove("hidden");
          fb.classList.add("cm-otp-feedback--err");
          fb.textContent = "Saisissez les 6 chiffres du code client — la vérification est automatique.";
        }
      }
    });
  });
})();
