# Computer-use automation for CoreDesk

Two programs in one repo:

1. **CoreDesk** — a mock legacy credit-union servicing app (the target).
   Nested tables, ASP.NET-style ids, no JSON API, two tenant configs of
   the same product.
2. **The automation system** — an LLM discovers how to complete a goal
   once, compiles a versioned capability artifact, and **replays that
   artifact with no model in the decision loop**.

> The model discovers. The artifact becomes a reusable capability.
> Deterministic replay is how an AI agent invokes it in production.

## Setup

Python 3.12.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium     # ~275 MB, once
python reset_db.py
```

Discovery needs a model; replay does not.

```bash
export GOOGLE_API_KEY=...        # https://aistudio.google.com/apikey
```

A repo-root `.env` is loaded if present (gitignored). **Never commit a key.**

| Variable | Default | Purpose |
|---|---|---|
| `GOOGLE_API_KEY` | — | Discovery only. Replay never reads it. |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Discovery model |
| `TENANT` / `PORT` | `riverbend` / `8001` | Which CoreDesk to start |
| `COREDESK_USER` / `COREDESK_PASS` | `mreyes` / `demo1234` | Harness sign-in (never sent to the model) |
| `COREDESK_DB` | `coredesk.db` | SQLite path (tests use a temp file) |

Demo logins: `mreyes` (MSR), `jtran` (TELLER_RO), `dpark` (SUPERVISOR).
Password for all three: `demo1234`.

## Run it

Two terminals:

```bash
make coredesk     # the target app  → http://127.0.0.1:8001
make console      # the operator UI → http://127.0.0.1:8010
```

A third, only for the cross-tenant demo:

```bash
make coredesk-summit    # → http://127.0.0.1:8002
```

## What to look at

Visit these in order. **The order is the architecture**: language goes in at
discovery, typed parameters go in at invocation, and a human gate sits
between them.

**1. `/discover`** — the natural-language entry point. Type a goal; a model
works out how to do it, once. Ends at compile → verify → draft.

**2. `/review`** — the human gate. The steps in plain English, a checklist
that explains what to check, and the artifact source byte for byte. Nothing
runs unattended until this passes.

**3. `/`** — the catalog. Approved capabilities only. Pick one, fill the
typed inputs, Run.

Two capabilities are approved. `read_savings_balance` reads.
`update_address` writes — it changes a member's address and returns the
confirmed value, and it was verified against two members in different
cities, so it works for records other than the one it was recorded from.

Two notes:

- No API key? `/discover/rehearse` replays a saved discovery run turn by
  turn, with no model and no browser.
- To see the escalation handoff: start a run, toggle `session_expired` at
  http://127.0.0.1:8001/admin/inject while it is in flight, sign in by hand
  in the browser window, then press **Resume** in the console.

To put something in the review queue: `make seed-draft`.

## Without the browser

```bash
make test
```

Browser tests are marked `live` and skip loudly when Chromium or CoreDesk is
missing. Replay tests unset the API key and inject a client that raises on
call.

```bash
make test-live    # the live ones, for real (needs CoreDesk on :8001)
```

## Terminal equivalents

The same operations without the console.

```bash
# Discover a capability. Verification needs a second parameter set — the
# same values twice prove determinism, not that it works for another
# record. --verify-runs is replays per set; verified_runs counts them all.
python scripts/run_discover.py \
  --goal "Find member 100101 and read their primary savings (SAVINGS suffix 0000) available balance." \
  --target "http://127.0.0.1:8001/menu" \
  --inputs '{"member_no": "100101"}' \
  --verify-inputs-2 '{"member_no": "100110"}' \
  --outputs '{"savings_balance": "money"}' \
  --capability-id coredesk.member.read_savings_balance \
  --verify-runs 2

# Replay the shipped artifact — no API key, no model.
python scripts/replay_evidence.py --variant success

# Other outcomes from the same artifact.
python scripts/replay_evidence.py --variant business-outcome   # 999999 → MEMBER_NOT_FOUND
python scripts/replay_evidence.py --variant no-savings         # 100102 → NO_SAVINGS_ACCOUNT
python scripts/replay_evidence.py --variant failure            # app_error
python scripts/replay_evidence.py --variant db-timeout         # recovery, visible in the trace
python scripts/replay_evidence.py --variant session-expired    # escalation
python scripts/replay_evidence.py --variant determinism        # three members, one artifact

# A write that completes, verified across two members.
python scripts/address_update_evidence.py

# Review and approve.
python -m control review artifacts/coredesk.member.read_savings_balance@1.json

# Same artifact, two tenants (needs Summit on :8002).
python scripts/cross_tenant_evidence.py
```

Set `HEADLESS=1` on any of these for CI or unattended runs.

`docs/REGENERATE_EVIDENCE.md` rebuilds `evidence/` from scratch.

## Layout

```
README.md  REPORT.md  evidence/     graded locations — do not rename
artifacts/                          versioned capability JSON
agent/                              discovery (LLM). Never imports playwright.
operator_console/                   the reviewer's UI on :8010. Imports no coredesk.
replay/                             production path. Never imports google.genai.
surface/                            the only Playwright import
control/                            policy, approval, ownership, escalation
coredesk/  db/                      the target app. Imports none of the above.
scripts/                            harnesses (sign-in, evidence, CLI)
```

## Further reading

- `REPORT.md` — design write-up (seven fixed headings)
- `evidence/README.md` — which run to open, and why
- `docs/REGENERATE_EVIDENCE.md` — how to rebuild `evidence/` from a clean database
- `docs/diagrams/` — the three sequence diagrams, with mermaid sources
