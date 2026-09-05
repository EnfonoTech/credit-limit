# Copyright (c) 2026, enfono and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

CREDIT_LIMIT_CHILD_DOCTYPE = "Customer Credit Limit"
OVERRIDE_LOG_DOCTYPE = "Credit Limit Override Log"


def get_credit_policy(customer, company):
	"""Per (customer, company) policy: credit limit, grace amount, max overdue days.

	Deliberately does NOT fall back to the Customer Group level row that core
	ERPNext's ``get_credit_limit`` supports - the brief asks for a policy per
	customer per company, not per customer group, so a missing row is simply
	"no policy configured" rather than being inherited from the group.
	"""
	row = frappe.db.get_value(
		CREDIT_LIMIT_CHILD_DOCTYPE,
		{"parent": customer, "parenttype": "Customer", "company": company},
		["credit_limit", "custom_grace_amount", "custom_max_overdue_days"],
		as_dict=True,
	)

	if not row:
		return frappe._dict(credit_limit=0, grace_amount=0, max_overdue_days=0)

	return frappe._dict(
		credit_limit=flt(row.credit_limit),
		grace_amount=flt(row.custom_grace_amount),
		max_overdue_days=cint(row.custom_max_overdue_days),
	)


def get_worst_overdue_days(customer, company, exclude_invoice=None):
	"""Largest number of days any *other, currently outstanding* invoice for
	this customer/company is past its due date. 0 if nothing is overdue.

	Only submitted (docstatus=1), non-return invoices with a positive
	outstanding amount are considered - this keeps drafts and cancelled/
	amended invoices out, and matches the population used for the amount
	check via ``get_customer_outstanding``. The invoice currently being
	submitted is excluded: by the time this runs its own row is already
	committed (docstatus=1), so a backdated invoice would otherwise be
	blocked purely for being overdue against itself.
	"""
	filters = {
		"customer": customer,
		"company": company,
		"docstatus": 1,
		"is_return": 0,
		"outstanding_amount": [">", 0],
	}
	if exclude_invoice:
		filters["name"] = ["!=", exclude_invoice]

	rows = frappe.db.get_all("Sales Invoice", filters=filters, fields=["due_date"])

	today = getdate()
	worst = 0
	for row in rows:
		if not row.due_date:
			continue
		overdue_days = (today - getdate(row.due_date)).days
		if overdue_days > worst:
			worst = overdue_days

	return worst


def get_override_role():
	return frappe.db.get_single_value("Accounts Settings", "credit_controller")


def user_can_override():
	role = get_override_role()
	return bool(role) and role in frappe.get_roles()


def evaluate_sales_invoice(sales_invoice):
	"""Run the credit policy against a Sales Invoice at submit time.

	Called from the ``check_credit_limit`` override on submit, i.e. *after*
	``make_gl_entries`` has already posted this invoice's own GL Entries in
	the same transaction (see core ``Sales Invoice.on_submit``). That means
	``get_customer_outstanding`` already reflects this invoice's own
	contribution, so we must not add ``grand_total`` a second time - doing so
	would double count it and would also mis-handle credit notes, whose
	negative GL impact already reduces the figure returned.

	Credit notes (``is_return``) only ever reduce exposure, so they are
	exempt from both checks entirely.
	"""
	from erpnext.selling.doctype.customer.customer import get_customer_outstanding

	if sales_invoice.is_return:
		return

	customer = sales_invoice.customer
	company = sales_invoice.company

	policy = get_credit_policy(customer, company)
	credit_limit = policy.credit_limit
	grace_amount = policy.grace_amount
	max_overdue_days = policy.max_overdue_days

	# Blank/zero credit limit -> unlimited: skip the amount check entirely.
	amount_breach = False
	effective_limit = credit_limit + grace_amount
	outstanding = 0.0
	if credit_limit > 0:
		# GL Entry.debit/credit are always stored in company (base) currency
		# regardless of the transaction currency, and credit_limit/grace are
		# entered by finance in company currency too, so comparing them
		# directly keeps everything in one currency without an extra
		# conversion step.
		outstanding = flt(get_customer_outstanding(customer, company))
		amount_breach = outstanding > effective_limit

	# Blank/zero max overdue days -> no overdue policy configured: skip.
	overdue_breach = False
	worst_overdue_days = 0
	if max_overdue_days > 0:
		worst_overdue_days = get_worst_overdue_days(customer, company, exclude_invoice=sales_invoice.name)
		overdue_breach = worst_overdue_days > max_overdue_days

	if not amount_breach and not overdue_breach:
		return

	message = _build_message(
		customer=customer,
		outstanding=outstanding,
		credit_limit=credit_limit,
		grace_amount=grace_amount,
		effective_limit=effective_limit,
		amount_breach=amount_breach,
		worst_overdue_days=worst_overdue_days,
		max_overdue_days=max_overdue_days,
		overdue_breach=overdue_breach,
	)

	if not user_can_override():
		frappe.throw(message, title=_("Credit Limit Exceeded"))

	frappe.msgprint(
		_("Submitted with an override of the customer credit policy. This has been recorded.")
		+ "<br><br>"
		+ message,
		title=_("Credit Limit Override"),
		indicator="orange",
	)

	_record_override(
		sales_invoice=sales_invoice,
		customer=customer,
		company=company,
		outstanding=outstanding,
		credit_limit=credit_limit,
		grace_amount=grace_amount,
		effective_limit=effective_limit,
		worst_overdue_days=worst_overdue_days,
		max_overdue_days=max_overdue_days,
		amount_breach=amount_breach,
		overdue_breach=overdue_breach,
	)


def _build_message(
	customer,
	outstanding,
	credit_limit,
	grace_amount,
	effective_limit,
	amount_breach,
	worst_overdue_days,
	max_overdue_days,
	overdue_breach,
):
	lines = []
	if amount_breach:
		lines.append(
			_(
				"Customer {0} has outstanding of {1} which exceeds the allowed limit of {2} "
				"(credit limit {3} + grace {4})."
			).format(
				frappe.bold(customer),
				frappe.bold(f"{outstanding:.2f}"),
				frappe.bold(f"{effective_limit:.2f}"),
				f"{credit_limit:.2f}",
				f"{grace_amount:.2f}",
			)
		)
	if overdue_breach:
		lines.append(
			_(
				"Customer {0} has an invoice overdue by {1} days, exceeding the maximum allowed "
				"of {2} days."
			).format(frappe.bold(customer), frappe.bold(worst_overdue_days), max_overdue_days)
		)
	return "<br>".join(lines)


def _record_override(
	sales_invoice,
	customer,
	company,
	outstanding,
	credit_limit,
	grace_amount,
	effective_limit,
	worst_overdue_days,
	max_overdue_days,
	amount_breach,
	overdue_breach,
):
	if amount_breach and overdue_breach:
		reason = "Amount Exceeded and Overdue"
	elif amount_breach:
		reason = "Amount Exceeded"
	else:
		reason = "Overdue Invoice"

	log = frappe.new_doc(OVERRIDE_LOG_DOCTYPE)
	log.update(
		{
			"sales_invoice": sales_invoice.name,
			"customer": customer,
			"company": company,
			"overridden_by": frappe.session.user,
			"overridden_on": now_datetime(),
			"reason": reason,
			"outstanding_amount": outstanding,
			"invoice_grand_total": flt(sales_invoice.base_grand_total),
			"credit_limit": credit_limit,
			"grace_amount": grace_amount,
			"effective_limit": effective_limit,
			"worst_overdue_days": worst_overdue_days,
			"max_overdue_days": max_overdue_days,
		}
	)
	log.flags.ignore_permissions = True
	log.insert(ignore_permissions=True)


def get_customer_exposure(customer, company):
	"""Shared by the report: one row of numbers for a customer/company pair."""
	from erpnext.selling.doctype.customer.customer import get_customer_outstanding

	policy = get_credit_policy(customer, company)
	effective_limit = policy.credit_limit + policy.grace_amount
	outstanding = flt(get_customer_outstanding(customer, company))
	worst_overdue_days = get_worst_overdue_days(customer, company)

	utilisation_percent = (outstanding / effective_limit * 100.0) if effective_limit > 0 else None

	return frappe._dict(
		customer=customer,
		company=company,
		credit_limit=policy.credit_limit,
		grace_amount=policy.grace_amount,
		effective_limit=effective_limit,
		outstanding=outstanding,
		utilisation_percent=utilisation_percent,
		worst_overdue_days=worst_overdue_days,
	)
