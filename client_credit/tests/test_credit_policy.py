# Copyright (c) 2026, enfono and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from erpnext.selling.doctype.customer.customer import get_customer_outstanding

from client_credit.client_credit.report.customer_credit_exposure.customer_credit_exposure import (
	execute as run_report,
)

CUSTOMER_GROUP = "All Customer Groups"
TERRITORY = "All Territories"
OVERRIDE_ROLE = "System Manager"  # Administrator (the default test user) already has this role.


def make_customer(prefix):
	name = f"{prefix} {frappe.generate_hash(length=8)}"
	doc = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": name,
			"customer_group": CUSTOMER_GROUP,
			"customer_type": "Individual",
			"territory": TERRITORY,
		}
	).insert()
	return doc.name


def set_credit_policy(customer, company, credit_limit=0, grace_amount=0, max_overdue_days=0):
	customer_doc = frappe.get_doc("Customer", customer)
	row = None
	for d in customer_doc.credit_limits:
		if d.company == company:
			row = d
			break

	if not row:
		customer_doc.append("credit_limits", {"company": company})
		row = customer_doc.credit_limits[-1]

	row.credit_limit = credit_limit
	row.custom_grace_amount = grace_amount
	row.custom_max_overdue_days = max_overdue_days

	if row.is_new():
		row.db_insert()
	else:
		row.db_update()


class TestCreditPolicy(FrappeTestCase):
	"""Everything here runs against a dedicated, throwaway Company/Item created
	in setUpClass, rather than erpnext's own "_Test Company"/"_Test Item" test
	fixtures - those are only populated by erpnext's own ``before_tests`` hook,
	which ``bench run-tests --app client_credit`` does not run (it only runs
	before_tests hooks contributed by this app). Creating our own company also
	means these tests do not depend on, or interfere with, any real company
	data already on the site.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()

		abbr = "C" + frappe.generate_hash(length=3).upper()
		company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": f"Credit Policy Test {abbr}",
				"abbr": abbr,
				"default_currency": "INR",
				"country": "India",
			}
		).insert()

		cls.company = company.name
		cls.debit_to = f"Debtors - {abbr}"
		cls.income_account = f"Sales - {abbr}"
		cls.cost_center = f"Main - {abbr}"

		cls.item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": f"Credit Policy Test Item {abbr}",
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 0,
			}
		).insert().item_code

	def setUp(self):
		frappe.db.set_single_value("Accounts Settings", "credit_controller", None)

	def tearDown(self):
		frappe.db.set_single_value("Accounts Settings", "credit_controller", None)

	def make_invoice(self, customer, qty=1, rate=100, do_not_submit=False, **kwargs):
		si = frappe.new_doc("Sales Invoice")
		si.company = self.company
		si.customer = customer
		si.debit_to = self.debit_to

		posting_date = kwargs.pop("posting_date", None)
		if posting_date:
			si.set_posting_time = 1
			si.posting_date = posting_date

		si.update(kwargs)

		si.append(
			"items",
			{
				"item_code": self.item,
				"qty": qty,
				"rate": rate,
				"income_account": self.income_account,
				"cost_center": self.cost_center,
			},
		)
		si.insert()
		if not do_not_submit:
			si.submit()
		return si

	def test_within_limit_submits_silently(self):
		customer = make_customer("Within Limit")
		set_credit_policy(customer, self.company, credit_limit=1000)

		si = self.make_invoice(customer, qty=1, rate=500)

		self.assertEqual(si.docstatus, 1)
		self.assertFalse(
			frappe.db.exists("Credit Limit Override Log", {"sales_invoice": si.name}),
		)

	def test_amount_over_limit_blocked_with_actual_numbers_in_message(self):
		customer = make_customer("Over Limit")
		set_credit_policy(customer, self.company, credit_limit=100, grace_amount=0)

		with self.assertRaises(frappe.ValidationError) as ctx:
			self.make_invoice(customer, qty=1, rate=500)

		message = str(ctx.exception)
		self.assertIn("100", message)
		self.assertIn("500", message)

	def test_grace_amount_extends_effective_limit(self):
		customer = make_customer("Grace")
		set_credit_policy(customer, self.company, credit_limit=100, grace_amount=50)

		# 140 <= 100 + 50: allowed.
		si = self.make_invoice(customer, qty=1, rate=140)
		self.assertEqual(si.docstatus, 1)

		# Next invoice pushes outstanding to 300, past the 150 effective limit.
		with self.assertRaises(frappe.ValidationError):
			self.make_invoice(customer, qty=1, rate=160)

	def test_override_role_can_submit_and_override_is_recorded(self):
		customer = make_customer("Override")
		set_credit_policy(customer, self.company, credit_limit=100, grace_amount=0)
		frappe.db.set_single_value("Accounts Settings", "credit_controller", OVERRIDE_ROLE)

		si = self.make_invoice(customer, qty=1, rate=500)

		self.assertEqual(si.docstatus, 1)

		log_name = frappe.db.exists("Credit Limit Override Log", {"sales_invoice": si.name})
		self.assertTrue(log_name)

		log = frappe.get_doc("Credit Limit Override Log", log_name)
		self.assertEqual(log.customer, customer)
		self.assertEqual(log.company, self.company)
		self.assertEqual(log.overridden_by, frappe.session.user)
		self.assertIsNotNone(log.overridden_on)
		self.assertEqual(log.reason, "Amount Exceeded")

	def test_overdue_invoice_blocks_even_within_amount_limit(self):
		customer = make_customer("Overdue")
		set_credit_policy(customer, self.company, credit_limit=100000, max_overdue_days=30)

		# Backdated invoice, unpaid, due well over 30 days ago.
		self.make_invoice(customer, qty=1, rate=10, posting_date=add_days(today(), -60))

		with self.assertRaises(frappe.ValidationError) as ctx:
			self.make_invoice(customer, qty=1, rate=1)

		self.assertIn("overdue", str(ctx.exception).lower())

	def test_return_invoice_is_exempt_and_reduces_exposure(self):
		customer = make_customer("Return")
		set_credit_policy(customer, self.company, credit_limit=100000, max_overdue_days=30)

		original = self.make_invoice(customer, qty=1, rate=100, posting_date=add_days(today(), -60))

		# The original is overdue past the policy; a credit note against it must
		# not be blocked by either check.
		credit_note = self.make_invoice(
			customer,
			qty=-1,
			rate=100,
			is_return=1,
			return_against=original.name,
		)

		self.assertEqual(credit_note.docstatus, 1)
		self.assertFalse(
			frappe.db.exists("Credit Limit Override Log", {"sales_invoice": credit_note.name}),
		)

		outstanding = get_customer_outstanding(customer, self.company)
		self.assertAlmostEqual(outstanding, 0, places=2)

	def test_amended_invoice_is_not_double_counted(self):
		customer = make_customer("Amend")
		set_credit_policy(customer, self.company, credit_limit=1000)

		si = self.make_invoice(customer, qty=1, rate=90)
		si.cancel()

		amended = frappe.copy_doc(si)
		amended.docstatus = 0
		amended.amended_from = si.name
		amended.insert()
		amended.submit()

		outstanding = get_customer_outstanding(customer, self.company)
		self.assertAlmostEqual(outstanding, 90, places=2)

	def test_blank_credit_limit_is_treated_as_unlimited(self):
		customer = make_customer("Unlimited")
		# No Customer Credit Limit row at all for this company.

		si = self.make_invoice(customer, qty=1, rate=100000)

		self.assertEqual(si.docstatus, 1)
		self.assertFalse(
			frappe.db.exists("Credit Limit Override Log", {"sales_invoice": si.name}),
		)

	def test_multi_currency_invoice_compared_in_company_currency(self):
		customer = make_customer("Multi Currency")
		set_credit_policy(customer, self.company, credit_limit=1000, grace_amount=0)

		# 100 USD @ 80 => 8000 in company currency (INR), well past the limit.
		with self.assertRaises(frappe.ValidationError):
			self.make_invoice(customer, qty=1, rate=100, currency="USD", conversion_rate=80)

	def test_report_lists_customers_at_or_above_threshold(self):
		high = make_customer("High Utilisation")
		set_credit_policy(high, self.company, credit_limit=100, grace_amount=0)
		self.make_invoice(high, qty=1, rate=90)  # 90%

		low = make_customer("Low Utilisation")
		set_credit_policy(low, self.company, credit_limit=1000, grace_amount=0)
		self.make_invoice(low, qty=1, rate=100)  # 10%

		columns, data = run_report({"company": self.company, "min_utilisation_percent": 80})

		self.assertEqual(len(columns), 6)
		customers_in_report = {row["customer"] for row in data}
		self.assertIn(high, customers_in_report)
		self.assertNotIn(low, customers_in_report)
