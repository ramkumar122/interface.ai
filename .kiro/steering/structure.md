# Repository Structure

The reviewer reads many submissions side by side and the brief specifies exact
paths. `/README.md`, `/REPORT.md` and `/evidence/` are graded locations — do not
rename or move them.

```
/README.md                  setup, config, and the exact demo commands
/REPORT.md                  design write-up, SEVEN fixed headings (see below)
/evidence/                  artifacts + logs from real runs
/requirements.txt
/reset_db.py                drop, create, seed, print summary
/run_coredesk.py            starts one tenant; TENANT and PORT from env

/coredesk/                  THE TARGET APP
  app.py                    routes; returns HTML only
  config.py                 tenant configuration dicts
  session.py                signed cookie helpers
  inject.py                 runtime-condition injection middleware
  templates/                base.html + one per screen
  static/coredesk.css

/db/
  schema.sql
  fixtures.py               seed data as readable Python literals
  connection.py
  queries.py                the ONLY module containing SQL
  money.py

/agent/                     DISCOVERY (LLM in the loop)
  perceive.py               accessibility tree + screenshot -> observation
  act.py                    the action vocabulary; enforces policy
  loop.py                   observe -> decide -> act
  compile.py                successful transcript -> capability artifact

/replay/                    PRODUCTION PATH (no LLM)
  engine.py
  locate.py                 locator resolution ladder
  detect.py                 runtime condition detection
  result.py                 the result contract

/control/
  session_owner.py          AUTOMATION | HUMAN | NONE, with a real lock
  escalate.py               intervention requests
  policy.py                 allowlist, risk classes, redaction

/artifacts/                 saved capability artifacts (JSON)
/tests/
```

## REPORT.md headings — exactly these seven, in this order

1. Architecture
2. Artifact schema
3. Determinism & error handling
4. Heterogeneity & multi-tenant
5. Escalation & handoff
6. Safety
7. Cuts

## Boundaries to respect

- `/coredesk/` must never import from `/agent/`, `/replay/` or `/control/`.
  The target app does not know it is being automated.
- `/replay/` must never import the Anthropic SDK. Not for types, not for
  anything. If it appears in an import, that is a bug.
- `/agent/` and `/replay/` both go through `/control/policy.py` for allowlist and
  risk checks. Policy is enforced in one place, not duplicated.
- `/db/queries.py` is the only place SQL lives.

## Naming

- Capability ids: `coredesk.<domain>.<verb>` e.g. `coredesk.member.read_savings_balance`
- Artifact files: `artifacts/<capability_id>@<version>.json`
- Evidence files: `evidence/<kind>-<scenario>.jsonl` e.g. `evidence/replay-not-found.jsonl`
