import { defineStore } from "pinia";
import { call } from "../api";

export const useVisitors = defineStore("visitors", {
	state: () => ({ dashboard: null, loading: false, error: null, loadedAt: 0 }),
	actions: {
		async load(force = false) {
			if (this.loading || (!force && this.dashboard && Date.now() - this.loadedAt < 15000)) return;
			this.loading = true;
			this.error = null;
			try {
				this.dashboard = await call("get_gate_dashboard");
				this.loadedAt = Date.now();
			} catch (e) {
				this.error = e.message;
			} finally {
				this.loading = false;
			}
		},
	},
});
