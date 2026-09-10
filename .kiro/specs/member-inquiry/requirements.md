# Requirements — Member Inquiry

## Introduction

Member Inquiry is the first operator-facing feature of CoreDesk and the UI that
the "read a member's savings balance" capability (risk class `safe`) will later
be automated against. It provides three server-rendered screens plus a shares
panel:

1. A search-criteria screen (`GET /mbrinq`).
2. A results grid (`GET /mbrinq/results`), reached by a `GET` form so results are
   bookmarkable.
3. A member record (`GET /member/{member_no}`) that embeds
4. a shares panel (`GET /member/{member_no}/shares`) inside an `<iframe>`.

The feature is read-only. It adds no card, address, or share-opening screens;
those are later specs. The tenant-heterogeneity choices here (different labels,
different action-link text, and a different results-grid column order) are
deliberate: they make a position-based locator read the wrong cell, which is the
condition the automation's locator strategy must survive.

Terminology: "session" means the signed staff cookie established at sign-on;
"tenant" is `riverbend` or `summit`; balances are rendered from integer cents.

## Requirement 1 — Authenticated access to every route

**User story:** As the servicing platform, I want every Member Inquiry route to
require an active staff session, so that no member data is exposed to an
unauthenticated caller.

#### Acceptance criteria

1. WHEN `GET /mbrinq` is requested without a valid session THE SYSTEM SHALL respond with a redirect to `/`.
2. WHEN `GET /mbrinq/results` is requested without a valid session THE SYSTEM SHALL respond with a redirect to `/`.
3. WHEN `GET /member/{member_no}` is requested without a valid session THE SYSTEM SHALL respond with a redirect to `/`.
4. WHEN `GET /member/{member_no}/shares` is requested without a valid session THE SYSTEM SHALL respond with a redirect to `/`.

_Note: the redirect status is `303`, kept consistent with the existing Task 1
session guards. `302`/`307` would be equally acceptable and are invisible to the
agent._

## Requirement 2 — Search-criteria screen (`GET /mbrinq`)

**User story:** As a servicing agent, I want a search screen, so that I can look
up a member by number or by last name.

#### Acceptance criteria

1. WHEN an authenticated user requests `GET /mbrinq` THE SYSTEM SHALL render a page whose heading is `MEMBER INQUIRY` and which shows the instruction line `Enter a member number or last name and press Search.`
2. THE SYSTEM SHALL render a bordered criteria table containing a labelled member-number input, a labelled `Last Name` input, and a `Search` button.
3. WHERE the tenant is `riverbend` THE SYSTEM SHALL label the member-number input `Member Number`.
4. WHERE the tenant is `summit` THE SYSTEM SHALL label the member-number input `Member ID`.
5. THE SYSTEM SHALL render a `Return to Menu` link on the criteria screen.
6. THE SYSTEM SHALL submit the search form with `method="get"` targeting `/mbrinq/results`, passing `member_no` and `last_name` as query-string parameters.
7. WHEN the criteria screen is requested with `member_no` and/or `last_name` query parameters present THE SYSTEM SHALL pre-fill the corresponding inputs with those values.
8. WHEN a user selects the Member Inquiry menu code or enters it in the main-menu fast-path box THE SYSTEM SHALL navigate to `GET /mbrinq`.
9. WHEN `GET /mbrinq` is requested with a `fn` query parameter naming a member sub-function THE SYSTEM SHALL render the heading `MEMBER INQUIRY - <FUNCTION>` (the function's description, uppercased) and carry `fn` through the search form as a hidden input.
10. WHEN `fn` is absent or unrecognized THE SYSTEM SHALL render the heading `MEMBER INQUIRY` with no function context.

## Requirement 3 — Empty-criteria validation

**User story:** As a servicing agent, I want a clear message when I search with
no criteria, so that I am not shown a meaningless empty grid.

#### Acceptance criteria

1. WHEN `GET /mbrinq/results` is requested and both `member_no` and `last_name` are empty or absent THE SYSTEM SHALL render the Member Inquiry criteria screen showing the message `Enter a member number or last name.` in red.
2. WHEN criteria validation fails THE SYSTEM SHALL NOT perform a member search.

## Requirement 4 — Results grid (`GET /mbrinq/results`)

**User story:** As a servicing agent, I want a results grid, so that I can
identify and select the correct member.

#### Search semantics

1. THE SYSTEM SHALL trim leading and trailing whitespace from both `member_no` and `last_name` before searching.
2. THE SYSTEM SHALL match `member_no` exactly and SHALL NOT prefix-match it.
3. WHEN a `last_name` value is submitted THE SYSTEM SHALL match members whose last name begins with that value, case-insensitively, and SHALL NOT match on first name.
4. WHEN both `member_no` and `last_name` are supplied THE SYSTEM SHALL treat `member_no` as authoritative and ignore `last_name`.
5. WHEN `member_no` contains non-digit characters THE SYSTEM SHALL perform the search normally, render the not-found message, and SHALL NOT raise an error.

#### Rendering

6. THE SYSTEM SHALL render a page whose heading is `MEMBER INQUIRY - RESULTS`.
7. THE SYSTEM SHALL give the results table an accessible name via a `<caption>` reading `Member search results`.
8. WHEN a `member_no` exactly matches a member THE SYSTEM SHALL render exactly one result row and SHALL NOT auto-redirect to the member record.
9. WHEN a search matches no member THE SYSTEM SHALL render the grid header row plus a single full-width cell containing `No records found for the specified criteria.`, and SHALL NOT hide the table.
10. WHEN a `last_name` matches one or more members THE SYSTEM SHALL render one row per matching member.
11. THE SYSTEM SHALL render all results ordered by member number ascending.
12. THE SYSTEM SHALL render the match count line `N record(s) found.` above the grid, where `N` is the number of matched rows, including `0`.
13. THE SYSTEM SHALL render each member name as `LAST, FIRST` in uppercase (e.g. `NAKAMURA, ALICE`).
14. THE SYSTEM SHALL render each date value in `MM/DD/YYYY` format.
15. THE SYSTEM SHALL render, in each row's final action cell, an `<a>` whose visible text is the tenant `link_select` value and whose target is `GET /member/{member_no}`.
16. WHERE the tenant is `riverbend` the action-link text SHALL be `SEL`; WHERE the tenant is `summit` it SHALL be `VIEW`.
17. THE SYSTEM SHALL give each result-row action link a WebForms-style id that includes the row index, of the form `ctl00_MainContent_grdMembers_ctl<NN>_lnkSel`.
18. WHERE the tenant is `riverbend` THE SYSTEM SHALL order the grid columns as `MBR NO | NAME | STATUS | JOINED | BRANCH` followed by the unlabelled action column.
19. WHERE the tenant is `summit` THE SYSTEM SHALL order the grid columns as `MBR NO | NAME | STATUS | BRANCH | JOINED` followed by the unlabelled action column (i.e. `BRANCH` appears before `JOINED`).
20. THE SYSTEM SHALL render a `Return to Inquiry` link that carries the submitted `member_no`/`last_name` (and `fn`, when in effect) back to the criteria screen so the prior criteria are preserved.
21. WHEN a `fn` naming a member sub-function is in effect THE SYSTEM SHALL target each row's action link at the corresponding member-scoped route (`CARD`→`/member/{member_no}/cards`, `ADDR`→`/member/{member_no}/address`, `SHARE_NEW`→`/member/{member_no}/shares/new`, `TXN`→`/member/{member_no}/transactions`); WHEN no `fn` is in effect the action link SHALL target `/member/{member_no}`.

## Requirement 5 — Member record (`GET /member/{member_no}`)

**User story:** As a servicing agent, I want a member record page, so that I can
confirm identity and navigate to member sub-functions.

#### Acceptance criteria

1. WHEN `GET /member/{member_no}` is requested for an existing member THE SYSTEM SHALL render a page whose heading is `MEMBER RECORD`.
2. THE SYSTEM SHALL render a two-column label/value table containing exactly these rows: `Member Number`, `Name`, `Status`, `Joined`, `Branch`, `Phone`.
3. THE SYSTEM SHALL render the `Name` value as `LAST, FIRST` in uppercase, consistent with the results grid.
4. THE SYSTEM SHALL NOT display date of birth, email, or the full mailing address on the member record page.
5. WHEN `GET /member/{member_no}` is requested for a member number that does not exist THE SYSTEM SHALL render a page containing `Member record not found.` and a `Return to Inquiry` link, and SHALL NOT raise an unhandled exception.
6. THE SYSTEM SHALL render a navigation strip containing the links `Shares`, `Cards`, `Address`, `Transactions`, and `Return to Inquiry`.
7. THE SYSTEM SHALL make the `Shares` link a same-page anchor targeting the embedded shares panel (`#shares-panel`), not a link to the standalone shares route.
8. THE SYSTEM SHALL point the `Cards`, `Address`, and `Transactions` links at member-scoped not-implemented stub routes (`/member/{member_no}/cards`, `/member/{member_no}/address`, `/member/{member_no}/transactions`), carrying the current `member_no`.
9. IF the member status is `INACTIVE` or `RESTRICTED` THEN THE SYSTEM SHALL render a red status banner reading `Member status is <STATUS>. Some functions are unavailable.` where `<STATUS>` is the member's status.
10. THE SYSTEM SHALL embed the shares panel as an `<iframe>` whose `src` is `/member/{member_no}/shares`, carrying an `id="shares-panel"` anchor target, a `title` attribute, a height of approximately 220px, and a 1px border.

## Requirement 6 — Shares panel (`GET /member/{member_no}/shares`)

**User story:** As a servicing agent, I want the member's share accounts listed
in the embedded panel, so that I can read balances and account status.

#### Acceptance criteria

1. WHEN `GET /member/{member_no}/shares` is requested for an existing member THE SYSTEM SHALL render a standalone HTML page without the shared `base.html` chrome, suitable for embedding in an iframe.
2. WHEN `GET /member/{member_no}/shares` is requested for a member number that does not exist THE SYSTEM SHALL render `Member record not found.` within the panel and SHALL NOT raise an unhandled exception.
3. THE SYSTEM SHALL give the shares table an accessible name via a `<caption>` reading `Share accounts`.
4. WHERE the tenant is `riverbend` THE SYSTEM SHALL render the panel heading `Shares`; WHERE the tenant is `summit` THE SYSTEM SHALL render `Savings Accounts` (from the tenant shares label).
5. WHERE the tenant is `riverbend` THE SYSTEM SHALL render the column headers `SUFFIX | TYPE | DESCRIPTION | AVAILABLE | LEDGER | STATUS`.
6. WHERE the tenant is `summit` THE SYSTEM SHALL render the column headers `ACCT | TYPE | DESCRIPTION | AVAIL BAL | LEDGER BAL | STATUS`.
7. THE SYSTEM SHALL render each share's type with spaces rather than underscores (e.g. `PRIMARY SAVINGS`).
8. THE SYSTEM SHALL render the available and ledger balances comma-grouped, to two decimals, with no currency symbol, produced by `cents_to_display`, right-aligned via an `align="right"` attribute on the cell (not a CSS class).
9. WHEN a balance is zero THE SYSTEM SHALL render it as `0.00` and never as blank.
10. THE SYSTEM SHALL render the available and ledger balances as independent values so that a share whose available and ledger differ shows both distinct amounts.
11. THE SYSTEM SHALL order share rows by suffix ascending.
12. WHERE a share's status is not `OPEN` THE SYSTEM SHALL render its real status (`FROZEN` or `CLOSED`) in the STATUS column and render that row in grey italic.
13. WHEN the member has no share accounts THE SYSTEM SHALL render `No share accounts on file.`

## Requirement 7 — Tenant heterogeneity is observable

**User story:** As the automation author, I want the two tenants to differ in
label, action text, and column order, so that a position-based locator provably
reads the wrong cell and the locator strategy is forced to use column names.

#### Acceptance criteria

1. WHERE the tenant is `summit` THE SYSTEM SHALL render the member-number search label as `Member ID`, the results action-link text as `VIEW`, and the shares available-balance header as `AVAIL BAL`.
2. WHERE the tenant is `riverbend` THE SYSTEM SHALL render the equivalent text as `Member Number`, `SEL`, and `AVAILABLE` respectively.
3. THE SYSTEM SHALL source every tenant-visible label, action text, and column header from tenant configuration rather than a hardcoded template literal.
4. THE SYSTEM SHALL place `JOINED` at a different column index between the two tenants (Requirement 4.18–4.19), so that a fixed-position cell read returns a different field per tenant.

## Requirement 8 — Fixture-driven rendering guarantees

**User story:** As the evidence-run author, I want specific seeded members to
render exactly as the fixture contract expects, so that later capability runs can
assert against them.

#### Acceptance criteria

1. WHEN member `100102` is opened THE SYSTEM SHALL render a shares panel containing a `CHECKING` row and no `PRIMARY SAVINGS` row.
2. WHEN member `100119` is opened THE SYSTEM SHALL render the FROZEN checking share with status `FROZEN` and the OPEN savings share with status `OPEN`.
3. WHEN member `100111` is opened THE SYSTEM SHALL render the available balance as `0.00`.
4. WHEN member `100110` is opened THE SYSTEM SHALL render the available balance as `74,209.99`.
5. WHEN a member whose name contains an apostrophe (`100113`) or a non-ASCII character (`100115`) is rendered THE SYSTEM SHALL render the name correctly HTML-escaped and unmangled.
6. WHEN a `last_name` of `miller` is submitted THE SYSTEM SHALL render exactly two rows, member `100106` before member `100107`.
7. WHEN a `last_name` of `MILLER` is submitted THE SYSTEM SHALL match case-insensitively and return the same two rows.
8. WHEN member `999999` is searched THE SYSTEM SHALL render the `No records found for the specified criteria.` message.

## Requirement 9 — Rendering invariants (verified by shared test helpers)

**User story:** As the automation platform, I want every screen in this feature
to keep the structural affordances the agent depends on, so that later locator
and redaction tests hold.

#### Acceptance criteria

1. THE SYSTEM SHALL render every route in this feature as a full HTML page and SHALL NOT expose any JSON endpoint for this feature.
2. THE SYSTEM SHALL add no JavaScript in this feature.
3. THE SYSTEM SHALL render no `data-testid` attribute on any route in this feature.
4. THE SYSTEM SHALL bind every visible `<input>` (i.e. excluding `type="hidden"`) to a matching `<label for="...">`.
5. THE SYSTEM SHALL render every data table with real `<th>` column headers whose text is uppercase, and lay out pages with nested tables and WebForms-style element ids.
6. THE SYSTEM SHALL set a distinct `<title>` on every route so the agent can tell which screen it is on.

## Resolved decisions

Answers confirmed at the requirements gate, folded into the criteria above.

1. **Column order.** Both tenants have the same five data columns; Summit reorders
   `BRANCH` before `JOINED` (4.18–4.19). This puts `JOINED` at a different index
   per tenant — the intended silent-wrong-answer demonstration.
2. **Record-page name casing.** `LAST, FIRST` uppercase, identical to the grid
   (5.3), so the agent can compare record-name to grid-name without a false
   mismatch.
3. **`Shares` nav link.** A same-page anchor to the embedded panel (`#shares-panel`),
   not a link to the chromeless route (5.7). The chromeless route exists only as
   the iframe `src`, so the agent never lands on a nav-less dead-end.
4. **Zero-results count line.** `0 record(s) found.` is always rendered alongside
   the not-found cell (4.12), so a checkpoint can assert the count line on every
   results render.
5. **Member sub-functions are member-scoped URLs.** The not-implemented stubs move
   to `/member/{member_no}/cards`, `/address`, `/transactions`, `/shares/new`, and
   the record nav strip links to them. Because every such path is more specific
   than the `/member/{member_no}` catch-all, route-declaration order no longer
   matters. Consequence: the main-menu member-function codes (`CRDMNT`, `ADRCHG`,
   `SHROPN`, `TXNHST`) can no longer target a member-agnostic page, so they route
   to Member Inquiry carrying a function code (`/mbrinq?fn=CARD` etc.). The
   criteria screen then shows a function-specific heading and the results action
   link targets that sub-function for the chosen member — a period-accurate
   "set function, then identify the member" flow rather than four dead codes.
6. **Tenant resolved per call.** The tenant config is read through `get_cfg()`
   (which reads the `TENANT` env var) rather than a module-level constant, so tests
   select a tenant with an env var and no module reload, while the app stays
   one-tenant-per-process.

## Design-gate notes

Fixed, tenant-neutral `<caption>` accessible names (`Member search results`,
`Share accounts`) are intentional: unlike the `<th>` text — which is
tenant-configured to demonstrate heterogeneity — the caption is stable across
tenants so a locator can scope to a table by name (`role=table, name="Share
accounts"`). The AX-tree contract these captions and headers produce is specified
in `design.md`.
