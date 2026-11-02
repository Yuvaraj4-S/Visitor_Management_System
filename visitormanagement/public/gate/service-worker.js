// Security Gate PWA service worker (scope /gate).
// Caches the app shell so the gate screen still opens on a flaky connection.
// API calls are never cached: gate decisions must always use live data.
const CACHE = "vms-gate-v4"; // bump to force every device to drop the old app shell
const SHELL = ["/gate", "/assets/visitormanagement/gate/gate.js", "/assets/visitormanagement/gate/gate.css", "/assets/visitormanagement/gate/manifest.json"];

self.addEventListener("install", (event) => {
	event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
	self.skipWaiting();
});

self.addEventListener("activate", (event) => {
	event.waitUntil(
		caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
	);
	self.clients.claim();
});

self.addEventListener("fetch", (event) => {
	const url = new URL(event.request.url);
	if (event.request.method !== "GET" || url.pathname.startsWith("/api/")) return;

	if (event.request.mode === "navigate") {
		// network first for the page, cached shell when offline
		event.respondWith(fetch(event.request).catch(() => caches.match("/gate")));
		return;
	}
	// Content-hashed files (chunks/, assets/) never change under the same name: serve them
	// from cache and skip the network round trip entirely.
	if (/\/assets\/visitormanagement\/gate\/(chunks|assets)\//.test(url.pathname)) {
		event.respondWith(
			caches.match(event.request).then(
				(hit) =>
					hit ||
					fetch(event.request).then((res) => {
						const copy = res.clone();
						caches.open(CACHE).then((c) => c.put(event.request, copy));
						return res;
					})
			)
		);
		return;
	}
	if (url.pathname.startsWith("/assets/visitormanagement/gate/")) {
		event.respondWith(
			fetch(event.request, { cache: "no-cache" })
				.then((res) => {
					const copy = res.clone();
					caches.open(CACHE).then((c) => c.put(event.request, copy));
					return res;
				})
				.catch(() => caches.match(event.request))
		);
	}
});
