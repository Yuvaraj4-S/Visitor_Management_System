<template>
	<div v-if="items.length" class="card">
		<p class="label">Items declared ({{ modelValue.length }}/{{ items.length }} verified)</p>
		<label v-for="item in items" :key="item.name" class="flex items-center gap-3 border-b border-slate-100 py-2 last:border-0">
			<input type="checkbox" class="h-5 w-5 accent-slate-900" :checked="modelValue.includes(item.name)" @change="toggle(item.name)" />
			<span class="flex-1 text-sm">
				{{ item.item_name }}
				<span class="text-slate-400">× {{ item.quantity || 1 }}</span>
				<span v-if="item.serial_number" class="block text-xs text-slate-400">S/N {{ item.serial_number }}</span>
			</span>
		</label>
	</div>
</template>

<script setup>
const props = defineProps({ items: { type: Array, default: () => [] }, modelValue: { type: Array, default: () => [] } });
const emit = defineEmits(["update:modelValue"]);
function toggle(name) {
	const set = new Set(props.modelValue);
	set.has(name) ? set.delete(name) : set.add(name);
	emit("update:modelValue", [...set]);
}
</script>
