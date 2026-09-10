# Tech Stack & Hard Rules

## Stack (fixed — never substitute without asking)

**CoreDesk (the target app)**
- Python 3.12
- FastAPI + Uvicorn, used **only as an HTML page server**
- Jinja2 templates
- stdlib `sqlite3`. No SQLAlchemy, no ORM, no migrations tool.
- One hand-written CSS file. No Tailwind, no Bootstrap, no build step.
- No React, Vue, or any JS framework.

**Automation system**
- Python 3.12
- Playwright (sync API), Chromium, headed
- Anthropic SDK, Claude with tool use
- Pydantic v2 for the artifact schema and result contracts
- pytest

## Non-negotiable rules for CoreDesk

1. **Zero JSON endpoints.** Every route returns a full HTML page via
   `TemplateResponse`. No `/api/*` route may exist. If you think you need one,
   stop and ask. The premise of the whole project is that this app has no API.
2. **Full page loads only.** Every action is a `<form method="post">` or a plain
   `<a href>`. No fetch, no XHR, no client-side routing, no partial updates.
3. **No `data-testid` anywhere, ever.** Not in templates, not in comments.
4. **Element ids imitate ASP.NET WebForms**, e.g.
   `ctl00_MainContent_txtUserId`, `ctl00_MainContent_grdMembers_ctl03_lnkSel`.
   They are meant to look unstable and unusable as locators.
5. **Layout uses nested `<table>`** with `cellpadding`/`cellspacing`. Never
   flexbox or CSS grid for page structure.
6. **Always keep these**, because the automation depends on them and real
   server-rendered enterprise apps have them:
   - a real `<label for="...">` bound to every form input
   - real `<button>` and `<a>` elements with meaningful visible text
   - real `<th>` column headers on every data table
7. **JavaScript only where explicitly specified.** Currently that is exactly one
   place: a `confirm()` dialog on the irreversible "Open Account" action.
8. **All tenant-visible text comes from config.** Never hardcode an institution
   name, label, button caption, column header or menu code in a template.

## Money

Store and compute money as **integer cents**. Never float, never `REAL`.
All rendering goes through `db/money.py`:

```
cents_to_display(1284550) -> "12,845.50"    # no currency symbol, comma grouped
display_to_cents("12,845.50") -> 1284550
```

Rationale: the agent extracts a balance as a typed output. A float rounding
artifact would be a silently wrong answer, which is worse than a crash.

## Data handling

- **Never store a full card number.** The `card` table has `last4` only. There is
  no column anywhere that could hold a PAN.
- A canary string `CANARY-A7F3-DONOTLOG` is seeded in the fixtures. It must never
  appear in any log, artifact, evidence file, or console output. There is a test
  that greps for it.
- Redact before writing, not after. Anything written to `audit_log.detail` must
  already be redacted by the caller.

## Code style

- Type hints on every function signature.
- Row types are `dataclass` or `NamedTuple`. Never leak `sqlite3.Row` upward.
- All SQL is parameterised. Never f-string or `%` a value into SQL.
- `db/queries.py` is the only module that contains SQL.
- Pure functions where practical; keep IO at the edges.
- Docstrings explain *why*, not *what*.

## Testing

- pytest, in `tests/`.
- Every business outcome listed in a capability contract needs a test.
- The single most important test: **replay must run with the Anthropic API key
  unset and an LLM client that raises on any call.** Zero LLM calls on the replay
  path is the central claim of this project; prove it mechanically.
