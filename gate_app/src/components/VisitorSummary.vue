<template>
	<div class="card overflow-hidden p-0">
		<div class="h-2" :style="{ background: visitor.badge_hex }"></div>
		<div class="flex gap-4 p-4">
			<img v-if="visitor.visitor_photo" :src="visitor.visitor_photo" class="h-24 w-20 rounded-xl object-cover" alt="" />
			<div class="min-w-0 flex-1">
				<p class="text-lg font-bold leading-tight">{{ visitor.visitor_full_name }}</p>
				<p class="text-sm text-slate-500">{{ visitor.visitor_type }} · {{ visitor.company__organisation || "—" }}</p>
				<p class="mt-1 text-xs text-slate-500">{{ visitor.mobile_number }}</p>
				<div class="mt-2 flex flex-wrap gap-1">
					<StatusBadge :status="visitor.status" />
					<span v-if="visitor.badge_number" class="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] font-semibold">{{ visitor.badge_number }}</span>
					<span class="rounded-full px-2 py-0.5 text-[11px] font-semibold text-white" :style="{ background: visitor.badge_hex }">{{ visitor.badge_colour || "Badge" }}</span>
				</div>
			</div>
		</div>
		<dl class="grid grid-cols-2 gap-x-4 gap-y-2 border-t border-slate-100 p-4 text-sm">
			<div><dt class="label">Host</dt><dd>{{ visitor.host_name }}</dd></div>
			<div><dt class="label">Pass</dt><dd>{{ visitor.name }}</dd></div>
			<div><dt class="label">ID Proof</dt><dd>{{ visitor.id_proof_type }} · {{ visitor.id_proof_number }}</dd></div>
			<div>
				<dt class="label">Validity</dt>
				<dd>
					{{ date(visitor.visit_date) }}<template v-if="visitor.multi_day_pass && visitor.pass_valid_until"> – {{ date(visitor.pass_valid_until) }} (daily)</template>
					· {{ time(visitor.expected_checkin) }}–{{ time(visitor.expected_checkout) }}
				</dd>
			</div>
			<div class="col-span-2"><dt class="label">Purpose</dt><dd>{{ visitor.purpose_of_visit }}</dd></div>
			<div v-if="visitor.items_carried" class="col-span-2">
				<dt class="label">Items carried</dt><dd class="whitespace-pre-line">{{ visitor.items_carried }}</dd>
			</div>
		</dl>
	</div>
</template>

<script setup>
import StatusBadge from "./StatusBadge.vue";
import { date, time } from "../utils/formatters";
defineProps({ visitor: { type: Object, required: true } });
</script>
