<template>
	<div class="card">
		<p class="label mb-2">Compare before confirming</p>
		<div class="grid grid-cols-3 gap-2">
			<button v-for="p in photos" :key="p.label" type="button" class="text-left" :disabled="!p.src" @click="open = p">
				<div class="flex aspect-[3/4] items-center justify-center overflow-hidden rounded-xl bg-slate-100">
					<img v-if="p.src" :src="p.src" class="h-full w-full object-cover" alt="" />
					<span v-else class="px-1 text-center text-[11px] text-slate-400">{{ p.empty }}</span>
				</div>
				<p class="mt-1 text-[11px] font-semibold text-slate-600">{{ p.label }}</p>
			</button>
		</div>
		<p class="mt-2 text-xs text-slate-500">{{ visitor.id_proof_type }} · {{ visitor.id_proof_number || "—" }} — tap a photo to enlarge.</p>

		<div v-if="open" class="fixed inset-0 z-50 flex flex-col bg-black/90 p-4" @click="open = null">
			<p class="mb-2 text-sm font-semibold text-white">{{ open.label }}</p>
			<img :src="open.src" class="min-h-0 flex-1 object-contain" alt="" />
			<p class="mt-2 text-center text-xs text-slate-300">Tap anywhere to close</p>
		</div>
	</div>
</template>

<script setup>
import { computed, ref } from "vue";

const props = defineProps({
	visitor: { type: Object, required: true },
	livePhoto: { type: String, default: "" },
});
const open = ref(null);
const photos = computed(() => [
	{ label: "Registered photo", src: props.visitor.visitor_photo, empty: "No photo on the pass" },
	{ label: "ID proof", src: props.visitor.id_proof_scan, empty: "No ID scan on the pass" },
	{ label: "Live photo", src: props.livePhoto, empty: "Capture below" },
]);
</script>
