<template>
	<form class="space-y-4" @submit.prevent="submit">
		<!-- Common to everyone in this walk-in: type, whom to visit, date -->
		<div class="card space-y-3">
			<h2 class="font-bold">Visit details <span class="text-xs font-normal text-slate-400">(common for all visitors)</span></h2>
			<div>
				<label class="label">Visitor type *</label>
				<select v-model="common.visitor_type" class="input" required>
					<option value="" disabled>Select</option>
					<option v-for="t in visitorTypes" :key="t">{{ t }}</option>
				</select>
			</div>
			<div class="relative">
				<label class="label">Person to visit *</label>
				<input v-model="empQuery" class="input" placeholder="Search employee" @input="searchEmployees" />
				<p v-if="common.person_to_visit" class="mt-1 text-xs text-emerald-700">
					Selected: {{ empLabel }}<template v-if="empDept"> · Department: {{ empDept }}</template>
				</p>
				<div v-if="empResults.length" class="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-xl border border-slate-200 bg-white shadow">
					<button v-for="e in empResults" :key="e.name" type="button" class="block w-full px-3 py-2 text-left text-sm hover:bg-slate-50" @click="pickEmployee(e)">
						{{ e.employee_name }} <span class="text-slate-400">· {{ e.department || e.name }}</span>
					</button>
				</div>
			</div>
			<div><label class="label">Purpose *</label><textarea v-model.trim="common.purpose_of_visit" class="input" rows="2" required></textarea></div>
			<div><label class="label">Visit date *</label><input v-model="common.visit_date" type="date" :min="todayStr" class="input" required /></div>
		</div>

		<!-- One card per visitor: everything that can differ per person -->
		<div v-for="(v, i) in visitors" :key="v.key" class="card space-y-3">
			<div class="flex items-center justify-between">
				<h2 class="font-bold">Visitor {{ i + 1 }}<span v-if="i === 0" class="text-xs font-normal text-slate-400"> (main)</span></h2>
				<button v-if="i > 0" type="button" class="px-2 text-xl text-rose-600" aria-label="Remove visitor" @click="removeVisitor(i)">×</button>
			</div>
			<div><label class="label">Full name *</label><input v-model.trim="v.visitor_full_name" class="input" required /></div>
			<div class="grid grid-cols-2 gap-3">
				<div>
					<label class="label">Mobile{{ i === 0 ? " *" : "" }}</label>
					<input v-model.trim="v.mobile_number" class="input" inputmode="tel" :required="i === 0" :placeholder="i === 0 ? '' : 'optional'" />
				</div>
				<div><label class="label">Email</label><input v-model.trim="v.email_id" type="email" class="input" placeholder="optional" /></div>
			</div>
			<div class="grid grid-cols-2 gap-3">
				<div><label class="label">Company</label><input v-model.trim="v.company__organisation" class="input" placeholder="optional" /></div>
				<div><label class="label">Vehicle number</label><input v-model.trim="v.vehicle_number" class="input" placeholder="optional" autocapitalize="characters" /></div>
			</div>
			<div class="grid grid-cols-2 gap-3">
				<div>
					<label class="label">ID proof</label>
					<select v-model="v.id_proof_type" class="input">
						<option value="">Select</option>
						<option v-for="t in idTypes" :key="t">{{ t }}</option>
					</select>
				</div>
				<div><label class="label">ID number</label><input v-model.trim="v.id_proof_number" class="input" autocapitalize="characters" /></div>
			</div>
			<div class="grid grid-cols-2 gap-3">
				<div><label class="label">Check-in *</label><input v-model="v.expected_checkin" type="time" class="input" required /></div>
				<div><label class="label">Check-out *</label><input v-model="v.expected_checkout" type="time" class="input" required /></div>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<span class="text-xs text-slate-500">Stay:</span>
				<button v-for="h in [1, 2, 4, 8]" :key="h" type="button" class="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold" @click="setDuration(v, h)">{{ h }}h</button>
			</div>

			<CameraCapture v-model="v.photo" label="Photo" />
			<CameraCapture v-model="v.idScan" label="ID proof photo" default-facing="environment" />

			<div class="space-y-2">
				<div class="flex items-center justify-between">
					<p class="label !mb-0">Items carried</p>
					<button type="button" class="btn bg-slate-100 text-sm" @click="addItem(v)">+ Add item</button>
				</div>
				<p v-if="!v.items.length" class="text-xs text-slate-400">Laptop, tools, documents… optional.</p>
				<div v-for="(item, j) in v.items" :key="j" class="space-y-2 rounded-xl border border-slate-200 p-3">
					<div class="flex gap-2">
						<input v-model.trim="item.item_name" class="input flex-1" placeholder="Item name *" />
						<input v-model.number="item.quantity" type="number" min="1" class="input w-20" placeholder="Qty" />
						<button type="button" class="px-2 text-xl text-rose-600" aria-label="Remove item" @click="v.items.splice(j, 1)">×</button>
					</div>
					<input v-model.trim="item.serial_number" class="input" placeholder="Serial / asset no. (optional)" />
				</div>
			</div>
		</div>

		<button type="button" class="btn w-full border-2 border-dashed border-slate-300 bg-white py-3 text-sm font-semibold text-slate-600" @click="addVisitor">
			+ Add visitor
		</button>
		<p class="text-center text-xs text-slate-400">
			{{ visitors.length }} visitor{{ visitors.length > 1 ? "s" : "" }} · the host approves once and each visitor gets their own pass.
		</p>

		<button class="btn-primary w-full py-4 text-base" :disabled="busy">{{ busy ? "Sending…" : "Send to Host for Approval" }}</button>
	</form>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { call } from "../api";
import { notify } from "../utils/toast";
import CameraCapture from "../components/CameraCapture.vue";

const router = useRouter();
const visitorTypes = ref([]);
const idTypes = ["Aadhaar", "PAN Card", "Passport", "Driving License"];

const pad = (n) => String(n).padStart(2, "0");
const hhmm = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
const now = new Date();
const todayStr = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;

// Common to every visitor in this request.
const common = reactive({ visitor_type: "", person_to_visit: "", purpose_of_visit: "", visit_date: todayStr });

// Per-visitor details. Visitor 1 is the request's main visitor; the rest become Additional Visitors.
let visitorKey = 0;
function newVisitor(from) {
	const v = {
		key: ++visitorKey, visitor_full_name: "", mobile_number: "", email_id: "", company__organisation: "",
		vehicle_number: "", id_proof_type: "", id_proof_number: "",
		expected_checkin: from ? from.expected_checkin : hhmm(now), expected_checkout: from ? from.expected_checkout : "",
		photo: "", idScan: "", items: [],
	};
	if (!from) setDuration(v, 2);
	return v;
}
const visitors = ref([newVisitor()]);

const busy = ref(false);
const empQuery = ref("");
const empResults = ref([]);
const empLabel = ref("");
const empDept = ref("");

// Check-out = check-in + hours, capped at 23:59 (a walk-in visit ends the same day).
function setDuration(v, hours) {
	const [h, m] = (v.expected_checkin || hhmm(new Date())).split(":").map(Number);
	const mins = Math.min(h * 60 + m + hours * 60, 23 * 60 + 59);
	v.expected_checkout = `${pad(Math.floor(mins / 60))}:${pad(mins % 60)}`;
}

onMounted(async () => {
	try {
		visitorTypes.value = await call("get_visitor_types");
	} catch (e) {
		notify(e.message, "error");
	}
});

let t1;
function searchEmployees() {
	clearTimeout(t1);
	common.person_to_visit = "";
	t1 = setTimeout(async () => (empResults.value = empQuery.value ? await call("search_employee", { query: empQuery.value }) : []), 250);
}
function pickEmployee(e) {
	common.person_to_visit = e.name;
	empLabel.value = `${e.employee_name} (${e.name})`;
	empDept.value = e.department || "";
	empQuery.value = e.employee_name;
	empResults.value = [];
}
function addVisitor() {
	// A new visitor starts with Visitor 1's times — change them if they stay a different window.
	visitors.value.push(newVisitor(visitors.value[0]));
}
function removeVisitor(i) {
	visitors.value.splice(i, 1);
}
function addItem(v) {
	v.items.push({ item_name: "", quantity: 1, serial_number: "" });
}

function validate() {
	if (!common.person_to_visit) return "Pick the person to visit.";
	for (const [i, v] of visitors.value.entries()) {
		const who = v.visitor_full_name || `Visitor ${i + 1}`;
		if (!v.visitor_full_name) return `Visitor ${i + 1} needs a name — or remove that visitor.`;
		if (!v.expected_checkin || !v.expected_checkout || v.expected_checkout <= v.expected_checkin)
			return `${who}: check-out must be after check-in.`;
		if (v.items.some((item) => !item.item_name || !(item.quantity > 0)))
			return `${who}: each item needs a name and a quantity — or remove the empty row.`;
	}
	return null;
}

async function submit() {
	const error = validate();
	if (error) return notify(error, "error");
	busy.value = true;
	try {
		const [main, ...others] = visitors.value;
		const data = {
			...common,
			visitor_full_name: main.visitor_full_name,
			mobile_number: main.mobile_number,
			email_id: main.email_id,
			company__organisation: main.company__organisation,
			vehicle_number: main.vehicle_number,
			id_proof_type: main.id_proof_type,
			id_proof_number: main.id_proof_number,
			expected_checkin: main.expected_checkin,
			expected_checkout: main.expected_checkout,
			number_of_visitors: visitors.value.length,
			items: main.items,
			visitor_photo_data: main.photo,
			id_proof_scan_data: main.idScan,
			additional_visitors: others.map((v) => ({
				visitor_full_name: v.visitor_full_name,
				mobile_number: v.mobile_number,
				email_id: v.email_id,
				company__organisation: v.company__organisation,
				vehicle_number: v.vehicle_number,
				id_proof_type: v.id_proof_type,
				id_proof_number: v.id_proof_number,
				expected_checkin: v.expected_checkin,
				expected_checkout: v.expected_checkout,
				items: v.items,
				visitor_photo_data: v.photo,
				id_proof_scan_data: v.idScan,
			})),
		};
		const res = await call("create_walk_in_request", { data });
		notify(`Request ${res.name} sent to the host`, "success");
		router.push("/requests");
	} catch (e) {
		notify(e.message, "error", 6000);
	} finally {
		busy.value = false;
	}
}
</script>
