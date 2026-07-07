const CACHE_NAME = 'atlas-toolkit-v5';
const APP_BASE_URL = new URL('./', self.location.href);
const APP_SHELL_URL = new URL('index.html', APP_BASE_URL).href;
const CORE_ASSETS = [
  '.',
  'index.html',
  'style.css',
  'script.js',
  'manifest.json',
  'icon.ico',
  'icons/icon-192.png',
  'icons/icon-512.png',
  'js/atlas-api.js',
  'js/atlas-converter.js',
  'js/atlas-extracter.js',
  'js/atlas-modifier.js',
  'js/dialogs.js',
  'js/drop.js',
  'js/modify-mode.js',
  'js/panel-resizer.js',
  'js/platform.js',
  'js/preview.js',
  'js/region-list.js',
  'js/state.js',
  'js/zip.js',
].map((path) => new URL(path, APP_BASE_URL).href);

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
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;

  event.respondWith(
    caches.match(event.request).then((cached) => {
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
            return caches.match(APP_SHELL_URL);
          }
          return Response.error();
        });
    })
  );
});
