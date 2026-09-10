# Design — Member Inquiry

## Research notes

Findings that ground this design. Sources linked; content paraphrased for
licensing compliance.

**Legacy "inquiry" screen conventions.** The green-screen / TN3270 lineage
(still reached today through terminal emulation and web front-ends) established
the pattern this feature imitates: a criteria screen, a fixed-column list
screen, and a drill-down detail screen, navigated by short function codes rather
than free browsing, with terse status lines such as a record count or a
"no records" message ([IBM 3270, Wikipedia](https://en.wikipedia.org/wiki/IBM_3270)).
ASP.NET WebForms `GridView` continued the same fixed-header, per-row-select-link
grid on the web, which is the specific look CoreDesk mimics. Takeaway: criteria →
results → record with a per-row select link and a "No records found" line is
faithful, not invented.

**How the AX tree exposes tables.** A native `<table>` maps to role `table`; a
`<th>` scoped to a column maps to `columnheader`; a `<td>` maps to `cell` (or
`gridcell` inside an interactive grid); `<tr>` maps to `row`; and `<thead>`/
`<tbody>` map to `rowgroup`
([MDN: table role](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Roles/table_role),
[columnheader role](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Roles/columnheader_role),
[cell role](https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Roles/cell_role)).
A column header's accessible name is its text content; a data cell's association
to its column is derived structurally from position/scope, not from any explicit
attribute we must add ([W3C ARIA APG: grid and table properties](https://www.w3.org/WAI/ARIA/apg/practices/grid-and-table-properties/)).
This is what makes "read the cell under the column whose header is AVAILABLE"
work — the header text is a first-class accessible name, and the automation can
locate a `columnheader` by name and then the aligned `cell`. It also confirms the
trap: nothing about the AX tree encodes a *stable* column index, so a
position-based read silently follows whatever order the tenant rendered.

**How the AX tree handles iframes.** An `<iframe>` is exposed as its own node
whose accessible name comes from its `title`; the embedded document is a
*separate* accessibility subtree that assistive tech (and automation) must
"enter" to reach — the child's nodes are not siblings of the parent's content
([W3C ACT rule on iframe names](https://www.w3.org/WAI/standards-guidelines/act/rules/4b1c6c/proposed/)).
This is why the shares grid must be located by first resolving the frame, then
the table inside it — hence the `title` on the iframe (Req 5.10) and the
`<caption>` on the inner table (Req 6.3).

**Current Playwright (Python, sync) API.** The old `page.accessibility.snapshot()`
is deprecated in favor of ARIA snapshots
([deprecation issue #16159](https://github.com/microsoft/playwright/issues/16159)):
`page.aria_snapshot()` / `locator.aria_snapshot()` return a YAML view of the
accessibility tree, and `expect(locator).to_match_aria_snapshot(...)` asserts it
([Playwright aria snapshots](https://playwright.dev/python/docs/aria-snapshots)).
For iframes, the current API is `page.frame_locator("iframe[title='...']")`
returning a `FrameLocator`, then `.get_by_role(...)` inside it
([FrameLocator](https://playwright.dev/python/docs/api/class-framelocator));
`locator.content_frame` converts an element locator to its frame. The later agent/
replay specs should target these, not the deprecated Accessibility class. Nothing
here contradicts the brief.

## Overview and route map

All routes live in `coredesk/app.py`, return HTML via `TemplateResponse`, require
a session (redirect `/` otherwise), and call only existing functions in
`db/queries.py`. No new SQL, no writes (read-only feature, so no `audit_log`).

| Route | Method | Template | queries.py calls | Notes |
|---|---|---|---|---|
| `/mbrinq` | GET | `mbrinq.html` | none | criteria form; pre-fills from query params |
| `/mbrinq/results` | GET | `mbrinq_results.html` (or `mbrinq.html` on empty criteria) | `find_members(member_no, last_name)` | trims criteria first |
| `/member/{member_no}` | GET | `member_record.html` / `member_notfound.html` | `get_member(member_no)` | not-found renders a page, no exception |
| `/member/{member_no}/shares` | GET | `member_shares.html` (no base chrome) | `get_member`, `list_shares(member_no)` | iframe body |
| `/member/{member_no}/cards` | GET | `notimpl.html` | none | member-scoped stub |
| `/member/{member_no}/address` | GET | `notimpl.html` | none | member-scoped stub |
| `/member/{member_no}/transactions` | GET | `notimpl.html` | none | member-scoped stub |
| `/member/{member_no}/shares/new` | GET | `notimpl.html` | none | member-scoped stub |

`find_members` already matches `member_no` exactly, `last_name` as a
case-insensitive prefix, lets `member_no` win, and orders by `member_no` ascending
— exactly Requirement 4.1–4.4 and 4.11. Whitespace trimming (Req 4.1) happens in
the route before the call, keeping SQL untouched.

### Route shape (no ordering dependency)

Every member sub-function is member-scoped, so there is no static `/member/*`
route left to collide with the `/member/{member_no}` catch-all: `/member/{member_no}`
is two segments; `/member/{member_no}/cards|address|transactions|shares|shares/new`
are three or four. They never overlap, so declaration order stops mattering. This
replaces the earlier plan of relying on registration order.

Consequences:
- The Task 1 member-agnostic stubs (`/member/cards`, etc.) are **removed**.
- The record nav strip (Req 5.8) links to the member-scoped stubs, carrying the
  current `member_no`, so those hrefs won't be rewritten when the card/address
  specs land.
- The MAIN MENU's member-centric codes (`CRDMNT`, `ADRCHG`, `SHROPN`, `TXNHST`)
  can no longer target a member-agnostic page, so they route to Member Inquiry
  (`/mbrinq`), the member-selection entry point. `MBRINQ`→`/mbrinq` and
  `SIGNOFF`→`/signoff` are unchanged. This is a small `MENU_FUNCTIONS` edit in
  `config.py`, flagged for confirmation.
- `member_no` is not regex-constrained (Starlette has no built-in regex
  converter); an unknown two-segment `/member/xxx` falls through to `get_member`
  → not-found page, which is acceptable.

## Function-code entry (`fn`)

The member-centric menu codes carry a canonical function token so the flow is
"set function, then identify the member" (period-accurate) instead of four codes
landing on an identical page. `MENU_FUNCTIONS` in `config.py` gains `fn` and
`member_action` fields on the member-centric entries:

| description | fn | member_action | menu route |
|---|---|---|---|
| Card Maintenance | `CARD` | `cards` | `/mbrinq?fn=CARD` |
| Address Change | `ADDR` | `address` | `/mbrinq?fn=ADDR` |
| Open Share Account | `SHARE_NEW` | `shares/new` | `/mbrinq?fn=SHARE_NEW` |
| Transaction History | `TXN` | `transactions` | `/mbrinq?fn=TXN` |

`Member Inquiry` (`/mbrinq`) and `Sign Off` (`/signoff`) keep `fn=None`. A helper
`fn_function(token) -> dict | None` returns the matching `MENU_FUNCTIONS` entry
(or `None` for absent/unknown), giving both the description (for the heading) and
`member_action` (for the action-link target). The token is canonical, so the same
values work for both tenants despite their different menu codes (`CRDMNT`/`CRD01`).

Behavior:
- **Criteria (`/mbrinq?fn=CARD`)**: heading `MEMBER INQUIRY - CARD MAINTENANCE`
  (description uppercased); the search form includes `<input type="hidden"
  name="fn" value="CARD">` so `fn` rides the `GET` submission through to results.
  Absent/unknown `fn` → plain `MEMBER INQUIRY`, no hidden input.
- **Results**: each row's action link targets `/member/{member_no}/{member_action}`
  when `fn` resolves, else `/member/{member_no}`. `Return to Inquiry` preserves
  `fn` alongside the criteria.

Unknown `fn` is treated as absent (defensive), so a stray value never errors.

## Tenant resolution (small refactor)

Replace the module-level `TENANT`/`CFG` constants in `coredesk/app.py` with a
function in `coredesk/config.py`:

```python
def get_tenant_key() -> str:
    key = os.environ.get("TENANT", "riverbend")
    return key if key in CONFIG else "riverbend"

def get_cfg() -> dict:
    return CONFIG[get_tenant_key()]
```

`render()` puts `get_cfg()` into every template context, and any route needing
tenant data takes it via `cfg: dict = Depends(get_cfg)`. The `TENANT` env var still
decides the tenant, so it remains one-tenant-per-process — but nothing caches a
stale `CFG` reference, and a test selects a tenant with
`monkeypatch.setenv("TENANT", "summit")`, no module reload and no import-order
fragility. Existing sign-on/menu routes switch from the `CFG` constant to
`get_cfg()` / `get_tenant_key()`; the menu helpers already accept a tenant key.

## Configuration additions (`coredesk/config.py`)

Column headers and their order are tenant-visible text, so they come from config
(tech rule 8). Add two ordered lists per tenant. `field` keys are logical names
the route populates into a per-row display dict; `money` marks right-aligned
cells.

```python
# riverbend
"results_columns": [
    {"header": "MBR NO",  "field": "member_no"},
    {"header": "NAME",    "field": "name"},
    {"header": "STATUS",  "field": "status"},
    {"header": "JOINED",  "field": "joined"},
    {"header": "BRANCH",  "field": "branch"},
],
"shares_columns": [
    {"header": "SUFFIX",      "field": "suffix"},
    {"header": "TYPE",        "field": "type"},
    {"header": "DESCRIPTION", "field": "description"},
    {"header": "AVAILABLE",   "field": "available", "money": True},
    {"header": "LEDGER",      "field": "ledger",    "money": True},
    {"header": "STATUS",      "field": "status"},
],

# summit  (results: BRANCH before JOINED; shares: AVAIL BAL / LEDGER BAL / ACCT)
"results_columns": [
    {"header": "MBR NO",  "field": "member_no"},
    {"header": "NAME",    "field": "name"},
    {"header": "STATUS",  "field": "status"},
    {"header": "BRANCH",  "field": "branch"},
    {"header": "JOINED",  "field": "joined"},
],
"shares_columns": [
    {"header": "ACCT",        "field": "suffix"},
    {"header": "TYPE",        "field": "type"},
    {"header": "DESCRIPTION", "field": "description"},
    {"header": "AVAIL BAL",   "field": "available", "money": True},
    {"header": "LEDGER BAL",  "field": "ledger",    "money": True},
    {"header": "STATUS",      "field": "status"},
],
```

The unlabelled action column on the results grid is **not** in `results_columns`;
the template renders an empty `<th></th>` plus the action `<td>` after the loop,
so header count still equals column count.

## Exact `<th>` text per tenant (diff target)

**Results grid** (final column is the unlabelled action column):

| Col | Riverbend | Summit |
|----:|-----------|--------|
| 1 | `MBR NO` | `MBR NO` |
| 2 | `NAME` | `NAME` |
| 3 | `STATUS` | `STATUS` |
| 4 | `JOINED` | `BRANCH` |
| 5 | `BRANCH` | `JOINED` |
| 6 | (empty) | (empty) |

**Shares grid:**

| Col | Riverbend | Summit |
|----:|-----------|--------|
| 1 | `SUFFIX` | `ACCT` |
| 2 | `TYPE` | `TYPE` |
| 3 | `DESCRIPTION` | `DESCRIPTION` |
| 4 | `AVAILABLE` | `AVAIL BAL` |
| 5 | `LEDGER` | `LEDGER BAL` |
| 6 | `STATUS` | `STATUS` |

All `<th>` text is uppercase (Req 9.5).

## Presentation helpers

Small pure helpers in `coredesk/app.py` (kept out of `db/`); money comes from the
data layer:

- `from db.money import cents_to_display`
- `_fmt_name(last, first) -> f"{last}, {first}".upper()` → `NAKAMURA, ALICE`
- `_fmt_date(iso) -> "MM/DD/YYYY"` (reformat `YYYY-MM-DD`; pure string split, no locale)
- `_share_type_label(t) -> t.replace("_", " ")` → `PRIMARY SAVINGS`

Routes build a per-row display dict keyed by the logical `field` names, so the
template just iterates `cfg.results_columns` / `cfg.shares_columns` and reads
`row[col.field]`. This is what lets one template serve both tenants while the
header text and order come entirely from config.

Escaping: `Jinja2Templates` autoescapes `.html`, so `O'Connor` and `Bergström`
render correctly (`&#39;`, UTF-8) with a `<meta charset="utf-8">` already in
`base.html`; the standalone shares page includes the same meta. No manual
escaping needed (Req 8.5).

## Screen designs

### base.html change

Add a `{% block title %}` so each page sets a distinct `<title>` (Req 9.6):

```html
<title>{% block title %}{{ cfg.product_banner }} - {{ cfg.institution_name }}{% endblock %}</title>
```

Titles: criteria `Member Inquiry - {inst}`, results `Member Inquiry Results -
{inst}`, record `Member Record {member_no} - {inst}`, shares (standalone)
`Share Accounts - {inst}`.

### Screen 1 — `mbrinq.html` (extends base)

- `<h1 id="ctl00_MainContent_hdrTitle">`: `MEMBER INQUIRY`, or `MEMBER INQUIRY -
  <FUNCTION>` when a valid `fn` is present (see Function-code entry).
- Instruction line, then a bordered criteria table `method="get" action="/mbrinq/results"`
  (with `<input type="hidden" name="fn" value="{fn}">` when `fn` is in effect):
  - `<label for="ctl00_MainContent_txtMemberNo">{{ cfg.label_member_no }}</label>` + text input `name="member_no"`
  - `<label for="ctl00_MainContent_txtLastName">Last Name</label>` + text input `name="last_name"`
  - `<button id="ctl00_MainContent_btnSearch">Search</button>`
- Inputs pre-fill from `member_no` / `last_name` query params (Req 2.7).
- Red validation line shown when the empty-criteria flag is set (Req 3.1).
- `Return to Menu` link → `/menu`.

### Screen 2 — `mbrinq_results.html` (extends base)

- `<h1>MEMBER INQUIRY - RESULTS</h1>`
- `N record(s) found.` line (literal `record(s)`, always shown incl. `0`).
- Table `id="ctl00_MainContent_grdMembers"` with `<caption>Member search results</caption>`,
  a header `<tr>` of `<th scope="col">` from `cfg.results_columns` + one empty
  `<th>` for the action column.
- One `<tr>` per member (ordered ascending). Cells read `row[col.field]`; final
  cell is `<a id="ctl00_MainContent_grdMembers_ctl<NN>_lnkSel" href="{target}">{{ cfg.link_select }}</a>`
  where `<NN>` = zero-padded `loop.index + 1` (data rows start at `ctl02`, matching
  the menu grid). `{target}` is `/member/{member_no}/{member_action}` when a valid
  `fn` is in effect, else `/member/{member_no}`.
- Zero matches: render the header row and a single
  `<td colspan="{{ cfg.results_columns|length + 1 }}">No records found for the
  specified criteria.</td>` (data columns + the action column, never a hardcoded
  literal) — table not hidden (Req 4.9).
- `Return to Inquiry` → `/mbrinq?member_no=..&last_name=..` (only the non-empty
  criteria), so the criteria screen re-fills.

### Screen 3 — `member_record.html` (extends base) and `member_notfound.html`

- Existing member: `<h1>MEMBER RECORD</h1>`, then a two-column label/value table
  `id="ctl00_MainContent_tblMember"` with exactly `Member Number`, `Name`
  (`LAST, FIRST` caps), `Status`, `Joined` (MM/DD/YYYY), `Branch`, `Phone`. No
  DOB/email/address.
- Status banner when `INACTIVE`/`RESTRICTED`: red `Member status is {STATUS}.
  Some functions are unavailable.`
- Nav strip: `Shares` → `#shares-panel` (same page); `Cards`/`Address`/
  `Transactions` → member-scoped stubs (`/member/{member_no}/cards`, `/address`,
  `/transactions`); `Return to Inquiry` → `/mbrinq`.
- Shares iframe:
  `<iframe id="shares-panel" title="Share accounts for member {member_no}"
   src="/member/{member_no}/shares" height="220" style="border:1px solid ...; width:100%"></iframe>`
- Nonexistent member: render `member_notfound.html` — `Member record not found.`
  + `Return to Inquiry`. No exception (Req 5.5).

### Screen 4 — `member_shares.html` (standalone, no base chrome)

- Minimal full document: `<!doctype html><html><head><meta charset=utf-8><title>…</title>
  <link rel="stylesheet" href="/static/coredesk.css"></head><body>…</body></html>`.
- `<h1>{{ cfg.label_shares }}</h1>` (`Shares` / `Savings Accounts`).
- Table `id="ctl00_MainContent_grdShares"` with `<caption>Share accounts</caption>`
  and `<th scope="col">` from `cfg.shares_columns`.
- One `<tr>` per share (ordered by suffix). Money cells carry `align="right"`
  (attribute, Req 6.8) and render via `cents_to_display` (so `0.00`, `74,209.99`,
  and distinct avail/ledger). Type rendered with spaces.
- Non-OPEN rows get `class="share-nonopen"` → grey italic (a small rule added to
  `coredesk.css`: `.share-nonopen td { color:#777; font-style:italic; }`). Status
  cell shows the real status.
- No shares: `No share accounts on file.`
- Nonexistent member: `Member record not found.` in the panel (Req 6.2).

## Accessibility-tree contract (the locator target)

This is the contract later locator code is written against. Shown in Playwright
aria-snapshot style. A browser-generated `<tbody>` may add a `rowgroup` wrapper;
locators use role+name and must not depend on it.

**Results grid — Riverbend** (`/mbrinq/results?member_no=100101`):

```
- table "Member search results":
  - row:
    - columnheader "MBR NO"
    - columnheader "NAME"
    - columnheader "STATUS"
    - columnheader "JOINED"
    - columnheader "BRANCH"
    - columnheader ""
  - row:
    - cell "100101"
    - cell "NAKAMURA, ALICE"
    - cell "ACTIVE"
    - cell "03/12/2015"
    - cell "001"
    - cell:
      - link "SEL"
```

**Results grid — Summit** differs only in header order (`BRANCH` before `JOINED`)
and the action link name (`VIEW`). Same member row therefore has `001` and
`03/12/2015` in swapped positions — the positional-read trap, made explicit.

**Shares grid — Riverbend** (`/member/100101/shares`, inside the iframe):

```
- table "Share accounts":
  - row:
    - columnheader "SUFFIX"
    - columnheader "TYPE"
    - columnheader "DESCRIPTION"
    - columnheader "AVAILABLE"
    - columnheader "LEDGER"
    - columnheader "STATUS"
  - row:
    - cell "0000"
    - cell "PRIMARY SAVINGS"
    - cell "Regular Share"
    - cell "12,845.50"
    - cell "12,845.50"
    - cell "OPEN"
  - row:
    - cell "0070"
    - cell "CHECKING"
    - cell "Free Checking"
    - cell "412.09"
    - cell "460.09"
    - cell "OPEN"
```

**On the record page**, the iframe is a distinct node named by its title, e.g.
`iframe "Share accounts for member 100101"`, and the table above lives in the
frame's subtree. Reading the savings available balance is therefore:
resolve frame by title → `get_by_role("table", name="Share accounts")` →
the `row` whose first cell is `0000` → the `cell` under `columnheader`
`AVAILABLE` (Riverbend) / `AVAIL BAL` (Summit). By header name, never index.

All literal values above (`100101`, `NAKAMURA, ALICE`, `ACTIVE`, `03/12/2015`,
`001`, `12,845.50`, `412.09`, `460.09`) were verified against member 100101 in
`db/fixtures.py`, so they are true and can be reused verbatim in tests and later
capability checkpoints, not merely illustrative.

## No JSON, no JavaScript (confirmation)

Every route returns `TemplateResponse` (HTML). No `/api/*`, no JSON. No `<script>`
and no inline handlers are added anywhere in this feature; the shares panel is a
plain `<iframe src>` (a full page load), not fetch/XHR. This satisfies tech rules
1, 2, and 7.

## Files created / modified

Created:
- `coredesk/templates/mbrinq.html`
- `coredesk/templates/mbrinq_results.html`
- `coredesk/templates/member_record.html`
- `coredesk/templates/member_notfound.html`
- `coredesk/templates/member_shares.html`
- `tests/test_member_inquiry.py`
- `tests/helpers.py` (shared assertions, reused by future specs)

Modified:
- `coredesk/app.py` — replace the `/mbrinq` stub with the criteria route; add the
  results, record, shares, and member-scoped stub routes; add presentation
  helpers; drop the module-level `TENANT`/`CFG` constants in favor of `get_cfg()`.
- `coredesk/config.py` — add `get_cfg()` / `get_tenant_key()`; add
  `results_columns` and `shares_columns` per tenant; repoint the member-centric
  `MENU_FUNCTIONS` routes to `/mbrinq`.
- `coredesk/templates/base.html` — add the `{% block title %}`.
- `coredesk/static/coredesk.css` — add `.share-nonopen` grey-italic rule (the money
  right-align uses an HTML attribute, not CSS).
- `tests/conftest.py` — move the seeded-temp-DB fixture here (session, autouse) so
  every test module shares it.
- `requirements.txt` — add `httpx` (Starlette `TestClient` dependency) pinned.

## Test plan (`tests/test_member_inquiry.py`)

Uses `fastapi.testclient.TestClient`. Because the tenant is resolved per call via
`get_cfg()`, a test selects the tenant with `monkeypatch.setenv("TENANT", "summit")`
before issuing requests — no module reload, no import-order fragility. The seeded
temp DB comes from the shared `tests/conftest.py` fixture (points `COREDESK_DB` at a
temp file, runs `reset_db.rebuild`). To assert redirects without following them,
use `TestClient(app, follow_redirects=False)`.

Shared helpers in `tests/helpers.py` (stdlib `html.parser`, no new dependency):
- `assert_no_data_testid(html)` — fails if `data-testid` appears anywhere.
- `assert_visible_inputs_have_labels(html)` — every `<input>` except
  `type="hidden"` has a `<label for>` matching its `id`.
- `assert_table_has_caption(html, table_id, expected_caption)` — the table with
  that id has a `<caption>` equal to the expected text.
- `assert_column_headers(html, table_id, expected_headers)` — the `<th>` text and
  order under that table equal the expected list (this is the test that protects
  the multi-tenant column-order claim).
- `assert_iframe_has_title(html, iframe_id)` — the iframe with that id carries a
  non-empty `title`.

Cases (mapped to requirements):
- Unauthenticated `GET` of each of the four routes → 303 to `/` (Req 1).
- `member_no=999999` → not-found message (Req 4.9 / 8.8).
- `last_name=miller` → exactly two rows, `100106` before `100107` (Req 8.6).
- `last_name=MILLER` → same two rows, case-insensitive (Req 8.7).
- `100101` shares panel contains `12,845.50`, and the checking row shows different
  available (`412.09`) and ledger (`460.09`) (Req 6.10 / 8).
- `100111` shares → `0.00` (Req 8.3).
- `100119` shares → both `OPEN` and `FROZEN` present (Req 8.2).
- Under `TENANT=summit`: criteria shows `Member ID`, results action shows `VIEW`,
  shares header shows `AVAIL BAL` (Req 7.1).
- Every route's rendered HTML passes `assert_no_data_testid` and
  `assert_visible_inputs_have_labels` (Req 9.3–9.4).
- (Coverage add) exact `member_no=100101` returns exactly one row and the response
  is 200, not a redirect (Req 4.8); `100113`/`100115` names render unmangled (Req 8.5).
- **AX contract** (the multi-tenant guard): under both tenants, assert the results
  grid `ctl00_MainContent_grdMembers` column headers equal the tenant's configured
  order (Riverbend `…JOINED,BRANCH`; Summit `…BRANCH,JOINED`) and its caption is
  `Member search results`; assert the shares grid `ctl00_MainContent_grdShares`
  headers match (`AVAILABLE`/`LEDGER` vs `AVAIL BAL`/`LEDGER BAL`) with caption
  `Share accounts`; and assert the record page's `shares-panel` iframe has a
  non-empty title (Req 4.18–4.19, 6.3–6.6, 5.10, 7.4).
- Function-code flow: `/mbrinq?fn=CARD` renders heading `MEMBER INQUIRY - CARD
  MAINTENANCE` and a hidden `fn` input; results under `fn=CARD` target the row
  action link at `/member/{member_no}/cards` (Req 2.9, 4.21).

## Open design choices (minor, non-blocking)

1. `member_notfound.html` is a separate small template rather than a conditional
   inside `member_record.html`, so the not-found page can carry its own heading/
   title cleanly. Easy to inline if you'd prefer one template.
2. The iframe border/height use a tiny inline `style` (1px border, ~220px) since
   they are structural to the embed, not theming. Everything else stays in the
   stylesheet.
