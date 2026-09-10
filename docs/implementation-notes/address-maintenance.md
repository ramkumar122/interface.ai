# Implementation notes — address-maintenance

## What was built

The multi-field write with a review round-trip. Routes: `GET /member/{m}/address`
(edit form, also the confirmation target), `POST /member/{m}/address` (validate →
review or re-render), `GET /member/{m}/address/review` (side-by-side review), and
`POST /member/{m}/address/commit` (write). An operator edits a member's mailing
address, sees a current-vs-new review, and confirms; a real change writes one
`ADDRESS_UPDATE` row and shows an `ADR-` reference. Field-level validation and the
review are both real server states the automation can checkpoint.

## Files created / modified

| Path | Created/Modified | Reason |
|---|---|---|
| `coredesk/templates/address_form.html` | created | current address + edit form + banners + field errors |
| `coredesk/templates/address_review.html` | created | FIELD/CURRENT/NEW review + hidden-field commit form |
| `tests/test_address_maintenance.py` | created | feature tests (16) |
| `docs/implementation-notes/address-maintenance.md` | created | this record |
| `coredesk/app.py` | modified | four address routes, `_validate_address`/`_parse_mmddyyyy`; removed address stub |
| `coredesk/config.py` | modified | `STATE_CODES` |

## Decisions I made

- **Pending change via hidden-field re-post.** `POST /address` validates then
  303-redirects to `GET /address/review?<values>`; the review embeds the values as
  hidden inputs that the commit re-posts. Nothing server-side. Trade-off: the
  address transits the review URL (so it can appear in browser history) and the
  review HTML; it never persists server-side or in a cookie. A cookie would keep
  it out of the URL but store an address in the browser; a session store adds
  state we don't want.
- **Idempotent commit prevents double-write.** `update_member_address` always
  writes, so the route compares submitted vs current address and, if identical,
  redirects with `?nochange=1` ("No change to apply.") without writing. A stale
  re-submit therefore no-ops.
- **Confirmation is the form GET with `?ref`.** Keeps to three GETs / two POSTs and
  reuses the form as the landing.
- **Effective date** has no stored "current" column, so the review's CURRENT cell
  for it is an em dash; the date is recorded in the audit detail, not on `member`.
- **No JavaScript** in this feature (unlike card-maintenance, no confirm needed).

## AX contract added

Review grid — `/member/100101/address/review?...`:

```
- table "Address change review":
  - row:
    - columnheader "FIELD"
    - columnheader "CURRENT"
    - columnheader "NEW"
  - row: [cell "Address Line 1"] [cell "742 Evergreen Ter"] [cell "1 New St"]
  - row: [cell "Address Line 2"] [cell ""] [cell ""]
  - row: [cell "City"] [cell "Springfield"] [cell "Newtown"]
  - row: [cell "State"] [cell "OR"] [cell "OR"]
  - row: [cell "ZIP Code"] [cell "97403"] [cell "97001"]
  - row: [cell "Effective Date"] [cell "\u2014"] [cell "<MM/DD/YYYY>"]
```

Edit form — `/member/100101/address`:

```
- textbox "Address Line 1"
- textbox "Address Line 2"
- textbox "City"
- combobox "State"
- textbox "ZIP Code"
- textbox "Effective Date"
- button "Continue"     # "Next" on summit (cfg.btn_continue)
- link "Cancel"
```

## Audit rows this feature writes

| Action | When | detail |
|---|---|---|
| `ADDRESS_UPDATE` | commit applies a real change | `Address updated, effective <MM/DD/YYYY>` |

No audit row for a no-op commit (unchanged address), validation failure, or a
role/status refusal.

## Tests added

- `test_form_shows_current_and_fields` — Req 2.1, 2.2
- `test_validation_rule` (6 params) — Req 3.1–3.6
- `test_two_failures_show_together` — Req 3.7
- `test_values_survive_validation_failure` — Req 3.7
- `test_valid_reaches_review` — Req 3.8, 4.1
- `test_review_without_pending` — Req 4.3
- `test_commit_writes_and_refs` — Req 5.1
- `test_double_commit_does_not_double_write` — Req 5.2
- `test_teller_ro_refused_at_both_posts` — Req 1.4
- `test_restricted_member_refused` — Req 1.3
- `test_invariants_form` — Req 6.2, 6.3

## Known gaps

- The address transits the review URL/history (see decision above); acceptable for
  a mock, would need a signed-cookie or server token to avoid.
- Commit re-validates but the "stale review with now-invalid data" edge just
  re-renders the form with errors; there's no separate message for it.
