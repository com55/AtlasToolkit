const CACHE_NAME = 'atlas-toolkit-v39';
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
  'js/app-bar.js',
  'js/atlas-api.js',
  'js/atlas-converter.js',
  'js/atlas-document.js',
  'js/atlas-extracter.js',
  'js/atlas-modifier.js',
  'js/atlas-session.js',
  'js/core-region-ops.js',
  'js/dialogs.js',
  'js/drop.js',
  'js/modify-mode.js',
  'js/panel-resizer.js',
  'js/platform.js',
  'js/preview.js',
  'js/region-list.js',
  'js/region-mesh-lookup.js',
  'js/region-mesh-mask.js',
  'js/state.js',
  'js/updates.js',
  // These 5 filenames are pinned to the vendored parser's own src/*.js
  // file list (see scripts/pull-vendor-deps.sh) -- cache.addAll() below is
  // atomic, so a future vendor-tag bump that adds/renames a file here
  // without a matching edit fails the whole SW install, not just masking.
  // Keep this list in sync with that script's TAG when bumping it.
  'js/vendor/spine-skeleton-binary/binary-reader.js',
  'js/vendor/spine-skeleton-binary/detect-version.js',
  'js/vendor/spine-skeleton-binary/index.js',
  'js/vendor/spine-skeleton-binary/read-skeleton-38.js',
  'js/vendor/spine-skeleton-binary/read-skeleton-42.js',
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
