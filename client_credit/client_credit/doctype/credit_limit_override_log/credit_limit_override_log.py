# Copyright (c) 2026, enfono and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CreditLimitOverrideLog(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(frappe._("Credit Limit Override Log entries cannot be modified after creation."))
