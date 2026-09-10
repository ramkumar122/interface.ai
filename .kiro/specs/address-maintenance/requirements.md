# Requirements — Address Maintenance

## Introduction

The multi-field write with a review step: `GET form → POST → GET review → POST
commit → GET confirmation`. Automates `coredesk.address.update` (`guarded_write`).
Reads with `write-path.md`. Five typed inputs, a real review round-trip, and
field-level validation as a business outcome.

Routes:
- `GET /member/{member_no}/address` — edit form (also the confirmation target).
- `POST /member/{member_no}/address` — validate → review or re-render.
- `GET /member/{member_no}/address/review` — side-by-side review.
- `POST /member/{member_no}/address/commit` — write, audit, redirect.

## Requirement 1 — Access and gating

1. WHEN any address route is requested without a session THE SYSTEM SHALL redirect to `/`.
2. WHEN the member does not exist THE SYSTEM SHALL render `Member record not found.` without an exception.
3. WHERE the member status is `RESTRICTED` THE SYSTEM SHALL render `Member status is RESTRICTED. This function is unavailable.` and refuse both POST steps (no write, no audit).
4. WHERE the role is `TELLER_RO` THE SYSTEM SHALL refuse both POST steps with `Your role does not permit this function.` (no write, no audit).

## Requirement 2 — Edit form (`GET /address`)

1. THE SYSTEM SHALL show the member's current address read-only above the form.
2. THE SYSTEM SHALL render labelled inputs: `Address Line 1`, `Address Line 2` (optional), `City`, `State` (a `<select>` of the 50 two-letter codes), `ZIP Code`, `Effective Date` (text `MM/DD/YYYY`, never `type="date"`).
3. THE SYSTEM SHALL render `Continue` (tenant `btn_continue`) and `Cancel`.
4. THE SYSTEM SHALL prefill the editable inputs with the current address on a fresh load.

## Requirement 3 — Validation (`POST /address`)

Each rule renders its own message beside the field; multiple failures show together.

1. IF line 1 is empty THEN `Address line 1 is required.`
2. IF city is empty THEN `City is required.`
3. IF state is not selected THEN `State is required.`
4. IF ZIP is not 5 or 9 digits THEN `ZIP code must be 5 or 9 digits.`
5. IF the effective date is unparseable THEN `Effective date must be MM/DD/YYYY.`
6. IF the effective date is in the past THEN `Effective date cannot be in the past.`
7. WHEN validation fails THE SYSTEM SHALL re-render the form (200) with all messages and the submitted values re-filled, and write nothing.
8. WHEN validation passes THE SYSTEM SHALL 303-redirect to the review URL carrying the new values.

## Requirement 4 — Review (`GET /address/review`)

1. THE SYSTEM SHALL render a side-by-side table `<caption>Address change review</caption>` with columns `FIELD | CURRENT | NEW`, one row per field.
2. THE SYSTEM SHALL render `Confirm Update` and `Back to Edit`.
3. WHEN the review is reached with no pending change THE SYSTEM SHALL render `No pending address change. Return to the address form.`

## Requirement 5 — Commit (`POST /address/commit`)

1. WHEN the submitted new address differs from the current address THE SYSTEM SHALL update the member, write exactly one `ADDRESS_UPDATE` audit row, and 303-redirect to the form with `?ref=ADR-#####` and a confirmation banner.
2. WHEN the submitted new address equals the current address (e.g. a stale re-submit) THE SYSTEM SHALL NOT write and SHALL NOT add an audit row (idempotent; prevents double-write).

## Requirement 6 — Rendering invariants

1. Full HTML only; no JSON; no JavaScript in this feature.
2. No `data-testid`.
3. Every visible input/select bound to a matching `<label for>`.
4. Nested-table layout, WebForms ids, `<caption>` on the review grid.

## Fixtures

- `100101` happy path (has a current address).
- Any member for validation rules (100101).
- TELLER_RO via `jtran`; RESTRICTED via `100108`.

## Design question (answered in design.md)

Where the pending change lives between the two POSTs — chosen: **hidden-field
re-post** (values ride the review URL then re-post as hidden fields; nothing
server-side). Trade-off + PII note in design.
