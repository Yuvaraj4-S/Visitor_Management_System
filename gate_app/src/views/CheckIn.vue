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
				<div v-if="!scanned" class="card border border-amber-200 bg-amber-50 text-sm text-amber-800">
					Scan the visitor's QR code to check them in (gate policy).
					<router-link to="/scan" class="mt-2 block font-semibold underline">Open scanner</router-link>
				</div>
				<CameraCapture v-model="photo" label="Live gate photo" required />
				<PhotoCompare :visitor="visitor" :live-photo="photo" />
				<div class="card space-y-3">
					<label class="flex items-center gap-3 text-sm"><input v-model="idMatch" type="checkbox" class="h-5 w-5 accent-slate-900" />Visitor matches the ID proof</label>
					<label class="flex items-center gap-3 text-sm"><input v-model="photoMatch" type="checkbox" class="h-5 w-5 accent-slate-900" />Visitor matches the pass photo</label>
				</div>
				<ItemChecklist v-model="verified" :items="visitor.items" />
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
const verified = ref([]);
const scanned = computed(() => route.query.scanned === "1");
const canConfirm = computed(() => scanned.value && photo.value && idMatch.value && photoMatch.value);

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
