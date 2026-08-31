(function () {
  var scanners = {};

  function getForm(deliveryId) {
    return (
      document.getElementById("pharmacy-pickup-form-" + deliveryId) ||
      document.getElementById("pharmacy-pickup-form-dash-" + deliveryId)
    );
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

  document.querySelectorAll(".courier-pharmacy-scan-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (typeof Html5Qrcode === "undefined") {
        alert("Scanner indisponible — rechargez la page.");
        return;
      }
      var deliveryId = btn.getAttribute("data-delivery-id");
      var panel = document.querySelector(
        '[data-pharmacy-scanner-panel="' + deliveryId + '"]'
      );
      var mountId = "pharmacy-scanner-" + deliveryId;
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
            var hidden = form.querySelector(".courier-pharmacy-qr-input");
            if (hidden) hidden.value = decoded;
            stopScanner(deliveryId);
            submitPharmacyValidation(form);
          },
          function () {}
        )
        .catch(function () {
          alert("Impossible d'accéder à la caméra.");
          stopScanner(deliveryId);
        });
    });
  });

  document.querySelectorAll(".courier-pharmacy-scan-close").forEach(function (btn) {
    btn.addEventListener("click", function () {
      stopScanner(btn.getAttribute("data-delivery-id"));
    });
  });

  function submitPharmacyValidation(form) {
    var codeInput = form.querySelector('[name="pharmacy_handoff_code"]');
    var qr = form.querySelector(".courier-pharmacy-qr-input");
    if (qr && qr.value) {
      var fd = new FormData(form);
      fd.set("action", "validate_pharmacy_pickup");
      setPharmacyFeedback(form, "wait", "Vérification en cours…");
      fetch(form.getAttribute("action") || window.location.href, {
        method: "POST",
        body: fd,
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      })
        .then(function (res) {
          return res.json();
        })
        .then(function (data) {
          setPharmacyFeedback(form, data.ok ? "ok" : "err", data.message);
          if (data.ok) form.setAttribute("data-handoff-validated", "1");
        })
        .catch(function () {
          setPharmacyFeedback(form, "err", "Impossible de vérifier le QR. Réessayez.");
        });
      return;
    }
    if (codeInput && codeInput.value && window.courierInstantValidate) {
      window.courierInstantValidate(form, "pharmacy", codeInput.value.replace(/\D/g, ""));
    }
  }

  function setPharmacyFeedback(form, state, message) {
    var fb = form.querySelector("[data-cm-otp-feedback]");
    if (!fb) return;
    fb.classList.remove("hidden", "cm-otp-feedback--wait", "cm-otp-feedback--ok", "cm-otp-feedback--err");
    if (state === "wait") fb.classList.add("cm-otp-feedback--wait");
    if (state === "ok") fb.classList.add("cm-otp-feedback--ok");
    if (state === "err") fb.classList.add("cm-otp-feedback--err");
    fb.textContent = message;
  }

  document.querySelectorAll(".courier-pharmacy-pickup-form").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      var submitter = e.submitter;
      var action =
        (submitter && submitter.name === "action" && submitter.value) ||
        (form.querySelector('input[type="hidden"][data-cm-action="validate"]') || {}).value ||
        "advance";
      var qr = form.querySelector(".courier-pharmacy-qr-input");
      var code = form.querySelector('[name="pharmacy_handoff_code"]');
      var hasQr = qr && qr.value;
      var hasCode = code && code.value && code.value.trim();
      var handoffValidated = form.getAttribute("data-handoff-validated") === "1";
      if (action === "validate_pharmacy_pickup" && !hasQr && !hasCode) {
        e.preventDefault();
        alert("Saisissez le code ou scannez le QR code sur le colis ou le bon de commande.");
        return;
      }
      if (action === "advance" && !hasQr && !hasCode && !handoffValidated) {
        e.preventDefault();
        alert("Scannez le QR code ou validez le code pharmacie avant de confirmer la récupération.");
      }
    });
  });
})();
