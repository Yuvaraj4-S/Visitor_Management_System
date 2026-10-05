<template>
	<div class="space-y-4">
		<div class="card p-2">
			<div id="qr-reader" class="overflow-hidden rounded-xl"></div>
			<p v-if="cameraError" class="p-3 text-sm text-rose-600">{{ cameraError }}</p>
		</div>
		<form class="card space-y-2" @submit.prevent="lookup(manual, false)">
			<label class="label">Or enter the pass ID</label>
			<input v-model.trim="manual" class="input" placeholder="VP-2026-00001" autocapitalize="characters" />
			<button class="btn-primary w-full" :disabled="!manual || busy">Find pass</button>
			<p class="text-xs text-slate-400">Gate policy needs a QR scan to check a visitor in or out; a typed ID opens the pass details.</p>
		</form>
	</div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { Html5Qrcode } from "html5-qrcode";
import { call } from "../api";
import { notify } from "../utils/toast";

const router = useRouter();
const manual = ref("");
const busy = ref(false);
const cameraError = ref("");
let scanner = null;

async function lookup(value, scanned) {
	if (!value || busy.value) return;
	busy.value = true;
	try {
		const res = await call("resolve_qr", { qr_data: value });
		const info = await call("get_visitor_for_checkin", { pass_name: res.visitor_pass });
		const q = { scanned: scanned && res.scanned ? 1 : 0 };
		if (info.next_action === "checkout") router.push({ path: `/checkout/${encodeURIComponent(res.visitor_pass)}`, query: q });
		else router.push({ path: `/checkin/${encodeURIComponent(res.visitor_pass)}`, query: q });
	} catch (e) {
		notify(e.message, "error");
		busy.value = false;
	}
}

onMounted(async () => {
	try {
		scanner = new Html5Qrcode("qr-reader");
		await scanner.start(
			{ facingMode: "environment" },
			{ fps: 10, qrbox: { width: 240, height: 240 } },
			async (text) => {
				if (busy.value) return;
				try {
					await scanner.pause(true);
				} catch (e) {
					/* already paused */
				}
				await lookup(text, true);
				if (scanner && !busy.value) scanner.resume();
			},
			() => {}
		);
	} catch (e) {
		cameraError.value = "Camera not available. Allow camera access, or type the pass ID below.";
	}
});

onBeforeUnmount(async () => {
	if (scanner) {
		try {
			await scanner.stop();
		} catch (e) {
			/* not running */
		}
		scanner = null;
	}
});
</script>
