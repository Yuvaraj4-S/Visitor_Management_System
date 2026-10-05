<template>
	<div class="card">
		<p class="label">{{ label }} <span v-if="required" class="text-rose-500">*</span></p>
		<div class="relative overflow-hidden rounded-xl bg-slate-900">
			<img v-if="modelValue" :src="modelValue" class="aspect-[4/3] w-full object-cover" alt="" />
			<video v-show="!modelValue && streaming" ref="video" class="aspect-[4/3] w-full object-cover" autoplay playsinline muted></video>
			<div v-if="!modelValue && !streaming" class="flex aspect-[4/3] items-center justify-center text-sm text-slate-400">
				{{ error || "Camera is off" }}
			</div>
		</div>
		<div class="mt-3 grid grid-cols-2 gap-2">
			<template v-if="modelValue">
				<button type="button" class="btn-ghost col-span-2" @click="retake">Retake</button>
			</template>
			<template v-else-if="streaming">
				<button type="button" class="btn-primary" @click="capture">Capture</button>
				<button type="button" class="btn-ghost" @click="flip">Flip camera</button>
			</template>
			<template v-else>
				<button type="button" class="btn-primary" @click="start">Open camera</button>
				<label class="btn-ghost cursor-pointer">
					Upload
					<input type="file" accept="image/*" :capture="facing === 'user' ? 'user' : 'environment'" class="hidden" @change="fromFile" />
				</label>
			</template>
		</div>
	</div>
</template>

<script setup>
import { onBeforeUnmount, ref } from "vue";

const props = defineProps({
	modelValue: { type: String, default: "" },
	label: { type: String, default: "Photo" },
	required: { type: Boolean, default: false },
	defaultFacing: { type: String, default: "user" },
});
const emit = defineEmits(["update:modelValue"]);
const video = ref(null);
const streaming = ref(false);
const error = ref("");
const facing = ref(props.defaultFacing);
let stream = null;

async function start() {
	error.value = "";
	try {
		stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: facing.value }, audio: false });
		streaming.value = true;
		video.value.srcObject = stream;
	} catch (e) {
		error.value = "Camera not available — allow camera access or use Upload.";
		stop();
	}
}

function stop() {
	if (stream) stream.getTracks().forEach((t) => t.stop());
	stream = null;
	streaming.value = false;
}

function capture() {
	const v = video.value;
	const canvas = document.createElement("canvas");
	const scale = Math.min(1, 1280 / (v.videoWidth || 1280));
	canvas.width = (v.videoWidth || 1280) * scale;
	canvas.height = (v.videoHeight || 960) * scale;
	canvas.getContext("2d").drawImage(v, 0, 0, canvas.width, canvas.height);
	emit("update:modelValue", canvas.toDataURL("image/jpeg", 0.8));
	stop();
}

function flip() {
	facing.value = facing.value === "user" ? "environment" : "user";
	stop();
	start();
}

function retake() {
	emit("update:modelValue", "");
	start();
}

function fromFile(e) {
	const file = e.target.files && e.target.files[0];
	if (!file) return;
	const img = new Image();
	img.onload = () => {
		const canvas = document.createElement("canvas");
		const scale = Math.min(1, 1280 / img.width);
		canvas.width = img.width * scale;
		canvas.height = img.height * scale;
		canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
		emit("update:modelValue", canvas.toDataURL("image/jpeg", 0.8));
		URL.revokeObjectURL(img.src);
	};
	img.src = URL.createObjectURL(file);
}

onBeforeUnmount(stop);
</script>
