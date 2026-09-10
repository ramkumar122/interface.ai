# Design — Transaction History

Read-only; builds on member-inquiry patterns. One route.

## Route

`GET /member/{member_no}/transactions` (replaces the stub) → `transactions.html`.
Query params: `share`, `from`, `to` (MM/DD/YYYY), `offset`. Uses `get_member`,
`list_shares`, `get_primary_savings`, `list_transactions`. No new SQL, no writes.

## Logic

- `shares = list_shares(member_no)`; default share = `get_primary_savings` id, else
  the first share, else none.
- Dates default to last 30 days: `from = today-30`, `to = today` (as MM/DD/YYYY),
  overridden by query params. Parse via `_parse_mmddyyyy`.
- If both parse and `from > to` → error `From date must be on or before To date.`,
  render no rows.
- Else `list_transactions(share, from_iso, to_iso)` (iso = `YYYY-MM-DD` or None),
  then sort DESC by `(posted_on, txn_id)`. `total = len`. Page = `rows[offset:offset+10]`.
- `PAGE_SIZE = 10`. `Previous` href when `offset > 0` (offset-10); `Next` when
  `offset+10 < total` (offset+10). Both preserve `share`/`from`/`to`.
- Empty (no error, total 0) → `No transactions found for the selected period.`

## Amount formatting

`_fmt_amount(cents)`: `cents < 0` → `"(" + cents_to_display(-cents) + ")"`, else
`cents_to_display(cents)`. Rendered in a `<td align="right">`. Debit `-4500` →
`(45.00)`; credit `245000` → `2,450.00`.

## Screen (`transactions.html`, extends base)

`<h1>TRANSACTION HISTORY</h1>`; a `method="get"` form with `Share` select (options
`suffix - description`, value = share_id, selected = current), `From`/`To` text
inputs (pre-filled), `Search` button. Then, if error, the red message; else a grid
`id="ctl00_MainContent_grdTxn"`, `<caption>Transaction history</caption>`, headers
`DATE | DESCRIPTION | TYPE | AMOUNT`, one row per page item (amount cell
`align="right"`), or a full-width `No transactions...` cell. Below: `Previous` /
`Next` links as applicable.

## AX contract

```
- table "Transaction history":
  - row: [columnheader "DATE"] [columnheader "DESCRIPTION"] [columnheader "TYPE"] [columnheader "AMOUNT"]
  - row: [cell "09/02/2026"] [cell "ACH DEPOSIT - PAYROLL"] [cell "ACH"] [cell "2,450.00"]
  - row: [cell "08/28/2026"] [cell "POS PURCHASE - PHARMACY"] [cell "POS"] [cell "(31.15)"]
  - ...
- combobox "Share"
- textbox "From"
- textbox "To"
- button "Search"
- link "Next"      # when more rows remain
```

## Files
Created: `transactions.html`, `tests/test_transaction_history.py`,
`docs/implementation-notes/transaction-history.md`. Modified: `coredesk/app.py`
(route + `_fmt_amount`; remove transactions stub).

## Tests
`100101` checking DESC order; empty range → message; reversed dates → validation
message; a debit renders `(...)` and a credit does not; paging: page 1 (offset 0)
= 10 rows + `Next`, no `Previous`; page 2 (offset 10) = 2 rows + `Previous`, no
`Next`, different rows; invariants.
