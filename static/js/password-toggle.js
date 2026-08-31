(function () {
  function enhancePasswordInput(input) {
    if (!input || input.type !== "password" || input.dataset.pwToggle === "1") return;
    if (input.closest(".pw-field")) return;

    input.dataset.pwToggle = "1";

    var wrap = document.createElement("div");
    wrap.className = "pw-field";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);

    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "pw-field__toggle";
    btn.setAttribute("aria-label", "Afficher le mot de passe");
    btn.setAttribute("aria-pressed", "false");
    btn.innerHTML = '<span class="material-symbols-rounded sm" aria-hidden="true">visibility</span>';
    wrap.appendChild(btn);

    btn.addEventListener("click", function () {
      var visible = input.type === "text";
      input.type = visible ? "password" : "text";
      btn.setAttribute("aria-label", visible ? "Afficher le mot de passe" : "Masquer le mot de passe");
      btn.setAttribute("aria-pressed", visible ? "false" : "true");
      btn.querySelector(".material-symbols-rounded").textContent = visible ? "visibility" : "visibility_off";
      input.focus();
    });
  }

  function initPasswordToggles(root) {
    (root || document).querySelectorAll('input[type="password"]:not([data-no-pw-toggle])').forEach(enhancePasswordInput);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { initPasswordToggles(); });
  } else {
    initPasswordToggles();
  }

  window.initPasswordToggles = initPasswordToggles;
})();
