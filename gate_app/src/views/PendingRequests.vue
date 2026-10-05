<template>
	<div class="space-y-3">
		<div class="flex items-center justify-between">
			<h2 class="font-bold">Gate entry requests (today)</h2>
			<button class="text-xs font-semibold text-slate-500" @click="load">{{ loading ? "Loading…" : "Refresh" }}</button>
		</div>
		<div v-for="r in rows" :key="r.name" class="card space-y-1">
			<div class="flex items-center justify-between">
				<p class="font-semibold">{{ r.visitor_full_name }}</p>
				<StatusBadge :status="r.status" />
			</div>
			<p class="text-xs text-slate-500">{{ r.name }} · {{ r.visitor_type }} · {{ r.mapping_type === "Group" ? `Group ${r.visitor_group}` : r.person_to_visit_name || r.person_to_visit }}</p>
			<p class="text-xs text-slate-400">Sent {{ time(r.request_datetime || r.creation) }}</p>
			<p v-if="r.status === 'Rejected'" class="text-sm text-rose-600">Rejected: {{ r.rejection_reason || "—" }}</p>
			<router-link v-if="r.visitor_pass" :to="`/checkin/${encodeURIComponent(r.visitor_pass)}`" class="btn-success mt-2 w-full">
				Approved — open pass {{ r.visitor_pass }}
			</router-link>
		</div>
		<p v-if="!loading && !rows.length" class="card text-center text-sm text-slate-400">No gate entry requests today.</p>
	</div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { call } from "../api";
import { notify } from "../utils/toast";
import { time } from "../utils/formatters";
import StatusBadge from "../components/StatusBadge.vue";

const rows = ref([]);
const loading = ref(false);
async function load() {
	loading.value = true;
	try {
		rows.value = await call("get_gate_entries", { days: 1 });
	} catch (e) {
		notify(e.message, "error");
	} finally {
		loading.value = false;
	}
}
let timer;
onMounted(() => {
	load();
	timer = setInterval(load, 15000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>
