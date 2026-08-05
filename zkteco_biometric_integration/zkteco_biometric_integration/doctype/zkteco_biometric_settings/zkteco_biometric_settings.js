// Copyright (c) 2025, Navari Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("ZKTeco Biometric Settings", {
	refresh(frm) {
		getToken(frm);
	},
});

function getToken(frm) {
	frm.add_custom_button(__("Get Token"), () => fetchToken(frm));
}

function fetchToken(frm) {
	frappe
		.call({
			doc: frm.doc,
			method: "generate_token",
			freeze: true,
			freeze_message: __("Generating authentication token..."),
		})
		.then((r) => {
			if (r.message) {
				frm.reload_doc();
				frappe.show_alert({
					message: __("Token retrieved successfully."),
					indicator: "green",
				});
			}
		});
}
