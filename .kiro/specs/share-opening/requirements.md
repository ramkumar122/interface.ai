# Requirements — Share Opening

## Introduction

The irreversible capability's UI: `coredesk.share.prepare_open`. Flow `GET form →
POST → GET review → POST commit (confirm()) → confirmation`. A human can complete
it; the automation agent reaches the review page and **stops** (its final click is
policy-blocked). The commit path is built fully. Reads with `write-path.md`.

Routes:
- `GET /member/{m}/shares/new` — form (replaces stub).
- `POST /member/{m}/shares/new` — validate → create DRAFT request → review.
- `GET /member/{m}/shares/new/review` — review with `SRQ-` reference.
- `POST /member/{m}/shares/new/commit` — Open Account (commit) or Cancel Request.

## Requirement 1 — Access and gating

1. WHEN any route is requested without a session THE SYSTEM SHALL redirect to `/`.
2. WHEN the member does not exist THE SYSTEM SHALL render `Member record not found.`
3. WHERE the member status is `INACTIVE` THE SYSTEM SHALL render `Member status is INACTIVE. Accounts cannot be opened.` and create no request.
4. WHERE the member status is `RESTRICTED` THE SYSTEM SHALL render `Member status is RESTRICTED. This function is unavailable.` (write-path gate).
5. WHERE the role is `TELLER_RO` THE SYSTEM SHALL refuse with `Your role does not permit this function.`

## Requirement 2 — Form (`GET /shares/new`)

1. THE SYSTEM SHALL render labelled inputs: `Share Type` (`<select>`: Regular Share, Money Market, Certificate 12mo), `Description`, `Initial Deposit Amount` (text, parsed via `display_to_cents`), `Funding Share` (`<select>` of the member's OPEN shares shown `suffix - description - available`).
2. THE SYSTEM SHALL render a `Continue to Review` button.

## Requirement 3 — Business outcomes (`POST /shares/new`)

Each renders its message and creates no share/request.

1. IF the member already has an OPEN share of the selected type THEN `Member already has a share of type <TYPE>.` (type spaced, e.g. `PRIMARY SAVINGS`).
2. IF the funding share's available balance < deposit THEN `Funding share has insufficient available balance.`
3. IF the deposit is unparseable or not positive THEN `Initial deposit must be a positive amount.`
4. IF the member has no OPEN share to fund from THEN `No eligible funding share on file.`
5. WHEN input is valid THE SYSTEM SHALL create a `DRAFT` share request and 303-redirect to the review URL.

## Requirement 4 — Review (`GET /shares/new/review`)

1. THE SYSTEM SHALL render `<caption>New share account review</caption>` showing every requested value in its parsed/reformatted form (deposit via `cents_to_display`, type spaced), not raw echoes.
2. THE SYSTEM SHALL issue and display an `SRQ-#####` request reference at this draft stage.
3. THE SYSTEM SHALL render `Open Account` (irreversible; `confirm()` — `Open this account? This cannot be undone.`) and `Cancel Request`.
4. WHEN the review is reached with no/invalid request THE SYSTEM SHALL render an informational message.

## Requirement 5 — Commit (`POST /shares/new/commit`)

1. WHEN `Open Account` is confirmed THE SYSTEM SHALL create the share, set the request `COMMITTED`, write one `SHARE_OPEN` audit row (reference = new `SHR-` id), and 303-redirect to the member record so the new share shows in the panel.
2. WHEN `Cancel Request` is chosen THE SYSTEM SHALL set the request `CANCELLED`, write no audit row, and return to the form.
3. THE `Open Account` control SHALL carry a `confirm()` handler; no other CoreDesk control except card Report-Lost-or-Stolen does.
4. WHEN a request is already committed/cancelled THE SYSTEM SHALL NOT open a second share (idempotent).

## Requirement 6 — Rendering invariants

1. Full HTML only; no JSON. 2. No JavaScript other than the `Open Account` `confirm()`.
3. No `data-testid`. 4. Every visible input/select has a `<label for>`.
5. `<caption>` on the review grid; nested-table layout; WebForms ids.

## Fixtures

- `100101` happy path (OPEN shares to fund). `100104` INACTIVE. `100105` already has
  PRIMARY SAVINGS. `100111` zero-balance funding → insufficient. `100109` has an OPEN
  savings (funding populated).
