(function (global) {
  var DEFAULT_ROOT_ID = "gp-toast-root";

  function iconFor(level) {
    if (level === "error") return "error";
    if (level === "warning") return "warning";
    if (level === "info") return "info";
    return "check_circle";
  }

  function getRoot(rootId) {
    return document.getElementById(rootId || DEFAULT_ROOT_ID);
  }

  function show(message, level, opts) {
    if (!message) return null;
    opts = opts || {};
    var root = getRoot(opts.rootId);
    if (!root) return null;

    level = level || "success";
    var toast = document.createElement("div");
    toast.className = "gp-toast gp-toast--" + level;
    toast.setAttribute("role", "status");

    var linkHtml = "";
    if (opts.linkUrl && opts.linkLabel) {
      linkHtml =
        '<a href="' +
        opts.linkUrl +
        '" class="gp-toast__link">' +
        opts.linkLabel +
        "</a>";
    }

    toast.innerHTML =
      '<span class="material-symbols-rounded sm gp-toast__icon">' +
      iconFor(level) +
      "</span>" +
      '<div class="gp-toast__body"><p class="gp-toast__msg">' +
      message +
      "</p>" +
      linkHtml +
      "</div>" +
      '<button type="button" class="gp-toast__close" aria-label="Fermer">' +
      '<span class="material-symbols-rounded sm">close</span></button>';

    root.appendChild(toast);
    toast.querySelector(".gp-toast__close").addEventListener("click", function () {
      toast.remove();
    });

    var duration = opts.duration || 5000;
    setTimeout(function () {
      toast.classList.add("is-leaving");
      setTimeout(function () {
        toast.remove();
      }, 280);
    }, duration);

    return toast;
  }

  global.GabPharmaToast = {
    show: show,
    success: function (msg, opts) {
      return show(msg, "success", opts);
    },
    error: function (msg, opts) {
      return show(msg, "error", opts);
    },
    warning: function (msg, opts) {
      return show(msg, "warning", opts);
    },
    info: function (msg, opts) {
      return show(msg, "info", opts);
    },
  };
})(window);
