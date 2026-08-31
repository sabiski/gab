(function () {
  var pending = {};

  function getCsrfToken(form) {
    var input = form.querySelector('[name="csrfmiddlewaretoken"]');
    return input ? input.value : "";
  }

  function getFeedback(form) {
    return form.querySelector("[data-cm-otp-feedback]");
  }

  function setFeedback(form, state, message) {
    var fb = getFeedback(form);
    if (!fb) return;
    fb.classList.remove("hidden", "cm-otp-feedback--wait", "cm-otp-feedback--ok", "cm-otp-feedback--err");
    if (!state) {
      fb.classList.add("hidden");
      fb.textContent = "";
      return;
    }
    if (state === "wait") fb.classList.add("cm-otp-feedback--wait");
    if (state === "ok") fb.classList.add("cm-otp-feedback--ok");
    if (state === "err") fb.classList.add("cm-otp-feedback--err");
    fb.textContent = message;
  }

  function setDigitState(form, state) {
    form.querySelectorAll(".cm-otp__digit").forEach(function (d) {
      d.classList.remove("cm-otp__digit--ok", "cm-otp__digit--err");
      if (state === "ok") d.classList.add("cm-otp__digit--ok");
      if (state === "err") d.classList.add("cm-otp__digit--err");
    });
  }

  function markValidated(form) {
    form.setAttribute("data-client-code-validated", "1");
    form.setAttribute("data-handoff-validated", "1");
    var banner = form.querySelector("[data-cm-validated-banner]");
    if (banner) banner.classList.remove("hidden");
    form.querySelectorAll(".cm-otp__digit, [data-cm-auto-input]").forEach(function (el) {
      el.setAttribute("readonly", "readonly");
    });
  }

  function actionForType(type) {
    return type === "pharmacy" ? "validate_pharmacy_pickup" : "validate_client_code";
  }

  function codeFieldForType(form, type) {
    if (type === "pharmacy") {
      return form.querySelector('[name="pharmacy_handoff_code"]');
    }
    return (
      form.querySelector('[name="validation_code"]') ||
      form.querySelector('[name="validation_code_manual"]')
    );
  }

  window.courierInstantValidate = function (form, type, code) {
    if (!form || !code) return Promise.resolve();
    var key = form.id + ":" + type;
    if (pending[key] === code) return pending[key + ":promise"] || Promise.resolve();
    pending[key] = code;

    setFeedback(form, "wait", "Vérification en cours…");
    setDigitState(form, null);

    var fd = new FormData(form);
    fd.set("action", actionForType(type));
    if (type === "client") {
      fd.set("validation_code", code);
      fd.set("validation_code_manual", code);
    } else {
      fd.set("pharmacy_handoff_code", code);
    }

    var promise = fetch(form.getAttribute("action") || window.location.href, {
      method: "POST",
      body: fd,
      headers: { "X-Requested-With": "XMLHttpRequest" },
      credentials: "same-origin",
    })
      .then(function (res) {
        return res.json();
      })
      .then(function (data) {
        if (data.ok) {
          setFeedback(form, "ok", data.message || "Code valide");
          setDigitState(form, "ok");
          markValidated(form);
        } else {
          setFeedback(form, "err", data.message || "Code incorrect");
          setDigitState(form, "err");
          delete pending[key];
        }
        return data;
      })
      .catch(function () {
        setFeedback(form, "err", "Impossible de vérifier le code. Réessayez.");
        setDigitState(form, "err");
        delete pending[key];
      });

    pending[key + ":promise"] = promise;
    return promise;
  };

  function maybeAutoValidate(form, type, code, minLen) {
    var clean = (code || "").replace(/\D/g, "");
    if (clean.length < minLen) {
      if (form.getAttribute("data-client-code-validated") !== "1" && form.getAttribute("data-handoff-validated") !== "1") {
        setFeedback(form, null, "");
        getFeedback(form).classList.add("hidden");
        setDigitState(form, null);
      }
      return;
    }
    if (minLen === 6 && clean.length !== 6) return;
    window.courierInstantValidate(form, type, clean);
  }

  function initOtp(container) {
    var form = container.closest("form");
    var hidden = container.querySelector(".cm-otp-hidden");
    var digits = container.querySelectorAll(".cm-otp__digit");
    if (!hidden || !digits.length || !form) return;

    var type = container.getAttribute("data-cm-auto-validate") || "client";
    var codeLen = parseInt(container.getAttribute("data-code-length") || "6", 10);

    function syncHidden() {
      var code = "";
      digits.forEach(function (d) {
        code += (d.value || "").replace(/\D/g, "").slice(-1);
      });
      hidden.value = code;
      return code;
    }

    function onCodeChange(code) {
      if (form.getAttribute("data-client-code-validated") === "1") return;
      maybeAutoValidate(form, type, code, codeLen);
    }

    digits.forEach(function (input, idx) {
      input.addEventListener("input", function () {
        input.value = input.value.replace(/\D/g, "").slice(-1);
        var code = syncHidden();
        if (input.value && digits[idx + 1]) digits[idx + 1].focus();
        onCodeChange(code);
      });
      input.addEventListener("keydown", function (e) {
        if (e.key === "Backspace" && !input.value && digits[idx - 1]) {
          digits[idx - 1].focus();
        }
      });
      input.addEventListener("paste", function (e) {
        e.preventDefault();
        var text = (e.clipboardData || window.clipboardData).getData("text") || "";
        var nums = text.replace(/\D/g, "").slice(0, digits.length);
        nums.split("").forEach(function (ch, i) {
          if (digits[i]) digits[i].value = ch;
        });
        onCodeChange(syncHidden());
      });
    });
  }

  document.querySelectorAll("[data-cm-otp]").forEach(initOtp);

  document.querySelectorAll("[data-cm-auto-input]").forEach(function (input) {
    var form = input.closest("form");
    if (!form) return;
    var type = input.getAttribute("data-cm-auto-input") || "client";
    var codeLen = parseInt(input.getAttribute("data-code-length") || "6", 10);
    var minLen = parseInt(input.getAttribute("data-code-min") || String(codeLen), 10);
    var debounce;

    input.addEventListener("input", function () {
      input.value = input.value.replace(/\D/g, "").slice(0, codeLen);
      var hidden = form.querySelector(".cm-otp-hidden");
      if (hidden) hidden.value = input.value;
      var digits = form.querySelectorAll(".cm-otp__digit");
      input.value.split("").forEach(function (ch, i) {
        if (digits[i]) digits[i].value = ch;
      });
      clearTimeout(debounce);
      debounce = setTimeout(function () {
        maybeAutoValidate(form, type, input.value, input.value.length >= codeLen ? codeLen : minLen);
      }, input.value.length >= codeLen ? 0 : 450);
    });
  });

  document.querySelectorAll("[data-cm-tabs]").forEach(function (wrap) {
    var buttons = wrap.querySelectorAll("[data-cm-tab]");
    var panels = wrap.querySelectorAll("[data-cm-panel]");
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var id = btn.getAttribute("data-cm-tab");
        buttons.forEach(function (b) {
          b.classList.toggle("is-active", b === btn);
        });
        panels.forEach(function (p) {
          p.classList.toggle("hidden", p.getAttribute("data-cm-panel") !== id);
        });
      });
    });
  });

  document.querySelectorAll("[data-cm-signature]").forEach(function (wrap) {
    var canvas = wrap.querySelector("canvas");
    var clearBtn = wrap.querySelector("[data-cm-sig-clear]");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var drawing = false;

    function resize() {
      var rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * (window.devicePixelRatio || 1);
      canvas.height = rect.height * (window.devicePixelRatio || 1);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
      ctx.strokeStyle = "#374151";
      ctx.lineWidth = 2;
      ctx.lineCap = "round";
    }
    resize();
    window.addEventListener("resize", resize);

    function pos(e) {
      var r = canvas.getBoundingClientRect();
      var t = e.touches ? e.touches[0] : e;
      return { x: t.clientX - r.left, y: t.clientY - r.top };
    }
    function start(e) {
      drawing = true;
      var p = pos(e);
      ctx.beginPath();
      ctx.moveTo(p.x, p.y);
      e.preventDefault();
    }
    function move(e) {
      if (!drawing) return;
      var p = pos(e);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
      e.preventDefault();
    }
    function end() {
      drawing = false;
    }
    canvas.addEventListener("mousedown", start);
    canvas.addEventListener("mousemove", move);
    canvas.addEventListener("mouseup", end);
    canvas.addEventListener("mouseleave", end);
    canvas.addEventListener("touchstart", start, { passive: false });
    canvas.addEventListener("touchmove", move, { passive: false });
    canvas.addEventListener("touchend", end);
    if (clearBtn) {
      clearBtn.addEventListener("click", function () {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      });
    }
  });

  document.querySelectorAll("[data-cm-photo]").forEach(function (wrap) {
    var input = wrap.querySelector('input[type="file"]');
    var preview = wrap.querySelector(".cm-photo-preview");
    var drop = wrap.querySelector(".cm-photo-drop");
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
})();
