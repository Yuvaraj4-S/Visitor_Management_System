<template>
	<div class="space-y-3">
		<div class="grid grid-cols-3 gap-1 rounded-xl bg-white p-1">
			<button v-for="t in tabs" :key="t.key" class="rounded-lg py-2 text-sm font-semibold" :class="tab === t.key ? 'bg-slate-900 text-white' : 'text-slate-500'" @click="tab = t.key">
				{{ t.label }} ({{ (lists[t.key] || []).length }})
			</button>
		</div>
		<input v-model.trim="q" class="input" placeholder="Search name, company, host, pass" />
		<div class="space-y-2">
			<VisitorCard v-for="v in filtered" :key="v.name" :visitor="v" @open="open" />
			<p v-if="!filtered.length" class="card text-center text-sm text-slate-400">{{ store.loading ? "Loading…" : "Nobody here." }}</p>
		</div>
	</div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import VisitorCard from "../components/VisitorCard.vue";
import { useVisitors } from "../stores/visitors";

const store = useVisitors();
const route = useRoute();
const router = useRouter();
const tabs = [
	{ key: "expected", label: "Expected" },
	{ key: "inside", label: "Inside" },
	{ key: "left", label: "Left" },
];
const tab = ref(route.query.tab || "expected");
const q = ref("");
const lists = computed(() => store.dashboard || {});
const filtered = computed(() => {
	const rows = lists.value[tab.value] || [];
	const s = q.value.toLowerCase();
	return s
		? rows.filter((r) => [r.visitor_full_name, r.company__organisation, r.host_name, r.name].some((x) => (x || "").toLowerCase().includes(s)))
		: rows;
});
function open(v) {
	router.push(v.status === "Checked-In" ? `/checkout/${encodeURIComponent(v.name)}` : `/checkin/${encodeURIComponent(v.name)}`);
}
onMounted(() => store.load(true));
</script>
