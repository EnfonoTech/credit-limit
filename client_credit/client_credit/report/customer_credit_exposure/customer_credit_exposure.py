# Copyright (c) 2026, enfono and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt

from client_credit.credit_policy import get_customer_exposure


def execute(filters=None):
	filters = frappe._dict(filters or {})
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 180},
		{"fieldname": "credit_limit", "label": _("Credit Limit"), "fieldtype": "Currency", "width": 120},
		{"fieldname": "grace_amount", "label": _("Grace"), "fieldtype": "Currency", "width": 100},
		{"fieldname": "outstanding", "label": _("Outstanding"), "fieldtype": "Currency", "width": 120},
		{
			"fieldname": "utilisation_percent",
			"label": _("Utilisation %"),
			"fieldtype": "Percent",
			"width": 110,
		},
		{
			"fieldname": "worst_overdue_days",
			"label": _("Worst Overdue Days"),
			"fieldtype": "Int",
			"width": 130,
		},
	]


def get_data(filters):
	if not filters.get("company"):
		frappe.throw(_("Company is required"))

	threshold = flt(filters.get("min_utilisation_percent") or 80)

	customer_filters = {"parenttype": "Customer", "company": filters.company}
	if filters.get("customer"):
		customer_filters["parent"] = filters.customer

	customers = frappe.db.get_all(
		"Customer Credit Limit",
		filters=customer_filters,
		fields=["parent as customer"],
		pluck="customer",
	)

	rows = []
	for customer in customers:
		exposure = get_customer_exposure(customer, filters.company)

		if exposure.utilisation_percent is None:
			# Blank/zero effective limit -> unlimited, utilisation is undefined.
			continue

		if exposure.utilisation_percent < threshold:
			continue

		rows.append(
			{
				"customer": exposure.customer,
				"credit_limit": exposure.credit_limit,
				"grace_amount": exposure.grace_amount,
				"outstanding": exposure.outstanding,
				"utilisation_percent": exposure.utilisation_percent,
				"worst_overdue_days": exposure.worst_overdue_days,
			}
		)

	rows.sort(key=lambda r: r["utilisation_percent"], reverse=True)
	return rows
