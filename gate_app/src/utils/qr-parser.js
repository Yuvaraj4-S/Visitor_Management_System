// Visitor Pass QR payload: PASS:<name>|VISITOR:<name>|VISIT_DATE:<date>
export function parseQR(text) {
	const out = {};
	String(text || "")
		.split("|")
		.forEach((part) => {
			const i = part.indexOf(":");
			if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
		});
	return out;
}
