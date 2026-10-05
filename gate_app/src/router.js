import { createRouter, createWebHistory } from "vue-router";

const routes = [
	{ path: "/", name: "dashboard", component: () => import("./views/Dashboard.vue") },
	{ path: "/scan", name: "scan", component: () => import("./views/QRScanner.vue") },
	{ path: "/checkin/:name", name: "checkin", component: () => import("./views/CheckIn.vue"), props: true },
	{ path: "/checkout/:name", name: "checkout", component: () => import("./views/CheckOut.vue"), props: true },
	{ path: "/new-entry", name: "new-entry", component: () => import("./views/GateEntryForm.vue") },
	{ path: "/visitors", name: "visitors", component: () => import("./views/VisitorList.vue") },
	{ path: "/requests", name: "requests", component: () => import("./views/PendingRequests.vue") },
	{ path: "/:pathMatch(.*)*", redirect: "/" },
];

export default createRouter({ history: createWebHistory("/gate"), routes });
