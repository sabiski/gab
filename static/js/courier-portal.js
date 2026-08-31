(function () {
  document.querySelectorAll("[data-cp-tabs]").forEach(function (root) {
    var buttons = root.querySelectorAll("[data-cp-tab]");
    var panels = root.querySelectorAll("[data-cp-panel]");
    function activate(id) {
      buttons.forEach(function (btn) {
        btn.classList.toggle("is-active", btn.getAttribute("data-cp-tab") === id);
      });
      panels.forEach(function (panel) {
        panel.classList.toggle("hidden", panel.getAttribute("data-cp-panel") !== id);
      });
    }
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        activate(btn.getAttribute("data-cp-tab"));
      });
    });
    var initial = root.getAttribute("data-cp-initial") || buttons[0]?.getAttribute("data-cp-tab");
    if (initial) activate(initial);
  });

  var timerEl = document.querySelector("[data-urgent-timer]");
  if (timerEl) {
    var deadline = timerEl.getAttribute("data-deadline");
    if (deadline) {
      var end = new Date(deadline).getTime();
      function tick() {
        var diff = Math.max(0, Math.floor((end - Date.now()) / 1000));
        var mm = String(Math.floor(diff / 60)).padStart(2, "0");
        var ss = String(diff % 60).padStart(2, "0");
        timerEl.textContent = mm + ":" + ss;
      }
      tick();
      setInterval(tick, 1000);
    }
  }

  document.querySelectorAll("[data-photo-preview]").forEach(function (wrap) {
    var input = wrap.querySelector('input[type="file"]');
    var preview = wrap.querySelector("img");
    var drop = wrap.querySelector("[data-photo-drop]");
    if (!input || !preview) return;
    function show(file) {
      if (!file || !file.type.startsWith("image/")) return;
      preview.src = URL.createObjectURL(file);
      preview.classList.remove("hidden");
    }
    input.addEventListener("change", function () {
      if (input.files[0]) show(input.files[0]);
    });
    if (drop) {
      drop.addEventListener("click", function () {
        input.click();
      });
    }
  });

  document.querySelectorAll("[data-cp-file-upload]").forEach(function (wrap) {
    var input = wrap.querySelector(".cp-file-upload__input");
    var zone = wrap.querySelector(".cp-file-upload__zone");
    var nameEl = wrap.querySelector("[data-file-name]");
    var hintEl = wrap.querySelector("[data-file-hint]");
    var clearBtn = wrap.querySelector("[data-file-clear]");
    if (!input || !zone) return;

    var defaultHint = hintEl ? hintEl.textContent : "";

    function formatSize(bytes) {
      if (bytes < 1024) return bytes + " o";
      if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " Ko";
      return (bytes / (1024 * 1024)).toFixed(1) + " Mo";
    }

    function setFile(file) {
      if (!file) {
        input.value = "";
        zone.classList.remove("has-selection");
        if (nameEl) {
          nameEl.textContent = "";
          nameEl.classList.add("hidden");
        }
        if (hintEl) hintEl.textContent = defaultHint;
        if (clearBtn) clearBtn.classList.add("hidden");
        return;
      }
      zone.classList.add("has-selection");
      if (nameEl) {
        nameEl.textContent = file.name + " · " + formatSize(file.size);
        nameEl.classList.remove("hidden");
      }
      if (clearBtn) clearBtn.classList.remove("hidden");
    }

    input.addEventListener("change", function () {
      setFile(input.files && input.files[0] ? input.files[0] : null);
    });

    if (clearBtn) {
      clearBtn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        setFile(null);
      });
    }

    ["dragenter", "dragover"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.add("is-dragover");
      });
    });
    ["dragleave", "drop"].forEach(function (evt) {
      zone.addEventListener(evt, function (e) {
        e.preventDefault();
        e.stopPropagation();
        zone.classList.remove("is-dragover");
      });
    });
    zone.addEventListener("drop", function (e) {
      var files = e.dataTransfer && e.dataTransfer.files;
      if (!files || !files.length) return;
      var file = files[0];
      try {
        var dt = new DataTransfer();
        dt.items.add(file);
        input.files = dt.files;
      } catch (err) {
        return;
      }
      setFile(file);
    });
  });
})();
