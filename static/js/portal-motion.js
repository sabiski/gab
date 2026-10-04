/* Gab'Pharma — compteurs animés et apparitions au défilement (back-office). */
(function () {
  var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function format(n) {
    return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  }

  // <span data-countup>12 500</span> → anime de 0 à 12 500 (suffixe conservé)
  function countUp(el) {
    var raw = (el.getAttribute('data-countup') || el.textContent || '').trim();
    var match = raw.replace(/[\s  ]/g, '').match(/^(-?\d+)(.*)$/);
    if (!match) return;
    var target = parseInt(match[1], 10);
    var suffix = match[2] ? ' ' + match[2].trim() : '';
    if (reduce || target === 0) { el.textContent = format(target) + suffix; return; }
    var start = null;
    var duration = Math.min(1100, 450 + String(Math.abs(target)).length * 90);
    function step(ts) {
      if (!start) start = ts;
      var p = Math.min(1, (ts - start) / duration);
      var eased = 1 - Math.pow(1 - p, 3);
      el.textContent = format(target * eased) + suffix;
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function init() {
    var counters = document.querySelectorAll('[data-countup]');
    var reveals = document.querySelectorAll('.gp-reveal');
    if (!('IntersectionObserver' in window)) {
      counters.forEach(countUp);
      reveals.forEach(function (el) { el.classList.add('is-visible'); });
      return;
    }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        if (el.hasAttribute('data-countup')) countUp(el);
        if (el.classList.contains('gp-reveal')) el.classList.add('is-visible');
        io.unobserve(el);
      });
    }, { threshold: 0.15 });
    counters.forEach(function (el) { io.observe(el); });
    reveals.forEach(function (el) { io.observe(el); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
