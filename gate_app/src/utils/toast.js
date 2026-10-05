import { reactive } from "vue";

export const toast = reactive({ message: "", kind: "info", visible: false, _t: null });

export function notify(message, kind = "info", ms = 3500) {
	toast.message = message;
	toast.kind = kind;
	toast.visible = true;
	clearTimeout(toast._t);
	toast._t = setTimeout(() => (toast.visible = false), ms);
}
