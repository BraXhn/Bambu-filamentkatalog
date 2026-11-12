// Service worker: katalogen fungerar offline med senast hämtade data. Nätet först, cachen som reserv.
// Bara sidan, skripten, katalogdata, priser och stil sparas – aldrig API-svar, inloggning eller kontosidor.
const CACHE = 'katalog-offline-v1';
const OFFLINE = ['/', '/index.html', '/data.json', '/offers.json', '/manifest.webmanifest'];
const cacheable = url => url.origin === location.origin &&
  (OFFLINE.includes(url.pathname) || /^\/(app|prices-ui)\.js$/.test(url.pathname) || url.pathname.startsWith('/assets/'));

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (!cacheable(url)) return;
  e.respondWith(fetch(req).then(res => {
    // Spara bara riktiga svar (inte omdirigering till inloggningen eller fel).
    if (res.ok && !res.redirected && res.type === 'basic') {
      const copy = res.clone();
      caches.open(CACHE).then(c => c.put(url.pathname === '/index.html' ? '/' : req, copy));
    }
    return res;
  }).catch(() => caches.match(req, { ignoreSearch: /^\/(app|prices-ui)\.js$/.test(url.pathname) })
    .then(hit => hit || (req.mode === 'navigate' ? caches.match('/') : undefined))
    .then(hit => hit || Response.error())));
});
