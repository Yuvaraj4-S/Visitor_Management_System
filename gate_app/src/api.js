const PREFIX = "visitormanagement.visitor_management.api.gate_pwa.";

function serverMessage(data) {
	try {
		if (data && data._server_messages) {
			return JSON.parse(data._server_messages)
				.map((m) => {
					const parsed = typeof m === "string" ? JSON.parse(m) : m;
					return parsed.message || parsed;
				})
				.join("\n")
				.replace(/<[^>]+>/g, "");
		}
	} catch (e) {
		/* fall through */
	}
	if (data && data.exception) return String(data.exception).split(":").slice(1).join(":").trim() || data.exception;
	return null;
}

export async function call(method, args = {}) {
	const res = await fetch(`/api/method/${method.includes(".") ? method : PREFIX + method}`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			Accept: "application/json",
			"X-Frappe-CSRF-Token": window.csrf_token || "",
		},
		body: JSON.stringify(args),
	});
	let data = {};
	try {
		data = await res.json();
	} catch (e) {
		/* non-JSON error page */
	}
	if (res.status === 403 && !data._server_messages) {
		window.location.href = "/login?redirect-to=/gate";
	}
	if (!res.ok) throw new Error(serverMessage(data) || `${res.status} ${res.statusText}`);
	return data.message;
}
