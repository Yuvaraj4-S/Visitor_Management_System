<template>
	<span class="inline-flex rounded-full px-2 py-0.5 text-[11px] font-semibold" :class="STATUS_COLOURS[status] || 'bg-slate-100 text-slate-600'">
		{{ status }}
	</span>
</template>

<script setup>
import { STATUS_COLOURS } from "../utils/formatters";
defineProps({ status: { type: String, default: "" } });
</script>
