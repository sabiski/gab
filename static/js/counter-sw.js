/* Service worker de la caisse hors app (portée : /espace/pharmacie/caisse/).
   Permet d'ouvrir la caisse sans réseau. Les ventes elles-mêmes sont mises en file
   par pharmacy-counter.js puis synchronisées au retour de la connexion. */
const CACHE = 'gp-caisse-v2';
const PAGE = '/espace/pharmacie/caisse/';
const ASSET_HOSTS = ['cdn.tailwindcss.com', 'fonts.googleapis.com', 'fonts.gstatic.com', 'unpkg.com'];

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('gp-caisse-') && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('timeout')), ms);
    promise.then((v) => { clearTimeout(t); resolve(v); }, (e) => { clearTimeout(t); reject(e); });
  });
}

// Page caisse : réseau d'abord (stock à jour), copie en cache pour le hors connexion.
async function pageNetworkFirst(request) {
  const cache = await caches.open(CACHE);
  try {
    const res = await withTimeout(fetch(request), 6000);
    const url = new URL(res.url || request.url);
    // On ne garde jamais une redirection (session expirée → page de connexion).
    // Ni redirection, ni variante « ?done=… » (bandeau de la dernière vente).
    if (res.ok && !res.redirected && url.pathname === PAGE && !new URL(request.url).search) {
      cache.put(PAGE, res.clone());
    }
    return res;
  } catch (e) {
    const cached = await cache.match(PAGE);
    if (cached) return cached;
    return new Response(
      '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">' +
      '<body style="font-family:sans-serif;padding:2rem;text-align:center">' +
      '<h1>Caisse indisponible hors connexion</h1>' +
      '<p>Ouvrez la caisse une première fois avec internet pour l\'utiliser ensuite sans réseau.</p></body>',
      { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } }
    );
  }
}

// Feuilles de style, scripts, polices : cache d'abord, rafraîchi en arrière-plan.
async function assetStaleWhileRevalidate(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request);
  const network = fetch(request)
    .then((res) => {
      if (res && (res.ok || res.type === 'opaque')) cache.put(request, res.clone());
      return res;
    })
    .catch(() => cached);
  return cached || network;
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  if (request.mode === 'navigate') {
    if (url.origin === self.location.origin && url.pathname === PAGE) {
      event.respondWith(pageNetworkFirst(request));
    }
    return;
  }
  const isStatic = url.origin === self.location.origin && url.pathname.startsWith('/static/');
  if (isStatic || ASSET_HOSTS.includes(url.hostname)) {
    event.respondWith(assetStaleWhileRevalidate(request));
  }
});
