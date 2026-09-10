# Design — Share Opening

Builds on `write-path.md`, card- and address-maintenance patterns.

## Route map

| Route | Method | Template | queries.py |
|---|---|---|---|
| `/member/{m}/shares/new` | GET | `share_new_form.html` | `get_member`, `list_shares` |
| `/member/{m}/shares/new` | POST | re-render or 303 → review | `get_member`, `list_shares`, `get_share`, `create_share_request` |
| `/member/{m}/shares/new/review` | GET | `share_new_review.html` | `get_share_request`, `get_share` |
| `/member/{m}/shares/new/commit` | POST | 303 → record or form | `get_share_request`, `commit_share_request`, `cancel_share_request` |

Replaces the `/member/{m}/shares/new` stub. All ≥3 segments.

## Type mapping

The `Share Type` `<select>` option values are the enum tokens with friendly labels:
`PRIMARY_SAVINGS`→"Regular Share", `MONEY_MARKET`→"Money Market",
`CERTIFICATE`→"Certificate 12mo". Duplicate-type check and `commit_share_request`'s
suffix logic both key off the enum.

## Data-layer addition

Add `cancel_share_request(request_id)` to `db/queries.py`: sets a `DRAFT` request to
`CANCELLED` (no audit — nothing was created). `create_share_request`,
`get_share_request`, and `commit_share_request` already exist from Task 2;
`commit_share_request` creates the share (suffix from type, bumped if taken),
marks `COMMITTED`, and audits `SHARE_OPEN` with the new `SHR-` id.

## POST validate order

gating (not-found → INACTIVE → RESTRICTED → TELLER_RO) → no OPEN funding shares →
share type required → deposit parse/positive (`display_to_cents`, catch `ValueError`,
`> 0`) → duplicate OPEN type → funding available < deposit → create DRAFT request →
303 to `review?req=SRQ-…`. First failing check renders a single message (no share,
no request created).

## Review + commit

`review` loads the DRAFT via `get_share_request(req)`; if missing/not DRAFT →
informational message. It looks up the funding share via `get_share` to display
`suffix - description - available`, shows the deposit via `cents_to_display`, the
spaced type, and the `SRQ-` id. The commit form has two submit buttons sharing
`name="op"`: `Open Account` (`value="open"`, `onclick="return confirm('Open this
account? This cannot be undone.')"`) and `Cancel Request` (`value="cancel"`, no
confirm). `commit`: `op=open` → guard the request is DRAFT (else redirect to record,
no double-open) → `commit_share_request` → 303 `/member/{m}`; `op=cancel` →
`cancel_share_request` → 303 form with an info note.

The `Open Account` `onclick` confirm is the second (and final) piece of JS in
CoreDesk, matching `tech.md`'s canonical example.

## AX contract

Review — `/member/100101/shares/new/review?req=SRQ-…`:

```
- table "New share account review":
  - row: [columnheader "FIELD"] [columnheader "VALUE"]
  - row: [cell "Share Type"] [cell "MONEY MARKET"]
  - row: [cell "Description"] [cell "Money Market"]
  - row: [cell "Initial Deposit"] [cell "100.00"]
  - row: [cell "Funding Share"] [cell "0000 - Regular Share - 12,845.50"]
  - row: [cell "Request"] [cell "SRQ-#####"]
- button "Open Account"
- button "Cancel Request"
```

Form — `combobox "Share Type"`, `textbox "Description"`, `textbox "Initial Deposit
Amount"`, `combobox "Funding Share"`, `button "Continue to Review"`.

## Audit rows

| Action | When | detail |
|---|---|---|
| `SHARE_OPEN` | Open Account commits a DRAFT | `Opened <TYPE> from <SRQ-id>` (reference = new SHR id) |

Cancel writes nothing.

## Files

Created: `share_new_form.html`, `share_new_review.html`, `tests/test_share_opening.py`,
`docs/implementation-notes/share-opening.md`. Modified: `coredesk/app.py` (four
routes, helpers; remove shares/new stub), `db/queries.py` (`cancel_share_request`).

## Tests

Function-scoped rebuild; `auth`/`auth_ro`. Review shows parsed values matching
inputs; each business outcome fires + creates no share; commit creates one share +
one `SHARE_OPEN`; new share appears in the panel; cancel sets `CANCELLED`, no share;
`Open Account` carries `confirm(`; INACTIVE/duplicate/insufficient/TELLER_RO gated.
