# Copyright (c) 2026, enfono and contributors
# For license information, please see license.txt

from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

from client_credit.credit_policy import evaluate_sales_invoice


class ClientCreditSalesInvoice(SalesInvoice):
	"""Replaces core's ``check_credit_limit`` (called from ``on_submit``,
	right after GL Entries for this invoice are posted) with the client's
	credit policy: limit + grace, and a max-overdue-days rule, with a
	recorded override instead of core's plain role bypass.

	Intentionally does not call ``super().check_credit_limit()`` - core's
	version only compares outstanding to a bare credit limit and would
	either block invoices the grace amount should allow, or silently permit
	an override with no record, both of which contradict the policy this
	app implements.
	"""

	def check_credit_limit(self):
		evaluate_sales_invoice(self)
