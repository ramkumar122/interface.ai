# Tasks — Member Inquiry

Implementation checklist. Each task is incremental and references the
requirements it satisfies. Build top to bottom; the app stays runnable and tests
stay green after every task.

- [ ] 1. Tenant resolution refactor (enables clean multi-tenant tests)
  - Add `get_tenant_key()` and `get_cfg()` to `coredesk/config.py` (read `TENANT`, validate, default `riverbend`).
  - In `coredesk/app.py`, remove the module-level `TENANT`/`CFG` constants; have `render()` inject `get_cfg()`; switch the sign-on/menu routes to `get_cfg()` / `get_tenant_key()`.
  - Confirm both tenants still serve sign-on and menu unchanged.
  - _Requirements: 7.3_

- [ ] 2. Config data for the new screens
  - Add `results_columns` and `shares_columns` (ordered, with `header`/`field`/`money`) to both tenants in `coredesk/config.py`, per the design diff tables.
  - Add `fn`/`member_action` to the member-centric `MENU_FUNCTIONS` entries and repoint their routes to `/mbrinq?fn=CARD|ADDR|SHARE_NEW|TXN`; leave `MBRINQ`→`/mbrinq`, `SIGNOFF`→`/signoff`. Add a `fn_function(token)` helper returning the entry (or `None`).
  - _Requirements: 4.18, 4.19, 6.5, 6.6, 7.1, 7.2, 7.3_

- [ ] 3. Shared chrome + presentation helpers
  - Add `{% block title %}` to `coredesk/templates/base.html` with the current default.
  - Add helpers to `coredesk/app.py`: `_fmt_name(last, first)` → `LAST, FIRST` caps, `_fmt_date(iso)` → `MM/DD/YYYY`, `_share_type_label(t)` → spaces; import `cents_to_display` from `db.money`.
  - Add a `.share-nonopen` grey-italic rule to `coredesk/static/coredesk.css`.
  - _Requirements: 9.6, 4.13, 4.14, 6.5, 6.7, 6.12_

- [ ] 4. Criteria screen — `GET /mbrinq`
  - Replace the `/mbrinq` stub with a session-guarded route rendering `mbrinq.html`.
  - Heading `MEMBER INQUIRY` (or `MEMBER INQUIRY - <FUNCTION>` when a valid `fn` is present), instruction line, bordered `method="get"` form to `/mbrinq/results` with labelled `member_no` (tenant label) and `Last Name` inputs, a hidden `fn` input when in effect, `Search` button, `Return to Menu` link; pre-fill inputs from query params; distinct `<title>`.
  - _Requirements: 1.1, 2.1–2.10, 9.4, 9.6_

- [ ] 5. Results grid — `GET /mbrinq/results`
  - Session guard; trim criteria; if both empty, re-render `mbrinq.html` with the red `Enter a member number or last name.` message and perform no search.
  - Otherwise call `find_members`, build per-row display dicts, render `mbrinq_results.html`: heading `MEMBER INQUIRY - RESULTS`, `<caption>Member search results</caption>`, tenant-ordered `<th>` + empty action `<th>`, `N record(s) found.`, name/date formatting, action `<a>` (`link_select`, WebForms `ctl<NN>` id) → `/member/{member_no}/{member_action}` when `fn` resolves else `/member/{member_no}`, dynamic-colspan not-found cell, `Return to Inquiry` preserving criteria and `fn`; distinct `<title>`.
  - _Requirements: 1.2, 3.1, 3.2, 4.1–4.21, 7.4, 8.6, 8.7, 8.8, 9.5, 9.6_

- [ ] 6. Member record — `GET /member/{member_no}`
  - Session guard; `get_member`; if missing render `member_notfound.html` (`Member record not found.` + `Return to Inquiry`, no exception).
  - Else render `member_record.html`: heading `MEMBER RECORD`, two-column label/value table (`Member Number`, `Name` as `LAST, FIRST` caps, `Status`, `Joined` MM/DD/YYYY, `Branch`, `Phone`; no DOB/email/address), red status banner when `INACTIVE`/`RESTRICTED`, nav strip (`Shares`→`#shares-panel`, member-scoped `Cards`/`Address`/`Transactions`, `Return to Inquiry`), shares `<iframe id="shares-panel" title=... src=/member/{member_no}/shares height~220 border 1px>`; distinct `<title>`.
  - _Requirements: 1.3, 5.1–5.10, 8.5_

- [ ] 7. Shares panel — `GET /member/{member_no}/shares`
  - Session guard; `get_member` (not-found → `Member record not found.` in the panel) then `list_shares`.
  - Render standalone `member_shares.html` (no base chrome, links the stylesheet): heading `cfg.label_shares`, `<caption>Share accounts</caption>`, tenant `<th>`, rows by suffix; type with spaces; available/ledger via `cents_to_display` with `align="right"` cells (distinct values; `0.00` never blank); non-OPEN rows `.share-nonopen` showing real status; `No share accounts on file.` when empty; distinct `<title>`.
  - _Requirements: 1.4, 6.1–6.13, 8.1, 8.2, 8.3, 8.4_

- [ ] 8. Member-scoped stub routes
  - Add `GET /member/{member_no}/cards`, `/address`, `/transactions`, `/shares/new`, each session-guarded and rendering `notimpl.html`; remove the old member-agnostic stubs.
  - _Requirements: 5.8_

- [ ] 9. Shared test scaffolding
  - Add `httpx` (pinned) to `requirements.txt`.
  - Move the seeded-temp-DB fixture into `tests/conftest.py` (session, autouse); remove the duplicate from `tests/test_queries.py`.
  - Create `tests/helpers.py` using stdlib `html.parser` (skip `type="hidden"`): `assert_no_data_testid`, `assert_visible_inputs_have_labels`, plus AX-contract assertions `assert_table_has_caption(html, table_id, caption)`, `assert_column_headers(html, table_id, headers)`, `assert_iframe_has_title(html, iframe_id)`.
  - _Requirements: 9.3, 9.4, 4.18, 4.19, 6.3, 6.5, 6.6, 5.10_

- [ ] 10. Feature tests — `tests/test_member_inquiry.py`
  - Auth: each of the four routes redirects to `/` when unauthenticated (use `follow_redirects=False`).
  - Search: `999999` → not-found message; `miller` → two rows `100106` before `100107`; `MILLER` → same (case-insensitive); exact `100101` → one row, HTTP 200 (no redirect).
  - Shares: `100101` contains `12,845.50` and distinct `412.09`/`460.09`; `100111` → `0.00`; `100119` → both `OPEN` and `FROZEN`; `100113`/`100115` names render unmangled.
  - Tenant: with `monkeypatch.setenv("TENANT","summit")`, criteria shows `Member ID`, results action shows `VIEW`, shares header shows `AVAIL BAL`.
  - Invariants: every route's HTML passes `assert_no_data_testid` and `assert_visible_inputs_have_labels`.
  - AX contract (multi-tenant guard): under both tenants, results grid headers equal the tenant's configured order and caption is `Member search results`; shares grid headers match (`AVAILABLE`/`LEDGER` vs `AVAIL BAL`/`LEDGER BAL`) and caption is `Share accounts`; record page `shares-panel` iframe has a non-empty title.
  - Function-code flow: `/mbrinq?fn=CARD` → heading `MEMBER INQUIRY - CARD MAINTENANCE` + hidden `fn`; results under `fn=CARD` target row action → `/member/{member_no}/cards`.
  - _Requirements: 1, 2.9, 4.8, 4.18, 4.19, 4.21, 6.3, 6.10, 7.1, 7.4, 8.2–8.8, 9.3, 9.4_

- [ ] 11. Verify and self-check
  - `python reset_db.py && pytest` green.
  - Run `TENANT=riverbend PORT=8001` and `TENANT=summit PORT=8002`; walk the acceptance script (menu→inquiry via code and fast-path; `100101`→SEL→record + populated shares panel; `miller`→two rows; `999999`→not-found; side-by-side tenant diffs; View Source shows no `data-testid`, WebForms ids, nested tables).
  - Stop the servers and clean up.
  - _Requirements: all_

---

_Out of scope for this feature (reminder, not a task): before building the cards
or address specs, spike the discovery run — a throwaway script driving member
100101 to read the savings balance — to confirm the model can drive a
nested-table page at acceptable token cost._
