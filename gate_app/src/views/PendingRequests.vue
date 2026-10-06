<template>
	<div class="space-y-3">
		<div class="flex items-center justify-between">
			<h2 class="font-bold">Walk-in requests (today)</h2>
			<button class="text-xs font-semibold text-slate-500" @click="load">{{ loading ? "Loading…" : "Refresh" }}</button>
		</div>
		<div v-for="r in rows" :key="r.name" class="card space-y-1">
			<div class="flex items-center justify-between">
				<p class="font-semibold">{{ r.visitor_full_name }}</p>
				<StatusBadge :status="r.status" />
			</div>
			<p class="text-xs text-slate-500">{{ r.name }} · {{ r.visitor_type }} · {{ r.host_name }}</p>
			<p class="text-xs font-medium text-slate-700">
				{{ date(r.visit_date) }} · {{ time(r.expected_checkin) }}–{{ time(r.expected_checkout) }}
			</p>
			<ul v-if="r.additional_visitors && r.additional_visitors.length" class="space-y-0.5 text-xs text-slate-600">
				<li v-for="x in r.additional_visitors" :key="x.visitor_full_name">
					+ {{ x.visitor_full_name }} · {{ time(x.expected_checkin) }}–{{ time(x.expected_checkout) }}
				</li>
			</ul>
			<p class="text-xs text-slate-400">Sent {{ time(r.creation) }}</p>
			<p v-if="r.status === 'Rejected'" class="text-sm text-rose-600">Rejected: {{ r.rejection_reason || "—" }}</p>
			<router-link v-if="r.visitor_pass" :to="`/checkin/${encodeURIComponent(r.visitor_pass)}`" class="btn-success mt-2 w-full">
				Approved — open pass {{ r.visitor_pass }}
			</router-link>
			<router-link
				v-for="x in (r.additional_visitors || []).filter((x) => x.visitor_pass)"
				:key="x.visitor_pass"
				:to="`/checkin/${encodeURIComponent(x.visitor_pass)}`"
				class="btn mt-1 w-full bg-emerald-50 text-emerald-700"
			>
				{{ x.visitor_full_name }} — pass {{ x.visitor_pass }}
			</router-link>
		</div>
		<p v-if="!loading && !rows.length" class="card text-center text-sm text-slate-400">No walk-in requests today.</p>
	</div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { call } from "../api";
import { notify } from "../utils/toast";
import { date, time } from "../utils/formatters";
import StatusBadge from "../components/StatusBadge.vue";

const rows = ref([]);
const loading = ref(false);
async function load() {
	loading.value = true;
	try {
		rows.value = await call("get_walk_in_requests", { days: 1 });
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
