import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import router from "./router";
import "./style.css";

createApp(App).use(createPinia()).use(router).mount("#app");

// Offline shell + installability. Served from an API route with
// `Service-Worker-Allowed: /gate`, so it can control the /gate scope.
if ("serviceWorker" in navigator) {
	window.addEventListener("load", () => {
		navigator.serviceWorker
			.register("/api/method/visitormanagement.visitor_management.api.gate_pwa.service_worker", { scope: "/gate" })
			.catch((e) => console.warn("Gate service worker not registered", e));
	});
}
