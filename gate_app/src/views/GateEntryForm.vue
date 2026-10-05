<template>
	<form class="space-y-4" @submit.prevent="submit">
		<div class="card space-y-3">
			<h2 class="font-bold">Visitor</h2>
			<div><label class="label">Full name *</label><input v-model.trim="f.visitor_full_name" class="input" required /></div>
			<div class="grid grid-cols-2 gap-3">
				<div><label class="label">Mobile *</label><input v-model.trim="f.mobile_number" class="input" inputmode="tel" required /></div>
				<div><label class="label">Visitors</label><input v-model.number="f.number_of_visitors" type="number" min="1" class="input" /></div>
			</div>
			<div><label class="label">Email</label><input v-model.trim="f.email_id" type="email" class="input" placeholder="optional" /></div>
			<div class="grid grid-cols-2 gap-3">
				<div>
					<label class="label">Visitor type *</label>
					<select v-model="f.visitor_type" class="input" required>
						<option v-for="t in visitorTypes" :key="t">{{ t }}</option>
					</select>
				</div>
				<div><label class="label">Company{{ needsCompany ? " *" : "" }}</label><input v-model.trim="f.visitor_company" class="input" :required="needsCompany" /></div>
			</div>
			<div class="grid grid-cols-2 gap-3">
				<div>
					<label class="label">ID proof *</label>
					<select v-model="f.id_proof_type" class="input" required>
						<option v-for="t in idTypes" :key="t">{{ t }}</option>
					</select>
				</div>
				<div><label class="label">ID number *</label><input v-model.trim="f.id_proof_number" class="input" required autocapitalize="characters" /></div>
			</div>
		</div>

		<CameraCapture v-model="photo" label="Visitor photo" required />
		<CameraCapture v-model="idScan" label="ID proof photo" required default-facing="environment" />

		<div class="card space-y-3">
			<h2 class="font-bold">Whom to visit</h2>
			<div class="grid grid-cols-2 gap-2">
				<button v-for="m in ['Single Person', 'Group']" :key="m" type="button" class="btn" :class="f.mapping_type === m ? 'bg-slate-900 text-white' : 'bg-slate-100'" @click="setMapping(m)">
					{{ m }}
				</button>
			</div>
			<div v-if="f.mapping_type === 'Single Person'" class="relative">
				<label class="label">Person to visit *</label>
				<input v-model="empQuery" class="input" placeholder="Search employee" @input="searchEmployees" />
				<p v-if="f.person_to_visit" class="mt-1 text-xs text-emerald-700">Selected: {{ empLabel }}</p>
				<div v-if="empResults.length" class="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-xl border border-slate-200 bg-white shadow">
					<button v-for="e in empResults" :key="e.name" type="button" class="block w-full px-3 py-2 text-left text-sm hover:bg-slate-50" @click="pickEmployee(e)">
						{{ e.employee_name }} <span class="text-slate-400">· {{ e.department || e.name }}</span>
					</button>
				</div>
			</div>
			<div v-else class="relative">
				<label class="label">Employee group *</label>
				<input v-model="groupQuery" class="input" placeholder="Search group" @input="searchGroups" />
				<p v-if="f.visitor_group" class="mt-1 text-xs text-emerald-700">Selected: {{ f.visitor_group }}</p>
				<div v-if="groupResults.length" class="absolute z-10 mt-1 max-h-56 w-full overflow-auto rounded-xl border border-slate-200 bg-white shadow">
					<button v-for="g in groupResults" :key="g" type="button" class="block w-full px-3 py-2 text-left text-sm hover:bg-slate-50" @click="pickGroup(g)">{{ g }}</button>
				</div>
			</div>
			<div><label class="label">Purpose *</label><textarea v-model.trim="f.purpose_of_visit" class="input" rows="2" required></textarea></div>
			<div><label class="label">Expected duration (hours)</label><input v-model.number="f.expected_duration" type="number" min="0.5" step="0.5" class="input" /></div>
		</div>

		<div class="card space-y-3">
			<div class="flex items-center justify-between">
				<h2 class="font-bold">Items carried</h2>
				<button type="button" class="btn bg-slate-100 text-sm" @click="addItem">+ Add item</button>
			</div>
			<p v-if="!items.length" class="text-sm text-slate-400">Laptop, tools, documents, samples… Tap "Add item" for each one.</p>
			<div v-for="(item, i) in items" :key="i" class="space-y-2 rounded-xl border border-slate-200 p-3">
				<div class="flex gap-2">
					<input v-model.trim="item.item_name" class="input flex-1" placeholder="Item name *" />
					<input v-model.number="item.quantity" type="number" min="1" class="input w-20" placeholder="Qty" />
					<button type="button" class="px-2 text-xl text-rose-600" aria-label="Remove item" @click="items.splice(i, 1)">×</button>
				</div>
				<div class="grid grid-cols-2 gap-2">
					<select v-model="item.item_category" class="input">
						<option value="">Category</option>
						<option v-for="c in itemCategories" :key="c">{{ c }}</option>
					</select>
					<input v-model.trim="item.serial_number" class="input" placeholder="Serial / asset no." />
				</div>
			</div>
		</div>

		<div class="card">
			<h2 class="mb-2 font-bold">Hospitality</h2>
			<div class="grid grid-cols-2 gap-2">
				<label v-for="h in hospitality" :key="h.field" class="flex items-center gap-2 text-sm">
					<input v-model="f[h.field]" type="checkbox" class="h-5 w-5 accent-slate-900" />{{ h.label }}
				</label>
			</div>
			<textarea v-model.trim="f.special_instructions" class="input mt-3" rows="2" placeholder="Special instructions"></textarea>
		</div>

		<button class="btn-primary w-full py-4 text-base" :disabled="busy">{{ busy ? "Sending…" : "Send for Approval" }}</button>
	</form>
</template>

<script setup>
import { computed, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { call } from "../api";
import { notify } from "../utils/toast";
import CameraCapture from "../components/CameraCapture.vue";

const router = useRouter();
const visitorTypes = ["Customer", "Supplier", "Contractor", "Candidate", "VIP"];
const idTypes = ["Aadhaar", "PAN Card", "Passport", "Driving License"];
const hospitality = [
	{ field: "meal_required", label: "Meal" },
	{ field: "cab_required", label: "Cab" },
	{ field: "hotel_required", label: "Hotel" },
	{ field: "factory_tour_required", label: "Factory tour" },
	{ field: "greeting_required", label: "Greeting" },
];
const f = reactive({
	visitor_full_name: "", mobile_number: "", email_id: "", visitor_type: "Customer", visitor_company: "",
	number_of_visitors: 1, id_proof_type: "Aadhaar", id_proof_number: "", mapping_type: "Single Person",
	person_to_visit: "", visitor_group: "", purpose_of_visit: "", expected_duration: 2, special_instructions: "",
	meal_required: false, cab_required: false, hotel_required: false, factory_tour_required: false, greeting_required: false,
});
const itemCategories = ["Electronics", "Tool", "Document / Sample / Gift / Perishable / Weapon / Other"];
const items = ref([]);
const photo = ref("");
const idScan = ref("");
const busy = ref(false);
const empQuery = ref("");
const empResults = ref([]);
const empLabel = ref("");
const groupQuery = ref("");
const groupResults = ref([]);
const needsCompany = computed(() => ["Contractor", "Supplier", "Customer"].includes(f.visitor_type));

let t1, t2;
function searchEmployees() {
	clearTimeout(t1);
	t1 = setTimeout(async () => (empResults.value = empQuery.value ? await call("search_employee", { query: empQuery.value }) : []), 250);
}
function searchGroups() {
	clearTimeout(t2);
	t2 = setTimeout(async () => (groupResults.value = await call("search_employee_group", { query: groupQuery.value })), 250);
}
function pickEmployee(e) {
	f.person_to_visit = e.name;
	empLabel.value = `${e.employee_name} (${e.name})`;
	empQuery.value = e.employee_name;
	empResults.value = [];
}
function pickGroup(g) {
	f.visitor_group = g;
	groupQuery.value = g;
	groupResults.value = [];
}
function addItem() {
	items.value.push({ item_name: "", quantity: 1, item_category: "", serial_number: "" });
}
function setMapping(m) {
	f.mapping_type = m;
	f.person_to_visit = "";
	f.visitor_group = "";
}

async function submit() {
	if (!photo.value || !idScan.value) return notify("Capture the visitor photo and the ID proof photo.", "error");
	if (f.mapping_type === "Single Person" && !f.person_to_visit) return notify("Pick the person to visit.", "error");
	if (f.mapping_type === "Group" && !f.visitor_group) return notify("Pick the employee group.", "error");
	if (items.value.some((item) => !item.item_name || !(item.quantity > 0))) return notify("Each item needs a name and a quantity — or remove the empty row.", "error");
	busy.value = true;
	try {
		const data = { ...f, items: items.value, visitor_photo_data: photo.value, id_proof_scan_data: idScan.value };
		["meal_required", "cab_required", "hotel_required", "factory_tour_required", "greeting_required"].forEach((k) => (data[k] = data[k] ? 1 : 0));
		const res = await call("create_gate_entry", { data });
		notify(`Request ${res.name} sent for approval`, "success");
		router.push("/requests");
	} catch (e) {
		notify(e.message, "error", 6000);
	} finally {
		busy.value = false;
	}
}
</script>
