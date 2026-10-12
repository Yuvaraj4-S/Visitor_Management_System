<template>
	<div class="space-y-4">
		<p v-if="loading" class="card text-center text-sm text-slate-400">Loading…</p>
		<template v-else-if="visitor">
			<VisitorSummary :visitor="visitor" />

			<div v-if="visitor.blocked_reason" class="card border border-rose-200 bg-rose-50 text-sm font-medium text-rose-700">
				Entry not allowed: {{ visitor.blocked_reason }}
			</div>
			<template v-else-if="visitor.next_action === 'checkout'">
				<router-link :to="`/checkout/${encodeURIComponent(name)}?scanned=${scanned ? 1 : 0}`" class="btn-primary w-full">Visitor is inside — go to Check-Out</router-link>
			</template>
			<template v-else>
				<p class="text-xs text-slate-500">
					{{ scanned ? "Identified by QR scan" : "Identified by Visitor Pass ID" }} — {{ name }}
					<router-link v-if="!scanned" to="/scan" class="ml-1 font-semibold underline">Scan instead</router-link>
				</p>
				<CameraCapture v-model="photo" label="Live gate photo (optional)" />
				<PhotoCompare :visitor="visitor" :live-photo="photo" />
				<div class="card space-y-3">
					<label class="flex items-center gap-3 text-sm"><input v-model="photoMatch" type="checkbox" class="h-5 w-5 accent-slate-900" />Visitor matches the pass photo</label>
				</div>
				<ItemChecklist v-model="verified" :items="visitor.items" />
				<div class="card">
					<label class="label">ID card number</label>
					<input v-model.trim="idCard" class="input" inputmode="numeric" placeholder="Number on the ID card handed over" />
				</div>
				<button class="btn-success w-full py-4 text-base" :disabled="!canConfirm || busy" @click="confirm">
					{{ busy ? "Checking in…" : "Confirm Check-In" }}
				</button>
			</template>
		</template>
	</div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { call } from "../api";
import { notify } from "../utils/toast";
import { useVisitors } from "../stores/visitors";
import VisitorSummary from "../components/VisitorSummary.vue";
import CameraCapture from "../components/CameraCapture.vue";
import ItemChecklist from "../components/ItemChecklist.vue";
import PhotoCompare from "../components/PhotoCompare.vue";

const props = defineProps({ name: { type: String, required: true } });
const route = useRoute();
const router = useRouter();
const store = useVisitors();
const visitor = ref(null);
const loading = ref(true);
const busy = ref(false);
const photo = ref("");
const idMatch = ref(false);
const photoMatch = ref(false);
const idCard = ref("");
const verified = ref([]);
const scanned = computed(() => route.query.scanned === "1");
// The visitor is identified either by QR scan or by the guard entering the Visitor
// Pass ID (a walk-in pass is minutes old and the visitor has no QR yet) — both reach
// this screen, so there is nothing further to require. Gate photo and the match
// tick are optional; `scanned` is still reported so the Security Log stays honest.
const canConfirm = computed(() => true);

onMounted(async () => {
	try {
		visitor.value = await call("get_visitor_for_checkin", { pass_name: props.name });
	} catch (e) {
		notify(e.message, "error");
	} finally {
		loading.value = false;
	}
});

async function confirm() {
	busy.value = true;
	try {
		await call("complete_checkin", {
			pass_name: props.name,
			gate_photo: photo.value,
			id_proof_match: idMatch.value ? 1 : 0,
			pass_photo_match: photoMatch.value ? 1 : 0,
			id_card_number: idCard.value,
			qr_scanned: scanned.value ? 1 : 0,
			verified_items: verified.value,
		});
		notify(`${visitor.value.visitor_full_name} checked in`, "success");
		store.load(true);
		router.push("/");
	} catch (e) {
		notify(e.message, "error", 6000);
	} finally {
		busy.value = false;
	}
}
</script>
