<template>
	<button class="card flex w-full items-center gap-3 text-left" @click="$emit('open', visitor)">
		<div class="relative">
			<img v-if="visitor.visitor_photo" :src="visitor.visitor_photo" class="h-12 w-12 rounded-full object-cover" alt="" />
			<div v-else class="flex h-12 w-12 items-center justify-center rounded-full bg-slate-200 font-bold text-slate-600">
				{{ initials(visitor.visitor_full_name) }}
			</div>
			<span class="absolute -bottom-0.5 -right-0.5 h-4 w-4 rounded-full border-2 border-white" :style="{ background: visitor.badge_hex }"></span>
		</div>
		<div class="min-w-0 flex-1">
			<p class="truncate font-semibold">{{ visitor.visitor_full_name }}</p>
			<p class="truncate text-xs text-slate-500">
				{{ visitor.visitor_type }} · {{ visitor.company__organisation || "—" }} · {{ visitor.host_name }}
			</p>
			<p class="text-xs text-slate-400">
				<template v-if="visitor.status === 'Checked-In'">In since {{ time(visitor.actual_checkin) }}</template>
				<template v-else-if="visitor.status === 'Checked-Out' && visitor.actual_checkout">Left {{ time(visitor.actual_checkout) }}</template>
				<template v-else>Expected {{ time(visitor.expected_checkin) }}–{{ time(visitor.expected_checkout) }}</template>
				<span v-if="visitor.request_channel === 'Walk-In'" class="ml-1 rounded bg-indigo-100 px-1 text-indigo-700">walk-in</span>
			</p>
		</div>
		<StatusBadge :status="visitor.status" />
	</button>
</template>

<script setup>
import StatusBadge from "./StatusBadge.vue";
import { initials, time } from "../utils/formatters";
defineProps({ visitor: { type: Object, required: true } });
defineEmits(["open"]);
</script>
