(function () {
  function ensureHidden(form, name) {
    var input = form.querySelector('[name="' + name + '"]');
    if (!input) {
      input = document.createElement("input");
      input.type = "hidden";
      input.name = name;
      form.appendChild(input);
    }
    return input;
  }

  function requestGpsThenSubmit(form, btn) {
    if (!navigator.geolocation) {
      alert("Le GPS est obligatoire. Utilisez un appareil avec localisation activée.");
      return;
    }
    var label = btn ? btn.textContent : "";
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Localisation…";
    }
    navigator.geolocation.getCurrentPosition(
      function (pos) {
        ensureHidden(form, "latitude").value = pos.coords.latitude.toFixed(6);
        ensureHidden(form, "longitude").value = pos.coords.longitude.toFixed(6);
        form.submit();
      },
      function () {
        if (btn) {
          btn.disabled = false;
          btn.textContent = label;
        }
        alert(
          "Autorisez l'accès à votre position pour prendre une course ou passer en ligne."
        );
      },
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
    );
  }

  document.querySelectorAll(".courier-gps-form").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      var lat = form.querySelector('[name="latitude"]');
      var lng = form.querySelector('[name="longitude"]');
      if (lat && lat.value && lng && lng.value) return;
      e.preventDefault();
      var btn = form.querySelector('[type="submit"]');
      requestGpsThenSubmit(form, btn);
    });
  });

  document.querySelectorAll(".courier-gps-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var formId = btn.getAttribute("data-gps-form");
      var form = formId ? document.getElementById(formId) : btn.closest("form");
      if (!form) return;
      requestGpsThenSubmit(form, btn);
    });
  });
})();
