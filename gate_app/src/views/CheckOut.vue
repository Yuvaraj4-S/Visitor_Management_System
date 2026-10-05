<template>
	<div class="space-y-4">
		<p v-if="loading" class="card text-center text-sm text-slate-400">Loading…</p>
		<template v-else-if="visitor">
			<VisitorSummary :visitor="visitor" />
			<div v-if="visitor.next_action !== 'checkout'" class="card text-sm text-slate-600">
				This visitor is not checked in ({{ visitor.status }}).
			</div>
			<template v-else>
				<div v-if="!scanned" class="card border border-amber-200 bg-amber-50 text-sm text-amber-800">
					Scan the visitor's QR code to check them out (gate policy).
					<router-link to="/scan" class="mt-2 block font-semibold underline">Open scanner</router-link>
				</div>
				<CameraCapture v-model="photo" label="Exit photo" required />
				<div class="card space-y-3">
					<label class="flex items-center gap-3 text-sm"><input v-model="idMatch" type="checkbox" class="h-5 w-5 accent-slate-900" />Visitor matches the ID proof</label>
					<label class="flex items-center gap-3 text-sm"><input v-model="photoMatch" type="checkbox" class="h-5 w-5 accent-slate-900" />Visitor matches the pass photo</label>
				</div>
				<button class="btn-danger w-full py-4 text-base" :disabled="!canConfirm || busy" @click="confirm">
					{{ busy ? "Checking out…" : "Confirm Check-Out" }}
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
		await call("complete_checkout", {
			pass_name: props.name,
			gate_photo: photo.value,
			id_proof_match: idMatch.value ? 1 : 0,
			pass_photo_match: photoMatch.value ? 1 : 0,
			qr_scanned: scanned.value ? 1 : 0,
		});
		notify(`${visitor.value.visitor_full_name} checked out`, "success");
		store.load(true);
		router.push("/");
	} catch (e) {
		notify(e.message, "error", 6000);
	} finally {
		busy.value = false;
	}
}
</script>
