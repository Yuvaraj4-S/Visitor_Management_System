<template>
	<div class="space-y-4">
		<p v-if="loading" class="card text-center text-sm text-slate-400">Loading…</p>
		<template v-else-if="visitor">
			<VisitorSummary :visitor="visitor" />
			<div v-if="visitor.next_action !== 'checkout'" class="card text-sm text-slate-600">
				This visitor is not checked in ({{ visitor.status }}).
			</div>
			<template v-else>
				<p class="text-xs text-slate-500">
					{{ scanned ? "Identified by QR scan" : "Identified by Visitor Pass ID" }} — {{ name }}
					<router-link v-if="!scanned" to="/scan" class="ml-1 font-semibold underline">Scan instead</router-link>
				</p>
				<CameraCapture v-model="photo" label="Exit photo (optional)" />
				<PhotoCompare :visitor="visitor" :live-photo="photo" />
				<div class="card space-y-3">
					<label class="flex items-center gap-3 text-sm"><input v-model="photoMatch" type="checkbox" class="h-5 w-5 accent-slate-900" />Visitor matches the pass photo</label>
				</div>
				<div class="card space-y-1">
					<p class="label">ID card to collect</p>
					<p class="text-lg font-bold">{{ visitor.id_card_number || "—" }}</p>
					<label class="flex items-center gap-3 text-sm"><input v-model="cardReturned" type="checkbox" class="h-5 w-5 accent-slate-900" />ID card returned</label>
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
const cardReturned = ref(false);
const scanned = computed(() => route.query.scanned === "1");
// The visitor is identified either by QR scan or by the guard entering the Visitor
// Pass ID (a walk-in pass is minutes old and the visitor has no QR yet) — both reach
// this screen, so there is nothing further to require. Exit photo and the match
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
		await call("complete_checkout", {
			pass_name: props.name,
			gate_photo: photo.value,
			id_proof_match: idMatch.value ? 1 : 0,
			pass_photo_match: photoMatch.value ? 1 : 0,
			id_card_returned: cardReturned.value ? 1 : 0,
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
