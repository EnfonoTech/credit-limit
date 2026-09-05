// Copyright (c) 2026, enfono and contributors
// For license information, please see license.txt

frappe.query_reports["Customer Credit Exposure"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "min_utilisation_percent",
			label: __("Minimum Utilisation %"),
			fieldtype: "Float",
			default: 80,
		},
	],
};
