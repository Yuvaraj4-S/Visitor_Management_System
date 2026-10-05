export function time(value) {
	if (!value) return "";
	const s = String(value);
	const t = s.includes(" ") ? s.split(" ")[1] : s;
	return t.slice(0, 5);
}

export function date(value) {
	if (!value) return "";
	const d = new Date(String(value).replace(" ", "T"));
	return isNaN(d) ? String(value) : d.toLocaleDateString(undefined, { day: "2-digit", month: "short" });
}

export function initials(name) {
	return (name || "?")
		.split(" ")
		.filter(Boolean)
		.slice(0, 2)
		.map((p) => p[0].toUpperCase())
		.join("");
}

export const STATUS_COLOURS = {
	Approved: "bg-sky-100 text-sky-700",
	"Items Verified": "bg-sky-100 text-sky-700",
	"Checked-In": "bg-emerald-100 text-emerald-700",
	"Checked-Out": "bg-slate-200 text-slate-600",
	"Pending Approval": "bg-amber-100 text-amber-700",
	Rejected: "bg-rose-100 text-rose-700",
	Expired: "bg-slate-200 text-slate-600",
	Draft: "bg-slate-100 text-slate-600",
};
