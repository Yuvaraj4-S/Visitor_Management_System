<template>
	<div class="space-y-4">
		<div class="grid grid-cols-2 gap-3">
			<div v-for="s in stats" :key="s.label" class="card" :class="s.alert ? 'ring-2 ring-rose-400' : ''" @click="s.to && $router.push(s.to)">
				<p class="text-3xl font-bold" :class="s.alert ? 'text-rose-600' : ''">{{ s.value }}</p>
				<p class="text-xs font-semibold uppercase tracking-wide text-slate-500">{{ s.label }}</p>
			</div>
		</div>

		<router-link to="/scan" class="btn-primary w-full py-5 text-base">⌗ Scan Visitor QR</router-link>

		<p v-if="store.error" class="card text-sm text-rose-600">{{ store.error }}</p>

		<section>
			<div class="mb-2 flex items-center justify-between">
				<h2 class="font-bold">Expected today</h2>
				<button class="text-xs font-semibold text-slate-500" @click="store.load(true)">{{ store.loading ? "Loading…" : "Refresh" }}</button>
			</div>
			<div class="space-y-2">
				<VisitorCard v-for="v in expected" :key="v.name" :visitor="v" @open="open" />
				<p v-if="!store.loading && !expected.length" class="card text-center text-sm text-slate-400">No more visitors expected today.</p>
			</div>
		</section>

		<section v-if="legend.length" class="card">
			<p class="label">Badge colours</p>
			<div class="flex flex-wrap gap-2">
				<span v-for="l in legend" :key="l.visitor_type" class="flex items-center gap-1.5 text-xs">
					<span class="h-3 w-3 rounded-full" :style="{ background: l.hex }"></span>{{ l.visitor_type }}
				</span>
			</div>
		</section>
	</div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted } from "vue";
import { useRouter } from "vue-router";
import VisitorCard from "../components/VisitorCard.vue";
import { useVisitors } from "../stores/visitors";

const store = useVisitors();
const router = useRouter();
const d = computed(() => store.dashboard || { stats: {}, expected: [], badge_legend: [] });
const expected = computed(() => d.value.expected || []);
const legend = computed(() => d.value.badge_legend || []);
const stats = computed(() => [
	{ label: "Expected", value: d.value.stats.expected ?? "–", to: "/visitors" },
	{ label: "Inside now", value: d.value.stats.inside ?? "–", to: "/visitors?tab=inside" },
	{ label: "Pending requests", value: d.value.stats.pending_requests ?? "–", to: "/requests" },
	{ label: "VIP today", value: d.value.stats.vip_today ?? "–" },
	{
		label: "Time over (inside)",
		value: d.value.stats.time_over_inside ?? "–",
		to: "/visitors?tab=time_over",
		alert: (d.value.stats.time_over_inside || 0) > 0,
	},
]);

function open(v) {
	router.push(v.status === "Checked-In" ? `/checkout/${encodeURIComponent(v.name)}` : `/checkin/${encodeURIComponent(v.name)}`);
}

let timer;
onMounted(() => {
	store.load(true);
	timer = setInterval(() => store.load(true), 30000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>
