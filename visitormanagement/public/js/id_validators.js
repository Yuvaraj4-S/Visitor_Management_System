/*
 * Reusable Indian ID proof validators (browser-safe, framework-agnostic).
 * Mirrors `visitormanagement/visitor_management/validators.py`.
 *
 * Exposed as `window.VMS_IDValidators` so any Frappe web form / desk form /
 * custom page script can call it. Pure JS — no jQuery, no Frappe deps.
 *
 * Canonical labels: "Aadhaar", "PAN Card", "Driving License", "Passport".
 * Aliases ("PAN", "DL") are accepted by validateID / idProofErrorMessage.
 */
(function (global) {
	"use strict";

	// ── Verhoeff tables (for Aadhaar) ───────────────────────────
	const D = [
		[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
		[1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
		[2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
		[3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
		[4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
		[5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
		[6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
		[7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
		[8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
		[9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
	];
	const P = [
		[0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
		[1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
		[5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
		[8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
		[9, 4, 5, 3, 1, 2, 6, 8, 7, 0],
		[4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
		[2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
		[7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
	];

	function verhoeffChecksum(digits) {
		let c = 0;
		for (let i = 0; i < digits.length; i++) {
			const d = parseInt(digits[digits.length - 1 - i], 10);
			c = D[c][P[i % 8][d]];
		}
		return c;
	}

	// ── Primitive validators ────────────────────────────────────
	const AADHAAR_RE = /^\d{12}$/;
	const PAN_RE = /^[A-Z]{5}[0-9]{4}[A-Z]{1}$/;
	const DL_RE = /^[A-Z]{2}[0-9]{2}\s?[0-9]{11}$/;
	const PASSPORT_RE = /^[A-Z]{1}[0-9]{7}$/;

	function strip(v) {
		return String(v == null ? "" : v).replace(/\s+/g, "");
	}
	function stripHyphens(v) {
		return String(v == null ? "" : v).replace(/[\s-]/g, "");
	}

	function validateAadhaar(number) {
		const clean = stripHyphens(number);
		if (!AADHAAR_RE.test(clean)) return false;
		if (clean[0] === "0" || clean[0] === "1") return false;
		return verhoeffChecksum(clean) === 0;
	}

	function validatePAN(number) {
		return PAN_RE.test(strip(number).toUpperCase());
	}

	function validateDrivingLicense(number) {
		const clean = String(number == null ? "" : number)
			.trim()
			.toUpperCase()
			.replace(/\s+/g, " ");
		return DL_RE.test(clean);
	}

	function validatePassport(number) {
		return PASSPORT_RE.test(strip(number).toUpperCase());
	}

	// ── Type dispatch ──────────────────────────────────────────
	const CANONICAL = {
		aadhaar: "Aadhaar",
		aadhar: "Aadhaar",
		uid: "Aadhaar",
		pan: "PAN Card",
		"pan card": "PAN Card",
		dl: "Driving License",
		"driving license": "Driving License",
		"driving licence": "Driving License",
		passport: "Passport",
	};
	const VALIDATORS = {
		"Aadhaar": validateAadhaar,
		"PAN Card": validatePAN,
		"Driving License": validateDrivingLicense,
		"Passport": validatePassport,
	};

	function canonicalType(idType) {
		return CANONICAL[String(idType == null ? "" : idType).trim().toLowerCase()] || null;
	}

	function validateID(idType, number) {
		const label = canonicalType(idType);
		if (!label) return false;
		return VALIDATORS[label](number);
	}

	function detectIDType(number) {
		const order = ["Aadhaar", "PAN Card", "Driving License", "Passport"];
		for (let i = 0; i < order.length; i++) {
			if (VALIDATORS[order[i]](number)) return order[i];
		}
		return null;
	}

	const ERROR_MESSAGES = {
		"Aadhaar":
			"Aadhaar must be exactly 12 digits, must not start with 0 or 1, " +
			"and must pass the UIDAI Verhoeff checksum.",
		"PAN Card":
			"PAN must be in the format ABCDE1234F " +
			"(5 uppercase letters, 4 digits, 1 uppercase letter).",
		"Driving License":
			"Driving License must be in the format SS00 00000000000 " +
			"(2 letters + 2 digits + optional space + 11 digits).",
		"Passport":
			"Passport must be in the format A1234567 " +
			"(1 uppercase letter followed by 7 digits).",
	};

	function idProofErrorMessage(idType) {
		const label = canonicalType(idType) || idType;
		return (
			ERROR_MESSAGES[label] ||
			"Unsupported ID Proof Type: " +
				JSON.stringify(idType) +
				". Use Aadhaar, PAN Card, Driving License, or Passport."
		);
	}

	global.VMS_IDValidators = {
		validateAadhaar,
		validatePAN,
		validateDrivingLicense,
		validatePassport,
		detectIDType,
		validateID,
		idProofErrorMessage,
	};
})(typeof window !== "undefined" ? window : globalThis);
