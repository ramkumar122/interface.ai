# Design — Card Maintenance

Builds on `write-path.md` (PRG, references, audit, reversible/irreversible,
gating) and the member-inquiry patterns (per-call `get_cfg()`, config-driven
columns, shared test helpers). Read those first; this only covers what is new.

## Overview and route map

All routes are session-guarded, return HTML, and reuse existing `db/queries.py`
functions. No new SQL; one existing function (`set_card_status`) has its `detail`
construction refined for redaction (below).

| Route | Method | Template | queries.py | Notes |
|---|---|---|---|---|
| `/member/{member_no}/cards` | GET | `cards_list.html` | `get_member`, `list_cards` | replaces the stub |
| `/member/{member_no}/cards/{card_id}/maint` | GET | `card_maint.html` | `get_member`, `get_card` | read-only detail + form; renders `?ref=` banner |
| `/member/{member_no}/cards/{card_id}/maint` | POST | `card_maint.html` (re-render) or 303 redirect | `get_member`, `get_card`, `set_card_status` | validate → apply → audit → PRG |

Routes are all ≥3 segments under `/member/{member_no}/...`, so they never collide
with the `/member/{member_no}` catch-all (ordering-independent, per member-inquiry).

## Config additions (`coredesk/config.py`)

Add `cards_columns` to **both** tenants, identical labels (confirmed at gate — no
Summit divergence for cards):

```python
"cards_columns": [
    {"header": "CARD",    "field": "card_id"},
    {"header": "NETWORK", "field": "network"},
    {"header": "LAST 4",  "field": "last4"},
    {"header": "STATUS",  "field": "status"},
    {"header": "LINKED",  "field": "linked"},
],
```

The unlabelled action (`MAINT`) column is rendered after the loop, as with the
results grid.

## Data-layer change — redact `set_card_status` detail

Change the one line that builds `detail` so a free-text note is never persisted:

```python
detail = "reason=%s" % reason + ("; note=Y" if notes else "")
```

The reason is a controlled enum value (safe); the note's presence is recorded but
its text is not. Everything else in `set_card_status` is unchanged. The existing
Task-2 test doesn't assert detail content, so it stays green.

## Presentation helpers (`coredesk/app.py`)

- `_mask_last4(last4) -> "****" + last4` → `****4417`.
- network label: reuse a `.replace("_", " ")` (same transform as share type) →
  `VISA DEBIT`.
- `_linked_suffix(share_id)` → the segment after the last `-` (`SHR-100101-0070` →
  `0070`), or `—` when `linked_share_id` is null.

## Screen designs

### base.html title

Titles: list `Card Maintenance - {inst}`, maint `Card Maintenance {card_id} - {inst}`.

### Card list — `cards_list.html` (extends base)

- `<h1 id="ctl00_MainContent_hdrTitle">CARD MAINTENANCE</h1>` plus a context line
  with member number and name (`NAKAMURA, ALICE`).
- Table `id="ctl00_MainContent_grdCards"`, `<caption>Cards on file</caption>`,
  header row from `cfg.cards_columns` + one empty action `<th>`.
- Row cells read `row[col.field]`; `last4` is pre-masked, `network` pre-spaced,
  `linked` pre-resolved. Action cell:
  `<a id="ctl00_MainContent_grdCards_ctl<NN>_lnkMaint" href="/member/{m}/cards/{card_id}/maint">MAINT</a>`.
- No cards → single full-width cell `No cards on file for this member.`
  (`colspan="{{ cfg.cards_columns|length + 1 }}"`).
- Links: `Return to Member Record` → `/member/{m}`, `Return to Inquiry` → `/mbrinq`.
- Member not found → reuse `member_notfound.html`.

### Card maintenance — `card_maint.html` (extends base)

Context flags passed by the route: `card` (dict or None), `banner` (ref confirmation
or None), `error` (validation, e.g. reason required), `info` (no-change / immutable
message).

- Card not found (card_id not this member's) → `Card not found.` + `Return to Cards` link.
- Otherwise:
  - `{% if banner %}` green confirmation line `Card updated. Reference: {{ ref }}.`
  - `{% if error %}` / `{% if info %}` red / plain message line.
  - Read-only detail table `id="ctl00_MainContent_tblCard"`: Card, Network, Last 4
    (masked), Status, Linked (suffix), Issued (`MM/DD/YYYY`).
  - Form `method="post"` to the same URL, with the irreversible-only confirm:
    ```html
    <form id="ctl00_MainContent_frmMaint" method="post"
          action="/member/{{ member_no }}/cards/{{ card.card_id }}/maint"
          onsubmit="var a=this.querySelector('input[name=action]:checked');
                    return !(a && a.value=='HOTLIST') ||
                           confirm('Report this card lost or stolen? This cannot be undone.');">
    ```
  - `<fieldset>` with `<legend>Action</legend>` and three radios, each with a
    `<label for>`: `Lock` (`LOCK`), `Unlock` (`UNLOCK`), `Report Lost or Stolen`
    (`HOTLIST`). The `<legend>` gives the group its accessible name.
  - `Reason` `<label for>` + `<select name="reason">` with a leading empty option
    `-- select --` then the four reasons.
  - `Notes` `<label for>` + `<textarea name="notes" maxlength="200">`.
  - `<button>Apply</button>` and `<a ...>Cancel</a>` → `/member/{m}/cards`.

The `onsubmit` handler is the **only** JavaScript this feature adds, and it fires
`confirm()` only when the `HOTLIST` radio is selected (Req 7.2).

## POST logic — precedence and transitions

The route evaluates in this order (first match wins, per Req 4.1); only the final
"apply" branch calls `set_card_status` (and therefore writes an audit row):

| # | Condition | Response | Audit? |
|---|---|---|---|
| 1 | member not found | `member_notfound.html` | no |
| 2 | member `RESTRICTED` | re-render, `Member status is RESTRICTED. This function is unavailable.` | no |
| 3 | role `TELLER_RO` | re-render, `Your role does not permit this function.` | no |
| 4 | card not this member's | re-render, `Card not found.` | no |
| 5 | card `HOTLISTED` | re-render, `Card is hotlisted and cannot be modified.` | no |
| 6 | card `EXPIRED` | re-render, `Card is expired and cannot be modified.` | no |
| 7 | reason empty | re-render, `Reason is required.` (re-fill action/notes) | no |
| 8 | no-op: `LOCK` on `LOCKED` | re-render, `Card is already locked. No change applied.` | no |
| 8 | no-op: `UNLOCK` on `ACTIVE` | re-render, `Card is already active. No change applied.` | no |
| 9 | apply | `set_card_status(...)` → 303 to maint GET `?ref=CRD-#####` | **yes (1)** |

Action→status map: `LOCK`→`LOCKED`, `UNLOCK`→`ACTIVE`, `HOTLIST`→`HOTLISTED`.
`set_card_status` maps status→audit action (`CARD_LOCK`/`CARD_UNLOCK`/`CARD_HOTLIST`).
Only success redirects (PRG); every other outcome re-renders the maint screen at
`200`. `Report Lost or Stolen` from `ACTIVE` or `LOCKED` is a valid apply (row 9).

## Masking and no-PAN guarantee

`last4` is rendered only as `****NNNN`, everywhere. There is no full PAN in the
schema. The test asserts the raw four digits never appear except immediately
preceded by a `*` (i.e. only inside the mask).

## Accessibility-tree contract

**Card list — `/member/100101/cards`** (both tenants identical headers):

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

**Card maintenance — `/member/100101/cards/CRD-100101-1/maint`**:

```
- heading "CARD MAINTENANCE" [level=1]
- table "..."            # read-only detail (label/value)
- group "Action":
  - radio "Lock"
  - radio "Unlock"
  - radio "Report Lost or Stolen"
- combobox "Reason"
- textbox "Notes"
- button "Apply"
- link "Cancel"
```

The irreversible option is a named `radio "Report Lost or Stolen"` that the agent
can see and decline. Values verified against fixtures: 100101 → `CRD-100101-1`,
`VISA DEBIT`, `****4417`, `ACTIVE`, linked `0070`.

## Rendering invariants / JS scope

Full HTML only, no JSON. The single `onsubmit` confirm is the only JS. No
`data-testid`. Every visible input/select/textarea has a `<label for>` (the
radios via their labels, the group via `<legend>`). `<th>` + `<caption>` on the
grid, config-sourced headers, nested-table layout, WebForms ids.

## Files created / modified

Created:
- `coredesk/templates/cards_list.html`, `coredesk/templates/card_maint.html`
- `tests/test_card_maintenance.py`
- `docs/implementation-notes/card-maintenance.md` (the standing record)

Modified:
- `coredesk/app.py` — replace the `/member/{member_no}/cards` stub with the list
  route; add the maint GET + POST; add `_mask_last4` / `_linked_suffix` helpers.
- `coredesk/config.py` — add `cards_columns` to both tenants.
- `db/queries.py` — redact `set_card_status` `detail`.

## Test plan (`tests/test_card_maintenance.py`)

Reuses `helpers.py` (`assert_no_data_testid`, `assert_visible_inputs_have_labels`,
`assert_table_has_caption`, `assert_column_headers`). Two signed-in clients: `auth`
(mreyes, MSR) and `auth_ro` (jtran, TELLER_RO). Because these tests mutate card
state, the module adds a **function-scoped autouse** fixture that calls
`reset_db.rebuild()` before each test, so every test starts from clean seed
(fast; staff unchanged so the session cookie stays valid). Audit assertions
snapshot `audit_log` counts before/after and assert the delta.

Cases (→ requirement):
- list shows caption + config header order; masked `****4417`; `MAINT` link (2, 7)
- `100109` → no-cards message (2.6, 8.3)
- `100112` → two rows; lock card 1 leaves card 2 `ACTIVE` (4.7, 8.4)
- lock `100101` ACTIVE → `LOCKED`, delta +1 audit row action `CARD_LOCK`, `CRD-` in banner (4.3, 8.1)
- lock `100103` already-LOCKED → info message, status unchanged, audit delta **0** (5.1, 8.2)
- hotlist `100101` → `HOTLISTED`, `CARD_HOTLIST`; a subsequent action → hotlisted-immutable message, no audit (4.5, 5.3)
- `100113` EXPIRED → expired message, audit delta 0 (5.4, 8.5)
- `POST` as `auth_ro` (TELLER_RO) → role message, status unchanged, audit delta 0 (1.4)
- `100108` RESTRICTED → restricted message on list + POST refused (1.3, 8.6)
- reason empty → `Reason is required.`, action/notes re-filled, no change (4.2)
- notes redaction: submit distinctive notes text, assert it is **not** in `audit_log.detail` (6.2)
- no-PAN: rendered HTML has no unmasked four-digit run of the card's last4 (6.3)
- invariants on list + maint: `assert_no_data_testid`, `assert_visible_inputs_have_labels` (7.3, 7.4)

## Open design choices (minor, non-blocking)

1. Non-success POST outcomes re-render the maint screen (200); only success uses
   PRG. This keeps the "no state change → no new URL/banner" contract crisp.
2. Card-not-found renders inside `card_maint.html` behind a flag rather than a
   separate template, since it's a one-line message + a return link.
