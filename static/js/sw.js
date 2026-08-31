const CACHE = 'gabpharma-v1';
const ASSETS = ['/', '/recherche/', '/offline/', '/static/css/app.css'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('push', (event) => {
  let payload = { title: "Gab'Pharma", body: '', data: {} };
  try {
    if (event.data) payload = event.data.json();
  } catch (e) {}
  event.waitUntil(
    self.registration.showNotification(payload.title || "Gab'Pharma", {
      body: payload.body || '',
      icon: payload.icon || '/static/icons/icon-192.png',
      data: payload.data || {},
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/profil/notifications/';
  event.waitUntil(clients.openWindow(url));
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const clone = res.clone();
        caches.open(CACHE).then((cache) => cache.put(event.request, clone));
        return res;
      })
      .catch(() =>
        caches.match(event.request).then((cached) => cached || caches.match('/offline/'))
      )
  );
});
