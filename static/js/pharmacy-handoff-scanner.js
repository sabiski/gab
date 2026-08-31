(function () {
  var scanners = {};

  function getForm(panel) {
    return panel ? panel.querySelector(".po-handoff__form") : null;
  }

  function stopScanner(orderId) {
    var entry = scanners[orderId];
    if (!entry) return;
    entry.scanner
      .stop()
      .catch(function () {})
      .finally(function () {
        entry.panel.classList.add("hidden");
        delete scanners[orderId];
      });
  }

  document.querySelectorAll("[data-handoff-scan]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      if (typeof Html5Qrcode === "undefined") {
        alert("Scanner indisponible — rechargez la page.");
        return;
      }
      var orderId = btn.getAttribute("data-order-id");
      var panel = document.querySelector('[data-scanner-panel="' + orderId + '"]');
      var mount = document.getElementById("handoff-scanner-" + orderId);
      if (!panel || !mount) return;

      if (scanners[orderId]) {
        stopScanner(orderId);
        return;
      }

      panel.classList.remove("hidden");
      var scanner = new Html5Qrcode("handoff-scanner-" + orderId);
      scanners[orderId] = { scanner: scanner, panel: panel };

      scanner
        .start(
          { facingMode: "environment" },
          { fps: 8, qrbox: { width: 220, height: 220 } },
          function (decoded) {
            var form = getForm(panel.closest(".po-handoff"));
            if (!form) return;
            var hidden = form.querySelector(".po-handoff-qr-input");
            if (hidden) hidden.value = decoded;
            stopScanner(orderId);
            form.submit();
          },
          function () {}
        )
        .catch(function () {
          alert("Impossible d'accéder à la caméra.");
          stopScanner(orderId);
        });
    });
  });

  document.querySelectorAll("[data-handoff-scan-close]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var panel = btn.closest("[data-scanner-panel]");
      if (!panel) return;
      stopScanner(panel.getAttribute("data-scanner-panel"));
    });
  });
})();
