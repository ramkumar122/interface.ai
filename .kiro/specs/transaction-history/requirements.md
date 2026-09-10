# Requirements — Transaction History

## Introduction

A thin, read-only screen: `GET /member/{member_no}/transactions`. No write path.
Automates nothing on its own but exercises money parsing (parenthesised debits)
and paging for the agent.

## Requirement 1 — Access
1. WHEN requested without a session THE SYSTEM SHALL redirect to `/`.
2. WHEN the member does not exist THE SYSTEM SHALL render `Member record not found.`

## Requirement 2 — Search controls
1. THE SYSTEM SHALL render a share selector (`<select>` of the member's shares), a `From` and a `To` date input (`MM/DD/YYYY` text, never `type="date"`), and a `Search` button, submitted via `method="get"`.
2. THE SYSTEM SHALL default to the member's primary savings share and the last 30 days on a fresh load.

## Requirement 3 — Results grid
1. THE SYSTEM SHALL render a table `<caption>Transaction history</caption>` with headers `DATE | DESCRIPTION | TYPE | AMOUNT`.
2. THE SYSTEM SHALL render the amount right-aligned via an `align` attribute, with negative amounts in accounting parentheses, e.g. `(45.00)`, and positive amounts plain.
3. THE SYSTEM SHALL order rows most-recent-first.
4. THE SYSTEM SHALL page at 10 rows with `Previous` / `Next` links carrying an `offset` query param; `Previous` appears only when `offset > 0`, `Next` only when more rows remain.
5. WHEN no rows match THE SYSTEM SHALL render `No transactions found for the selected period.`
6. WHEN the From date is after the To date THE SYSTEM SHALL render `From date must be on or before To date.` and no rows.

## Requirement 4 — Rendering invariants
1. Full HTML only; no JSON; no JavaScript. 2. No `data-testid`. 3. Every visible input/select bound to `<label for>`. 4. `<caption>` + `<th>` on the grid; nested tables; WebForms ids.

## Fixtures
- `100101` checking (`SHR-100101-0070`) has 12 seeded rows (pages: 10 + 2).
- `100110` savings has 5 rows. A far-past range returns the empty message.
