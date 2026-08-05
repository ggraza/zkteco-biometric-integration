// Copyright (c) 2025, Navari Limited and contributors
// For license information, please see license.txt

frappe.ui.form.on("ZKTeco Biometric Settings", {
	refresh(frm) {
		getToken(frm);
		syncEmployees(frm);
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

const SEARCH_EMPLOYEES_METHOD =
	"zkteco_biometric_integration.zkteco_biometric_integration.services.employee_service.search_employees";

const FILTER_FIELDS = ["company", "department", "designation", "branch", "role", "status"];

function syncEmployees(frm) {
	frm.add_custom_button(__("Sync Employees"), () => showSyncDialog(frm));
}

function showSyncDialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Sync Employees to ZKTeco"),
		size: "large",
		fields: [
			{ fieldtype: "Section Break", label: __("Filters") },
			{ fieldtype: "Link", fieldname: "company", label: __("Company"), options: "Company" },
			{
				fieldtype: "Link",
				fieldname: "department",
				label: __("Department"),
				options: "Department",
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Link",
				fieldname: "designation",
				label: __("Designation"),
				options: "Designation",
			},
			{ fieldtype: "Link", fieldname: "branch", label: __("Branch"), options: "Branch" },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Link", fieldname: "role", label: __("User Role"), options: "Role" },
			{
				fieldtype: "Select",
				fieldname: "status",
				label: __("Status"),
				options: ["Active", "Inactive", "Suspended", "Left"].join("\n"),
				default: "Active",
			},
			{ fieldtype: "Section Break", label: __("Employees") },
			{
				fieldtype: "Check",
				fieldname: "sync_all",
				label: __("Sync every employee matching the filters above"),
				onchange: () =>
					dialog.set_df_property(
						"employees",
						"hidden",
						dialog.get_value("sync_all") ? 1 : 0
					),
			},
			{
				fieldtype: "MultiSelectList",
				fieldname: "employees",
				label: __("Employees"),
				get_data: (txt) => fetchEmployees(dialog, txt),
			},
		],
		primary_action_label: __("Sync"),
		primary_action: (values) => runSync(frm, dialog, values),
	});

	dialog.show();
}

function collectFilters(dialog) {
	const values = dialog.get_values(true) || {};

	return FILTER_FIELDS.reduce((filters, field) => {
		if (values[field]) {
			filters[field] = values[field];
		}
		return filters;
	}, {});
}

function fetchEmployees(dialog, txt) {
	return frappe
		.call({
			method: SEARCH_EMPLOYEES_METHOD,
			args: { filters: collectFilters(dialog), txt: txt || "" },
		})
		.then((r) => r.message || []);
}

function runSync(frm, dialog, values) {
	const employees = values.sync_all ? [] : values.employees || [];

	if (!values.sync_all && !employees.length) {
		frappe.msgprint(__("Select at least one employee, or tick the sync-all option."));
		return;
	}

	frappe
		.call({
			doc: frm.doc,
			method: "sync_employees",
			args: { employees: employees, filters: collectFilters(dialog) },
			freeze: true,
			freeze_message: __("Syncing employees..."),
		})
		.then((r) => {
			dialog.hide();
			showSyncResult(r.message);
		});
}

function showSyncResult(result) {
	if (!result) return;

	if (result.queued) {
		frappe.msgprint({
			title: __("Queued"),
			indicator: "blue",
			message: __("Syncing {0} employees in the background.", [result.total]),
		});
		return;
	}

	const failed = result.failed || [];

	if (!failed.length) {
		frappe.show_alert({
			message: __("Synced {0} employee(s).", [result.synced.length]),
			indicator: "green",
		});
		return;
	}

	const rows = failed
		.map(
			(f) =>
				`<li><b>${frappe.utils.escape_html(f.employee)}</b>: ${frappe.utils.escape_html(
					f.error
				)}</li>`
		)
		.join("");

	frappe.msgprint({
		title: __("Sync completed with errors"),
		indicator: "orange",
		message:
			__("Synced {0}, failed {1}:", [result.synced.length, failed.length]) +
			`<ul>${rows}</ul>`,
	});
}
