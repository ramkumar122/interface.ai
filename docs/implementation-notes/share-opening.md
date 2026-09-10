# Implementation notes — share-opening

## What was built

The irreversible capability's UI. Routes: `GET /member/{m}/shares/new` (form,
replacing the stub), `POST /member/{m}/shares/new` (validate → create a DRAFT
`share_request` → review), `GET /member/{m}/shares/new/review` (review with an
`SRQ-` reference), `POST /member/{m}/shares/new/commit` (Open Account or Cancel
Request). A human can open a share end-to-end; the automation agent is meant to
reach the review page and stop (its Open Account click is policy-blocked later).

## Files created / modified

| Path | Created/Modified | Reason |
|---|---|---|
| `coredesk/templates/share_new_form.html` | created | share-open form + business-outcome messages |
| `coredesk/templates/share_new_review.html` | created | review grid + Open Account (confirm) / Cancel Request |
| `tests/test_share_opening.py` | created | feature tests (11) |
| `docs/implementation-notes/share-opening.md` | created | this record |
| `coredesk/app.py` | modified | four routes, type map + funding helpers; removed shares/new stub |
| `db/queries.py` | modified | added `cancel_share_request` |

## Decisions I made

- **Share Type select values are enum tokens** with friendly labels
  (`PRIMARY_SAVINGS`→"Regular Share", `MONEY_MARKET`→"Money Market",
  `CERTIFICATE`→"Certificate 12mo"). Duplicate check and suffix logic key off the
  enum.
- **A DRAFT request is created at the review step** and carries the `SRQ-`
  reference, as specified. Each Continue-to-Review makes a new DRAFT (acceptable).
- **RESTRICTED and TELLER_RO refusals added** (per `write-path.md`), beyond the
  spec's explicit INACTIVE/duplicate/funding outcomes.
- **Idempotent commit.** `commit`/`cancel` first check the request is still DRAFT;
  a second submit (already committed/cancelled) redirects to the record without
  opening a second share.
- **Description defaults** to the type label when left blank.
- **Second and final `confirm()`** in CoreDesk: an `onclick` on the Open Account
  button only (`Open this account? This cannot be undone.`). Cancel Request has no
  dialog.
- **Commit does not deduct the deposit from the funding share** (mock; funding is a
  validation gate only).

## AX contract added

Review — `/member/100101/shares/new/review?req=SRQ-…`:

```
- table "New share account review":
  - row: [columnheader "FIELD"] [columnheader "VALUE"]
  - row: [cell "Share Type"] [cell "MONEY MARKET"]
  - row: [cell "Description"] [cell "Money Market"]
  - row: [cell "Initial Deposit"] [cell "1,000.00"]
  - row: [cell "Funding Share"] [cell "0000 - Regular Share - 12,845.50"]
  - row: [cell "Request"] [cell "SRQ-#####"]
- button "Open Account"     # onclick confirm()
- button "Cancel Request"
```

Form — `combobox "Share Type"`, `textbox "Description"`, `textbox "Initial Deposit
Amount"`, `combobox "Funding Share"`, `button "Continue to Review"`, `link "Cancel"`.

## Audit rows this feature writes

| Action | When | detail |
|---|---|---|
| `SHARE_OPEN` | Open Account commits a DRAFT | `Opened <TYPE> from <SRQ-id>` (reference = new `SHR-` id) |

Cancel Request writes nothing.

## Tests added

- `test_form_lists_types_and_funding` — Req 2.1
- `test_inactive_member_refused` — Req 1.3
- `test_duplicate_type_refused` — Req 3.1
- `test_insufficient_funding_refused` — Req 3.2
- `test_deposit_must_be_positive` — Req 3.3
- `test_review_shows_parsed_values` — Req 4.1, 4.2
- `test_open_account_carries_confirm` — Req 4.3, 5.3
- `test_commit_creates_share_and_audits` — Req 5.1
- `test_cancel_sets_cancelled_and_creates_no_share` — Req 5.2
- `test_teller_ro_refused` — Req 1.5
- `test_invariants_form` — Req 6.3, 6.4

## Known gaps

- "No eligible funding share on file." is defensive; every active fixture member
  has an OPEN savings, so it isn't exercised by a fixture (only by the code path).
- The deposit isn't moved out of the funding share on commit (mock).
