# Design — Address Maintenance

Builds on `write-path.md` and the card-maintenance write pattern.

## Route map

| Route | Method | Template | queries.py |
|---|---|---|---|
| `/member/{m}/address` | GET | `address_form.html` | `get_member` |
| `/member/{m}/address` | POST | `address_form.html` (re-render) or 303 → review | `get_member` |
| `/member/{m}/address/review` | GET | `address_review.html` | `get_member` |
| `/member/{m}/address/commit` | POST | 303 → form | `get_member`, `update_member_address` |

Replaces the `/member/{m}/address` stub. All ≥3 segments; no catch-all collision.

## Pending-change mechanism (the design question)

**Chosen: hidden-field re-post.** `POST /address` validates, then 303-redirects to
`GET /address/review?line1=…&line2=…&city=…&state=…&zip=…&eff=…`. The review page
renders those values and embeds them as hidden inputs in a form that POSTs to
`/address/commit`. Nothing is stored server-side.

Trade-off / PII: the address transits the review **URL** (so it can appear in
browser history) and the review HTML. It never persists server-side and never
lands in a cookie. For a mock servicing screen this is acceptable; the honest
caveat is the URL/history exposure. A signed cookie would keep it out of the URL
but put an address in the browser; a server session store would add state we
explicitly don't want. Hidden-field re-post is the simplest that stores nothing.

## Validation

A pure helper `_validate_address(line1, city, state, zip, eff) -> (errors: dict,
eff_norm)`. Rules per Requirement 3. ZIP: strip one optional hyphen, must be all
digits of length 5 or 9. Date: parse `MM/DD/YYYY` via the existing date helpers;
past = strictly before `date.today()`. Errors is `{field: message}`; the template
prints each beside its field. Money/PII: none here.

## Idempotent commit (no double-write)

`update_member_address` unconditionally writes + audits, so the **route** guards:
load the member, compare the submitted `(line1, line2, city, state, zip)` against
the current values; if identical, redirect to the form with `?nochange=1` (info
"No change to apply.") and do **not** call `update_member_address`. Only a real
difference calls it → one `ADDRESS_UPDATE` row + `ADR-` ref. A stale re-submit
(address already applied) therefore no-ops.

## Config addition

`STATE_CODES` — module-level list of the 50 two-letter codes (+ `DC`) in
`coredesk/config.py`; passed to the form template. Not tenant-specific.

## Screens

- `address_form.html` (extends base): title `Address Change - {inst}`, `<h1>ADDRESS
  CHANGE</h1>`; `?ref` → green banner `Address updated. Reference: ADR-….`;
  `?nochange=1` → notice `No change to apply.`; current-address read-only block;
  form `method=post` to `/address` with `vals` (submitted-or-current) and `errors`
  (message beside each field); `State` select from `STATE_CODES`; `Continue`
  (`cfg.btn_continue`) + `Cancel` (→ member record).
- `address_review.html` (extends base): `<h1>ADDRESS CHANGE - REVIEW</h1>`; if no
  pending values → the informational message + link to form; else the
  `FIELD|CURRENT|NEW` table (`<caption>Address change review</caption>`), then a
  commit form with hidden inputs for the six values + `Confirm Update` / `Back to
  Edit`.

Effective Date has no stored "current", so its CURRENT cell shows an em dash.

## AX contract

Review grid — `/member/100101/address/review?...`:

```
- table "Address change review":
  - row:
    - columnheader "FIELD"
    - columnheader "CURRENT"
    - columnheader "NEW"
  - row: [cell "Address Line 1"] [cell "742 Evergreen Ter"] [cell "<new>"]
  - ... one row per field ...
```

Form (`/member/100101/address`): `textbox "Address Line 1"`, `textbox "Address
Line 2"`, `textbox "City"`, `combobox "State"`, `textbox "ZIP Code"`, `textbox
"Effective Date"`, `button "Continue"` (or `Next` on summit), `link "Cancel"`.

## Audit rows

| Action | When | detail |
|---|---|---|
| `ADDRESS_UPDATE` | commit applies a real change | `Address updated, effective <MM/DD/YYYY>` (no free text beyond the date) |

## Files

Created: `address_form.html`, `address_review.html`, `tests/test_address_maintenance.py`,
`docs/implementation-notes/address-maintenance.md`.
Modified: `coredesk/app.py` (four routes, `_validate_address`, remove address stub),
`coredesk/config.py` (`STATE_CODES`).

## Tests

Function-scoped `reset_db.rebuild()` (mutating), `auth`/`auth_ro`. Cases: each of
the six rules independently; two simultaneous failures; valid → review with
correct current/new; review with no pending → info message; commit writes +1
`ADDRESS_UPDATE` + `ADR-` ref; double commit → no second write; TELLER_RO refused
at both POSTs; submitted values survive a validation failure; invariants.
