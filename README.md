# Client Credit

A Frappe/ERPNext app that blocks Sales Invoice submission when a customer has
gone past an agreed credit position, with an auditable override path and a
credit-exposure report.

## Approach: extend, don't reimplement

ERPNext already ships a credit-limit mechanism (`Customer` → *Credit Limit*
table, per Company, checked in `Sales Invoice.check_credit_limit()` on
submit). It is close to what's needed but too narrow: no grace amount, no
overdue-days rule, and its "override" is a silent role check with nothing
recorded. Reimplementing credit-limit-checking-on-submit from scratch would
duplicate logic ERPNext already gets right (GL-based outstanding, is_return
sign, amendment handling) and risks drifting out of sync with it.

Instead this app **extends** the existing mechanism:

- **Custom Fields**, not a new DocType, add `Grace Amount` and `Max Overdue
  Days` directly onto ERPNext's existing `Customer Credit Limit` child table
  (the same per-Customer-per-Company row that already holds `Credit Limit`).
  Shipped as fixtures, so plain Custom Fields, no core files touched.
- The existing **Credit Controller** role field on `Accounts Settings` *is*
  the "nominated override role" the brief asks for — reused as-is rather
  than inventing a second role field.
- `Sales Invoice.check_credit_limit()` — the exact method and call site core
  already uses on submit, right after this invoice's own GL Entries are
  posted — is replaced via `override_doctype_class` (`hooks.py`) with a
  version that evaluates limit+grace and the max-overdue-days rule, and
  actually records an override instead of silently letting a role through.
  `extend_doctype_class` (mixins, additive) would have been preferable to
  fully replacing the class, but it's a v16+ feature; this bench is v15.
- The new **Credit Limit Override Log** DocType is the one genuinely new
  piece of data storage, because nothing in core records "who overrode this,
  when, against which invoice" — that's the one thing the brief requires
  that has no existing home to extend.
- The new **Customer Credit Exposure** report is new (core's own *Customer
  Credit Balance* report doesn't know about grace or overdue days, and
  doesn't filter by utilisation), but it reuses core's
  `get_customer_outstanding()` rather than re-querying GL Entries itself.

See `client_credit/credit_policy.py` for the actual policy logic, and
`client_credit/overrides/sales_invoice.py` for the override hook-in.

## Where to configure it

- **Customer → Credit Limit** table (per Company): `Credit Limit`, `Grace
  Amount`, `Max Overdue Days` (blank/0 in either of the last two disables
  that specific check for that customer/company).
- **Accounts Settings → Credit Controller**: the role allowed to submit past
  the block. Anyone holding it gets a recorded override instead of a block;
  everyone else gets a hard stop.
- **Credit Limit Override Log** (list view): the audit trail — one row per
  override, who/when/which invoice/why/the numbers at the time.
- **Report → Customer Credit Exposure**: customers at or above a utilisation
  threshold (default 80%) for a given Company.

## Edge-case decisions

- **Amended invoices**: outstanding is computed from GL Entries
  (`is_cancelled = 0`), not from Sales Invoice rows directly; cancelling an
  invoice flags its GL Entries `is_cancelled = 1` and the amendment posts its
  own fresh entries on its own submit, so a cancel+amend cycle is counted
  exactly once, never twice (`get_customer_outstanding`, reused from core).
- **Return invoices**: an `Is Return` credit note posts a negative debit to
  the debtors account, so it reduces GL-based outstanding automatically;
  it is also explicitly exempted from both checks (core's own `on_submit`
  already skips `check_credit_limit()` entirely when `is_return`, and our
  override repeats the exemption defensively) — a credit note can only
  shrink exposure, so blocking it can never be correct.
- **Multi-currency**: everything is compared in the Sales Invoice's
  **Company (base) currency**. `GL Entry.debit`/`credit` are always stored
  in company currency regardless of the invoice's transaction currency, and
  `Credit Limit`/`Grace Amount` are entered by finance in company currency
  too — comparing them directly keeps one consistent unit without an extra
  conversion step. A USD invoice against a SAR company is converted to SAR
  by its own exchange rate at GL posting before the check ever runs.
- **Zero or blank credit limit**: treated as **unlimited** — the amount
  check is skipped entirely (matches core ERPNext's own
  `if not credit_limit: return` behaviour, so it isn't a surprise to anyone
  already used to stock ERPNext). Grace Amount is irrelevant when Credit
  Limit is blank/0. Likewise a blank/0 Max Overdue Days independently
  disables just the overdue check. Both must be explicitly set to bite.
- **Multi-company**: policy, outstanding, overdue-days and the report are
  all keyed by `(customer, company)` — the same `Customer Credit Limit`
  child row ERPNext already scopes per Company, so two companies for the
  same customer are fully independent positions by construction.
- **Draft invoices**: drafts (`docstatus = 0`) never count, consistently in
  both the block and the report — the amount check is GL-Entry-based
  (drafts have none) and the overdue-days query explicitly filters
  `docstatus = 1`, so there is one shared notion of "counts as exposure"
  behind both.

## Install

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/<your-fork>/client_credit --branch develop
bench --site <site> install-app client_credit
```

Requires ERPNext to already be installed on the site (`required_apps` in
`hooks.py` declares this so `bench get-app`/`install-app` pull it in).

## Tests

```bash
bench --site <site> set-config allow_tests true   # if not already enabled
bench --site <site> run-tests --app client_credit
```

The tests create their own throwaway Company/Item/Customers in
`setUpClass`/per test (not ERPNext's `_Test Company` fixtures, which are only
populated by ERPNext's own `before_tests` hook — a hook that
`--app client_credit` intentionally does not run), and everything is rolled
back automatically at the end of the test run (standard `FrappeTestCase`
behaviour) — safe to run against a site that already has real data.

## Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/client_credit
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.

### License

mit
# credit-limit
