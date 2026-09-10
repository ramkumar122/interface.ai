# CoreDesk (mock)

CoreDesk is a mock legacy credit-union servicing web app. It intentionally
looks and behaves like enterprise software written around 2006 and patched
since: full-page loads, nested-table layout, server-rendered HTML only, and
ASP.NET WebForms-style element ids. It is used as the target application for a
computer-use automation exercise.

FastAPI is used strictly as an HTML page server. There are **no** JSON
endpoints, no client-side routing, and no JavaScript.

## Stack

- Python 3.12
- FastAPI + Uvicorn (HTML page server only)
- Jinja2 templates
- Plain CSS (one stylesheet)
- SQLite via the stdlib `sqlite3` (no ORM). All money is stored as INTEGER
  CENTS and rendered through `db/money.py`; no floats anywhere in the money path.

## Multi-tenant

The same codebase serves two tenants, selected by the `TENANT` environment
variable:

| Tenant      | Key         | Port | Institution                    | Accent |
|-------------|-------------|------|--------------------------------|--------|
| A           | `riverbend` | 8001 | Riverbend Credit Union         | navy   |
| B           | `summit`    | 8002 | Summit Federal Credit Union    | maroon |

All tenant-specific values live in `coredesk/config.py`; templates hardcode
nothing.

## Project layout

```
run_coredesk.py     starts one tenant; TENANT and PORT from env
reset_db.py         drop, create, seed, print summary
coredesk/           the target app (routes, config, session, templates, static)
db/                 schema, fixtures, connection, queries (only SQL lives here), money
tests/              pytest suite
```

The target app under `coredesk/` is self-contained and does not know it is
being automated. `agent/`, `replay/`, `control/`, `artifacts/`, and `evidence/`
are placeholders for later tasks.

## Install

Requires Python 3.12.

```bash
cd "Computer-Use Automation"
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Database

Sign-on and all member data are backed by a single SQLite file, `coredesk.db`,
at the repo root. Both tenants share this one file; tenant differences are
presentation only.

Build (or rebuild) it before running the app:

```bash
source .venv/bin/activate
python reset_db.py
```

`reset_db.py` drops the file, recreates the schema (`db/schema.sql`), seeds the
fixtures (`db/fixtures.py`), and prints a summary (row counts plus the 20
seeded members with status, share count, and card count). It is idempotent:
running it twice produces identical data.

Data-layer layout:

- `db/schema.sql` — DDL (tables + indexes)
- `db/fixtures.py` — seed data as readable Python literals
- `db/connection.py` — connection helper (row factory + foreign keys ON)
- `db/queries.py` — all SQL (reads, writes, seed); returns typed rows
- `db/money.py` — `cents_to_display` / `display_to_cents`

Passwords are hashed with stdlib PBKDF2-HMAC-SHA256 (per-user salt stored in
the hash string). No bcrypt/passlib dependency.

## Tests

```bash
source .venv/bin/activate
pytest
```

Tests run against a throwaway database (via the `COREDESK_DB` environment
variable) and never touch `coredesk.db`.

## Run both tenants (two terminals)

Terminal 1 — Tenant A (Riverbend, navy) on port 8001:

```bash
source .venv/bin/activate
TENANT=riverbend PORT=8001 python run_coredesk.py
```

Terminal 2 — Tenant B (Summit, maroon) on port 8002:

```bash
source .venv/bin/activate
TENANT=summit PORT=8002 python run_coredesk.py
```

Then open:

- http://127.0.0.1:8001/  (Riverbend)
- http://127.0.0.1:8002/  (Summit)

`TENANT` and `PORT` both default to `riverbend` / `8001` when unset. If you set
`TENANT=summit` without a `PORT`, it defaults to `8002`.

## Demo logins

| User ID  | Password   | Display   | Role        | Branch |
|----------|------------|-----------|-------------|--------|
| `mreyes` | `demo1234` | M. REYES  | MSR         | 001    |
| `jtran`  | `demo1234` | J. TRAN   | TELLER_RO   | 004    |
| `dpark`  | `demo1234` | D. PARK   | SUPERVISOR  | 001    |

## Configuration

- `COREDESK_SECRET` — secret used to sign the session cookie. A development
  default is used when unset; set a real value for any non-local use.
- `COREDESK_DB` — path to the SQLite file. Defaults to `coredesk.db` at the
  repo root. Tests set this to a temporary file.

## Navigation

- `GET /` — sign-on. Redirects to `/menu` if already signed on.
- `POST /signon` — validates credentials, sets a signed session cookie, and
  redirects to `/menu`. On failure it redirects back to `/?err=1` without
  revealing which field was wrong.
- `GET /menu` — the main menu (requires a session).
- `POST /menu` — fast-path box: enter a function code (case-insensitive) to
  jump straight to that screen; unknown codes re-render the menu with an error.
- `GET /signoff` — clears the session and returns to sign-on.
- Function screens (`/mbrinq`, `/member/cards`, `/member/address`,
  `/member/shares/new`, `/member/transactions`) are stubs for now and show
  "FUNCTION NOT YET IMPLEMENTED".
