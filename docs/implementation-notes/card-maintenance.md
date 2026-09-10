# Implementation notes — card-maintenance

## What was built

The first write feature. Routes added: `GET /member/{member_no}/cards` (card list,
replacing the stub), `GET /member/{member_no}/cards/{card_id}/maint` (read-only
detail + action form), and `POST` on the same maint URL (apply). A signed-in
operator can now list a member's cards (masked last-4), open a card, and Lock /
Unlock / Report Lost or Stolen it, with a reason and optional notes. Applied
changes audit and redirect to a confirmation banner; no-ops and refusals render a
message and change nothing.

## Files created / modified

| Path | Created/Modified | Reason |
|---|---|---|
| `coredesk/templates/cards_list.html` | created | card list grid + no-cards + return links |
| `coredesk/templates/card_maint.html` | created | detail + action form, scoped confirm(), banners |
| `tests/test_card_maintenance.py` | created | feature tests (13) |
| `docs/implementation-notes/card-maintenance.md` | created | this record |
| `coredesk/app.py` | modified | list + maint GET/POST routes, `_mask_last4`/`_linked_suffix`; removed cards stub |
| `coredesk/config.py` | modified | `cards_columns` per tenant |
| `db/queries.py` | modified | redact `set_card_status` `detail` (reason + note marker, never note text) |
| `coredesk/static/coredesk.css` | modified | `.confirm` / `.notice` banner styles |

## Decisions I made

- **TELLER_RO can view, refused at POST.** A read-only teller can open the list and
  maint detail; only the POST is refused. (Spec 5's `readonly_role` will disable
  the write buttons; not done here.)
- **`Card not found.`** added for a `card_id` that isn't the member's (the spec's
  outcome table had no message for it) rather than crashing.
- **`cards_columns` identical across tenants.** The spec gave one header set and no
  Summit variant; config-sourced but same labels for both tenants.
- **First `confirm()` in the app.** Implemented as one inline `onsubmit` handler
  scoped to the `HOTLIST` radio (fires only when Report Lost or Stolen is selected).
  `tech.md` still says "only Open Account"; `write-path.md` already reflects "two".
- **Notes redaction.** `set_card_status` now writes `detail = "reason=<value>"`
  plus `"; note=Y"` when a note was entered — never the note text.
- **`LINKED`** renders the linked share's suffix (e.g. `0070`) or an em dash for a
  card with no linked share (the seeded Visa credit card).
- **Only success uses PRG.** No-op / refusal / validation outcomes re-render the
  maint screen at 200; only an applied change redirects with `?ref=`.

## AX contract added

Card list — `/member/100101/cards` (identical headers both tenants):

```
- table "Cards on file":
  - row:
    - columnheader "CARD"
    - columnheader "NETWORK"
    - columnheader "LAST 4"
    - columnheader "STATUS"
    - columnheader "LINKED"
    - columnheader ""
  - row:
    - cell "CRD-100101-1"
    - cell "VISA DEBIT"
    - cell "****4417"
    - cell "ACTIVE"
    - cell "0070"
    - cell:
      - link "MAINT"
```

Card maintenance — `/member/100101/cards/CRD-100101-1/maint`:

```
- heading "CARD MAINTENANCE" [level=1]
- table            # read-only detail (label/value)
- group "Action":
  - radio "Lock"
  - radio "Unlock"
  - radio "Report Lost or Stolen"
- combobox "Reason"
- textbox "Notes"
- button "Apply"
- link "Cancel"
```

## Audit rows this feature writes

| Action code | When it fires | detail |
|---|---|---|
| `CARD_LOCK` | Lock applied to an ACTIVE card | `reason=<value>` (+ `; note=Y`) |
| `CARD_UNLOCK` | Unlock applied to a LOCKED card | `reason=<value>` (+ `; note=Y`) |
| `CARD_HOTLIST` | Report Lost or Stolen applied to ACTIVE/LOCKED | `reason=<value>` (+ `; note=Y`) |

No audit row is written for no-op (already locked/active), immutable
(hotlisted/expired), permission/status refusal, or validation failure.

## Tests added

- `test_card_list_caption_headers_and_mask` — Req 2.2, 2.4
- `test_no_cards_message` — Req 2.6, 8.3
- `test_two_cards_listed_and_isolated` — Req 4.7, 8.4
- `test_lock_active_card_happy_path` — Req 4.3, 8.1
- `test_lock_already_locked_writes_nothing` — Req 5.1, 8.2
- `test_hotlist_then_immutable` — Req 4.5, 5.3
- `test_expired_card_refused` — Req 5.4, 8.5
- `test_teller_ro_refused` — Req 1.4
- `test_restricted_member_blocked` — Req 1.3, 8.6
- `test_reason_required` — Req 4.2
- `test_notes_never_stored_verbatim` — Req 6.2
- `test_no_unmasked_pan_in_html` — Req 6.3
- `test_invariants_list_and_maint` — Req 7.3, 7.4

## Known gaps

- The `readonly_role` UX (disabled write buttons with an explanation) is not here;
  it arrives with the runtime-conditions spec. Today TELLER_RO is refused at POST.
- The maint form's `<select>`/`<textarea>` labels aren't verified by the shared
  helper (it checks `<input>` only); they are present but unasserted.
