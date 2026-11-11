const CACHE_NAME = __CACHE_NAME__;
const APP_BASE_URL = new URL('./', self.location.href);
const APP_SHELL_URL = new URL('index.html', APP_BASE_URL).href;
const CORE_ASSETS = __CORE_ASSETS__.map((path) => new URL(path, APP_BASE_URL).href);

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE_ASSETS))
  );
  // Don't skipWaiting() here — a deployed update would otherwise swap the
  // controller mid-session against the page the user is still running. The
  // new worker waits until the page acknowledges it via a SKIP_WAITING
  // message (see script.js's update-prompt flow).
});

self.addEventListener('message', (event) => {
  if (event.data === 'SKIP_WAITING' || event.data?.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k.startsWith('atlas-toolkit-py-') && k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  event.respondWith(
    caches.open(CACHE_NAME).then((cache) => cache.match(event.request).then((cached) => {
      if (cached) return cached;
      return fetch(event.request)
        .then((response) => {
          if (!response || response.status !== 200 || response.type === 'opaque') {
            return response;
          }
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => {
          if (event.request.mode === 'navigate') {
            return cache.match(APP_SHELL_URL);
          }
          return Response.error();
        });
    }))
  );
});
