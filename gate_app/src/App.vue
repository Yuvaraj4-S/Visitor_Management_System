<template>
	<div class="mx-auto flex min-h-screen max-w-xl flex-col pb-24">
		<header class="sticky top-0 z-20 bg-slate-900 px-4 pb-3 pt-[max(0.75rem,env(safe-area-inset-top))] text-white">
			<div class="flex items-center justify-between">
				<div>
					<p class="text-xs uppercase tracking-widest text-slate-400">Visitor Management</p>
					<h1 class="text-lg font-bold">Security Gate</h1>
				</div>
				<div class="text-right text-xs text-slate-300">
					<p class="font-semibold text-white">{{ boot.user_fullname }}</p>
					<p :class="online ? 'text-emerald-400' : 'text-rose-400'">{{ online ? "Online" : "Offline" }}</p>
				</div>
			</div>
		</header>
		<main class="flex-1 px-4 py-4">
			<router-view />
		</main>
		<BottomNav />
		<transition name="fade">
			<div
				v-if="toast.visible"
				class="fixed inset-x-4 bottom-24 z-50 mx-auto max-w-md whitespace-pre-line rounded-xl px-4 py-3 text-sm font-medium text-white shadow-lg"
				:class="{ 'bg-emerald-600': toast.kind === 'success', 'bg-rose-600': toast.kind === 'error', 'bg-slate-800': toast.kind === 'info' }"
			>
				{{ toast.message }}
			</div>
		</transition>
	</div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import BottomNav from "./components/BottomNav.vue";
import { toast } from "./utils/toast";

const boot = window.gate_boot || {};
const online = ref(navigator.onLine);
const setOnline = () => (online.value = navigator.onLine);
onMounted(() => {
	window.addEventListener("online", setOnline);
	window.addEventListener("offline", setOnline);
});
onBeforeUnmount(() => {
	window.removeEventListener("online", setOnline);
	window.removeEventListener("offline", setOnline);
});
</script>

<style>
.fade-enter-active,
.fade-leave-active {
	transition: opacity 0.2s;
}
.fade-enter-from,
.fade-leave-to {
	opacity: 0;
}
</style>
