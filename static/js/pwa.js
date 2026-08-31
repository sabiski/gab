/* Gab'Pharma PWA */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/static/js/sw.js').catch(() => {});
  });
}

/* Expose clé VAPID pour scripts push (profil) */
window.GP_VAPID_PUBLIC_KEY = document.querySelector('meta[name="vapid-public-key"]')?.content || '';
