# Requirements — Card Maintenance

## Introduction

Card Maintenance is the first **write** feature in CoreDesk and the UI the
`coredesk.card.lock` capability (risk class `guarded_write`) is automated
against. It is where the write path is settled: post/redirect/get, a visible
reference, one audit row per applied change, the reversible/irreversible split,
and refusals rendered as business outcomes rather than crashes. It reads
alongside `write-path.md`, which these criteria assume.

Two screens plus a POST:

1. `GET /member/{member_no}/cards` — the card list (replaces the current stub).
2. `GET /member/{member_no}/cards/{card_id}/maint` — read-only detail + an action form.
3. `POST /member/{member_no}/cards/{card_id}/maint` — validate, apply, audit, redirect.

Card status values are `ACTIVE`, `LOCKED`, `HOTLISTED`, `EXPIRED` (from the
schema). The three operator actions are **Lock** (→ `LOCKED`), **Unlock**
(→ `ACTIVE`), and **Report Lost or Stolen** (→ `HOTLISTED`, irreversible).

## Requirement 1 — Authenticated access and function gating

**User story:** As the servicing platform, I want the card function guarded by
session, role, and member status, so that only permitted operators reach it and
restricted members are protected.

#### Acceptance criteria

1. WHEN any card route is requested without a valid session THE SYSTEM SHALL redirect to `/`.
2. WHEN the member number does not exist THE SYSTEM SHALL render `Member record not found.` and SHALL NOT raise an unhandled exception.
3. WHERE the member status is `RESTRICTED` THE SYSTEM SHALL render `Member status is RESTRICTED. This function is unavailable.` on the card list and maintenance screens, and SHALL refuse the POST, making no state change and writing no audit row.
4. WHERE the signed-in role is `TELLER_RO` THE SYSTEM SHALL refuse the POST with `Your role does not permit this function.`, making no state change and writing no audit row.
5. WHERE the signed-in role is `TELLER_RO` THE SYSTEM MAY still render the read-only card list and maintenance detail (a read-only role may look, not change).

## Requirement 2 — Card list (`GET /member/{member_no}/cards`)

**User story:** As a servicing agent, I want to see a member's cards, so that I
can pick the right one to maintain.

#### Acceptance criteria

1. WHEN an existing member's card list is requested THE SYSTEM SHALL render a page whose heading is `CARD MAINTENANCE`, showing the member number and name for context.
2. THE SYSTEM SHALL render a table `id="ctl00_MainContent_grdCards"` with `<caption>Cards on file</caption>` and column headers, from tenant config, of `CARD | NETWORK | LAST 4 | STATUS | LINKED` followed by an unlabelled action column.
3. THE SYSTEM SHALL render the network with spaces, not underscores (e.g. `VISA DEBIT`).
4. THE SYSTEM SHALL render the last four digits masked as `****NNNN` (e.g. `****4417`) and SHALL NEVER render a bare four-digit card number.
5. THE SYSTEM SHALL render, in each row's action cell, an `<a>` whose visible text is `MAINT`, with a WebForms-style id including the row index, targeting that card's maintenance screen.
6. WHEN the member has no cards THE SYSTEM SHALL render `No cards on file for this member.`
7. THE SYSTEM SHALL order card rows by `card_id` ascending and render one row per card (a member with two cards shows two rows).
8. THE SYSTEM SHALL render a `Return to Member Record` link and a `Return to Inquiry` link.

## Requirement 3 — Card maintenance screen (`GET .../maint`)

**User story:** As a servicing agent, I want a maintenance screen for one card,
so that I can choose an action, give a reason, and apply it.

#### Acceptance criteria

1. WHEN the maintenance screen is requested for a card belonging to the member THE SYSTEM SHALL render a read-only detail table containing: card id, network (with spaces), masked last four, current status, linked share suffix, and issued date (`MM/DD/YYYY`).
2. WHEN the `card_id` does not belong to the member THE SYSTEM SHALL render `Card not found.` and SHALL NOT raise an unhandled exception.
3. THE SYSTEM SHALL render an action radio group labelled `Action` with exactly three options, each with a real `<label for>`: `Lock`, `Unlock`, `Report Lost or Stolen`.
4. THE SYSTEM SHALL render a required `Reason` `<select>`, labelled, with options `Member request`, `Suspected fraud`, `Card misplaced`, `Other`.
5. THE SYSTEM SHALL render an optional `Notes` `<textarea>`, labelled, with `maxlength="200"`.
6. THE SYSTEM SHALL render `Apply` and `Cancel` controls; `Cancel` returns to the card list without changing anything.
7. WHERE the `Report Lost or Stolen` action is selected THE SYSTEM SHALL trigger a `confirm()` dialog reading `Report this card lost or stolen? This cannot be undone.` before submitting; WHERE `Lock` or `Unlock` is selected THE SYSTEM SHALL NOT trigger a dialog.
8. THE SYSTEM SHALL render all three action options with correct accessible names even when the expectation is that the irreversible option is declined.

## Requirement 4 — Apply an action (`POST .../maint`)

**User story:** As a servicing agent, I want my action applied, audited, and
confirmed, so that the change is recorded and I can see it took effect.

#### Acceptance criteria

1. THE SYSTEM SHALL evaluate POST outcomes in this precedence (first match wins): member-not-found → member `RESTRICTED` → role `TELLER_RO` → card-not-found → card `HOTLISTED` (immutable) → card `EXPIRED` (immutable) → reason-required → no-op (already in target state) → apply.
2. WHEN a reason is not selected THE SYSTEM SHALL re-render the maintenance form with `Reason is required.` and re-fill the submitted action/notes, applying no change.
3. WHEN `Lock` is applied to an `ACTIVE` card THE SYSTEM SHALL set the status to `LOCKED`, write exactly one audit row with action `CARD_LOCK`, and 303-redirect to the maintenance GET with `?ref=CRD-#####`.
4. WHEN `Unlock` is applied to a `LOCKED` card THE SYSTEM SHALL set the status to `ACTIVE`, write one audit row with action `CARD_UNLOCK`, and redirect with a reference.
5. WHEN `Report Lost or Stolen` is applied to an `ACTIVE` or `LOCKED` card THE SYSTEM SHALL set the status to `HOTLISTED`, write one audit row with action `CARD_HOTLIST`, and redirect with a reference.
6. WHEN a change is applied THE SYSTEM SHALL render a confirmation banner containing the reference on the redirect target (never in the POST response body).
7. WHEN an operation applies to exactly the addressed `card_id` THE SYSTEM SHALL leave the member's other cards unchanged.

## Requirement 5 — Informational (no-change) outcomes

**User story:** As a servicing agent, I want "nothing to do" cases to tell me so
clearly, so that I don't mistake a no-op for a failure — and the system must not
record a change that didn't happen.

#### Acceptance criteria

1. WHEN `Lock` is requested on an already-`LOCKED` card THE SYSTEM SHALL render `Card is already locked. No change applied.`, leave the status unchanged, and write **no** audit row.
2. WHEN `Unlock` is requested on an already-`ACTIVE` card THE SYSTEM SHALL render `Card is already active. No change applied.`, leave the status unchanged, and write **no** audit row.
3. WHEN any action is requested on a `HOTLISTED` card THE SYSTEM SHALL render `Card is hotlisted and cannot be modified.` and write no audit row.
4. WHEN any action is requested on an `EXPIRED` card THE SYSTEM SHALL render `Card is expired and cannot be modified.` and write no audit row.

## Requirement 6 — Data protection

**User story:** As the platform, I want card numbers and free-text notes kept out
of anything that could leak, so that a naive reader or a log grep never sees them.

#### Acceptance criteria

1. THE SYSTEM SHALL render the last four digits only, always masked as `****NNNN`, on every screen; there is no full card number anywhere in the data or output.
2. THE SYSTEM SHALL NOT write a submitted `Notes` value verbatim into `audit_log.detail`; the detail SHALL record the (controlled) reason and at most a redacted indicator that a note was present.
3. THE SYSTEM SHALL NOT emit an unmasked four-digit card-number sequence in the rendered HTML.

## Requirement 7 — Rendering invariants and JavaScript scope

**User story:** As the automation platform, I want the write screens to keep the
structural affordances and the no-JS rule (except the one specified dialog), so
locator and safety tests hold.

#### Acceptance criteria

1. THE SYSTEM SHALL render every route in this feature as full HTML and SHALL expose no JSON endpoint.
2. THE SYSTEM SHALL add no JavaScript other than the specified `confirm()` dialog scoped to the `Report Lost or Stolen` action.
3. THE SYSTEM SHALL render no `data-testid` attribute on any route in this feature.
4. THE SYSTEM SHALL bind every visible `<input>`, `<select>`, and `<textarea>` to a matching `<label for>`.
5. THE SYSTEM SHALL source the card-list column headers from tenant configuration and render the data table with real `<th>` headers and a `<caption>`, with nested-table layout and WebForms-style ids.

## Requirement 8 — Fixture-driven guarantees

**User story:** As the evidence-run author, I want specific seeded cards to behave
exactly as the fixture contract expects.

#### Acceptance criteria

1. WHEN member `100101`'s card (`****4417`, `ACTIVE`) is locked THE SYSTEM SHALL move it to `LOCKED` with one `CARD_LOCK` row and a `CRD-` reference (the happy path).
2. WHEN member `100103`'s already-`LOCKED` card is locked THE SYSTEM SHALL render the already-locked message and write no audit row.
3. WHEN member `100109` is opened THE SYSTEM SHALL render `No cards on file for this member.`
4. WHEN member `100112` is opened THE SYSTEM SHALL render two card rows, and maintaining one SHALL leave the other unchanged.
5. WHEN member `100113`'s `EXPIRED` card is actioned THE SYSTEM SHALL render the expired message and write no audit row.
6. WHERE the member is `100108` (`RESTRICTED`) THE SYSTEM SHALL render the RESTRICTED message and refuse the function.

## Assumptions and open questions

Decisions where the spec left room; please confirm or correct at this gate.

1. **TELLER_RO can view, not write.** I let a read-only teller open the list and
   the maintenance detail, and refuse only at POST (Req 1.4–1.5). Alternative:
   block the maint screen entirely for `TELLER_RO`. The test only asserts the POST
   refusal. (Spec 5's `readonly_role` will later disable the write buttons with an
   explanation; I'm not doing that here.)
2. **`Card not found.`** The outcome table doesn't list a message for a `card_id`
   that isn't the member's, so I added `Card not found.` (Req 3.2, 4.1) rather than
   crash. Say the word if you want it folded into `Member record not found.`
3. **Card-list headers are the same for both tenants.** The spec gives one header
   set and, unlike the results/shares grids, no Summit variant. I'll put
   `cards_columns` in tenant config (so it's config-sourced) but with identical
   labels for now. Easy to diverge later if you want the heterogeneity here too.
4. **First `confirm()` in the app.** `tech.md` says the only JS is the Open
   Account `confirm()`, but this spec explicitly adds `confirm()` to `Report Lost
   or Stolen`. `write-path.md` already reflects "exactly two." Implemented as a
   single inline handler scoped to the hotlist action (fires only when it's
   selected). Flagging the `tech.md` wording lag.
5. **Notes redaction shape.** I'll store `reason=<value>` (a controlled enum) in
   `audit_log.detail` and, if a note was entered, a non-revealing marker like
   `note=Y` — never the note text. This requires refining `set_card_status`'s
   detail construction (it currently joins reason+notes). Finalized in design.
6. **`LINKED` value.** I'll render the linked share's suffix (e.g. `0070`), or `—`
   when a card has no linked share (the seeded Visa credit card). Confirmed in
   design.
