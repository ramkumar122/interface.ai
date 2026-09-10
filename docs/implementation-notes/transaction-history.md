# Implementation notes — transaction-history

## What was built

A read-only screen at `GET /member/{member_no}/transactions` (replacing the stub).
An operator picks a share and a `From`/`To` date range and searches; results show
DATE / DESCRIPTION / TYPE / AMOUNT, most-recent-first, paged 10 at a time with
Previous/Next. Debits render in accounting parentheses. Defaults: primary savings,
last 30 days.

## Files created / modified

| Path | Created/Modified | Reason |
|---|---|---|
| `coredesk/templates/transactions.html` | created | search form + results grid + paging |
| `tests/test_transaction_history.py` | created | feature tests (6) |
| `docs/implementation-notes/transaction-history.md` | created | this record |
| `coredesk/app.py` | modified | transactions route + `_fmt_amount`; removed transactions stub |
| `db/fixtures.py` | modified | added TXN-00016/00017 → 100101 checking now 12 rows (still within the 8–12 contract) so paging is exercised |
| `tests/test_queries.py` | modified | `test_list_transactions_for_checking` count 10 → 12 |

## Decisions I made

- **Bumped 100101 checking from 10 to 12 transactions** (both new rows dated
  08/28 and 09/02, outside July) so a real page 2 exists. This stays inside the
  fixture contract's "8–12 rows" and keeps the July date-bounded count at 4; the
  one Task-2 query test that asserted `len == 10` is updated to `12`.
- **Sort descending in the route.** `list_transactions` returns ascending; the
  screen sorts `(posted_on, txn_id)` descending for most-recent-first.
- **Default share = primary savings**, else the first share. The seeded primary
  savings has no transactions, so the default view shows the empty message until a
  share with activity (the checking) is selected — realistic for this screen.
- **Lenient date parsing.** A reversed range is the one validation error; an
  unparseable bound is simply dropped (treated as no bound) rather than erroring,
  since the spec only specifies the reversed-range message.
- **No JavaScript, no writes.**

## AX contract added

```
- combobox "Share"
- textbox "From"
- textbox "To"
- button "Search"
- table "Transaction history":
  - row: [columnheader "DATE"] [columnheader "DESCRIPTION"] [columnheader "TYPE"] [columnheader "AMOUNT"]
  - row: [cell "09/02/2026"] [cell "ACH DEPOSIT - PAYROLL"] [cell "ACH"] [cell "2,450.00"]
  - row: [cell "08/28/2026"] [cell "POS PURCHASE - PHARMACY"] [cell "POS"] [cell "(31.15)"]
- link "Next"        # when more rows remain
- link "Previous"    # when offset > 0
```

## Audit rows this feature writes

None. This feature is read-only.

## Tests added

- `test_orders_most_recent_first` — Req 3.3
- `test_empty_range_message` — Req 3.5
- `test_reversed_dates_validation` — Req 3.6
- `test_debit_parenthesised_credit_plain` — Req 3.2
- `test_paging` — Req 3.4 (page 1 = 10 rows + Next, no Previous; page 2 = 2 rows + Previous, no Next; different rows)
- `test_invariants` — Req 4.2, 4.3

## Known gaps

- Amount right-alignment is an `align` attribute (survives stylesheet stripping);
  the grid's money column has no `.currency` class.
- No CSV/export or running-balance column (out of scope for this thin screen).
