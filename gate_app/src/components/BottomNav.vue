<template>
	<nav class="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white pb-[env(safe-area-inset-bottom)]">
		<div class="mx-auto grid max-w-xl grid-cols-5">
			<router-link
				v-for="item in items"
				:key="item.to"
				:to="item.to"
				class="flex flex-col items-center gap-0.5 py-2 text-[11px] font-medium"
				:class="isActive(item) ? 'text-slate-900' : 'text-slate-400'"
			>
				<span
					class="flex h-9 w-9 items-center justify-center rounded-full text-lg"
					:class="item.primary ? 'bg-slate-900 text-white' : ''"
					>{{ item.icon }}</span
				>
				{{ item.label }}
			</router-link>
		</div>
	</nav>
</template>

<script setup>
import { useRoute } from "vue-router";

const route = useRoute();
const items = [
	{ to: "/", label: "Home", icon: "⌂" },
	{ to: "/visitors", label: "Visitors", icon: "☰" },
	{ to: "/scan", label: "Scan", icon: "⌗", primary: true },
	{ to: "/new-entry", label: "Walk-in", icon: "+" },
	{ to: "/requests", label: "Requests", icon: "⏱" },
];
const isActive = (item) => (item.to === "/" ? route.path === "/" : route.path.startsWith(item.to));
</script>
