# Regenerating `evidence/`

`evidence/` is committed, so a clone already has it.  This is how to rebuild
it from a clean database — before submission, or after a change that could
alter what a run produces.

**Order matters.**  Steps 1–11 are free and involve no model.  Step 12 costs
API credits and needs a person.  A failure early therefore costs nothing,
which is the point of running them in this order.

Total: roughly 15 minutes of wall time, and one discovery run's worth of
credits (~15k tokens) for step 12.

---

## Prerequisites

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # ~275 MB, once
python reset_db.py                   # drop, create, seed
```

Long-running processes, each in its own terminal:

```bash
make coredesk           # Riverbend on :8001 — everything needs this
make coredesk-summit    # Summit on :8002 — only step 8
make console            # :8010 — only step 12
```

Step 12 needs a browser window a person can type into, so do **not** set
`HEADLESS` for it.  Everything else accepts `HEADLESS=1` and runs faster
without a window flashing up — step 11's script plays the human itself.

For step 12 only:

```bash
export GOOGLE_API_KEY=...            # or put it in .env at the repo root
export GEMINI_MODEL=gemini-3.6-flash # optional; this is the default
```

Every script checks CoreDesk is reachable **and that it accepts
`COREDESK_USER`/`COREDESK_PASS`** before doing anything, and exits `2` with
the reason.  A wrong password costs you a message, not a run.

---

## Steps 1–7 — the replay result taxonomy

```bash
HEADLESS=1 python scripts/replay_evidence.py --variant success
HEADLESS=1 python scripts/replay_evidence.py --variant business-outcome
HEADLESS=1 python scripts/replay_evidence.py --variant no-savings
HEADLESS=1 python scripts/replay_evidence.py --variant failure
HEADLESS=1 python scripts/replay_evidence.py --variant db-timeout
HEADLESS=1 python scripts/replay_evidence.py --variant session-expired
HEADLESS=1 python scripts/replay_evidence.py --variant determinism
```

| Variant | Directory | Expect |
|---|---|---|
| `success` | `replay-success/` | `status: success`, `savings_balance: 12,845.50` |
| `business-outcome` | `replay-business-outcome/` | `status: business_outcome`, `outcome_id: MEMBER_NOT_FOUND`. Takes ~12s — the wait for search results times out first, which is the detector doing its job |
| `no-savings` | `replay-no-savings/` | `outcome_id: NO_SAVINGS_ACCOUNT` on member 100102. No error text on the page: detected by the shares table being present *and* row `0000` absent |
| `failure` | `replay-failure/` | `status: failure`, `APP_ERROR` in the diagnostic |
| `db-timeout` | `replay-db-timeout/` | `status: success` **with** a `recoveries` entry (`db_timeout`, `retry`, attempt 1). Success alone is not the result — a recovery that leaves no trace is indistinguishable from nothing having happened |
| `session-expired` | `replay-session-expired/` | `status: escalated`, `instructions` naming the step and the member, plus `001-escalation.png` |
| `determinism` | `replay-determinism/` | `all_passed: true` across 100110 (`74,209.99`), 100111 (`0.00`), 100115 (`2,405.60`) — comma grouping, a zero that must not render blank, a non-ASCII name |

Anything unexpected in step 1 means CoreDesk is not seeded — run
`python reset_db.py`.

## Step 8 — `replay-cross-tenant`

**Needs Summit on :8002.**

```bash
HEADLESS=1 python scripts/cross_tenant_evidence.py
```

**Expect** both tenants returning `12,845.50`.  If Summit times out, the
`overrides.summit` block is not being applied — check that terminal is
really running `TENANT=summit`.

## Step 9 — `verify-gate-wrong-value`

```bash
HEADLESS=1 python scripts/verify_gate.py --variant wrong-value
```

**Expect** failure, and specifically:

```
'OPEN' is not valid money (cannot parse as money: 'OPEN')
```

The column header is changed to `STATUS`, which **resolves cleanly** — no
locate error — and returns `OPEN` where money was expected.  A pass here
would mean the gate is broken.

Other variants exist (`clean`, `break`, `wrong`) and are not part of the
shipped set.  `wrong` uses `LEDGER`, which passes on 100101 because that
share's available and ledger balances are equal — the reason the STATUS
variant replaced it.

## Step 10 — `approval-gate`

```bash
python scripts/approval_gate_evidence.py
```

No browser, no CoreDesk, no model.  **Expect** all four phases holding:

```
Phase 1  approved + valid hash  -> check_passed=True
Phase 2  unverified             -> approve_blocked=True
Phase 3  tampered               -> replay=failure at (pre-replay)
Phase 4  writing the tampered   -> write_blocked=True
All four gates held.
```

It works on copies in a temp directory and re-checks the shipped artifact is
byte-identical afterwards.  If it says a gate did not hold, read the report
before doing anything else.

## Step 10b — `replay-address-update`

```bash
python reset_db.py
HEADLESS=1 python scripts/address_update_evidence.py
```

Needs `artifacts/coredesk.member.update_address@1.json` **approved** — a
guarded write is refused otherwise, which is the policy tier working.

**Expect** `Write path proven.`, and in the report `state_change` showing a
different `address_before` and `address_after`, `audit_row` with
`actor: AUTOMATION`, plus `values_are_parameters.matches: true`.  That last
one is the assertion: a match proves the address is a parameter rather than
values baked into the artifact.

**This changes member 100101's address.**  Run `python reset_db.py`
afterwards before relying on fixture state.

## Step 11 — `replay-escalation-resume`

```bash
HEADLESS=1 python scripts/escalation_evidence.py
```

The full control transfer.  **Expect** four phases ending
`Phase 4: Resume succeeded! Balance: 12,845.50`, and four screenshots.
Phase 2 is the one that matters: `act()` is confirmed to refuse while
ownership is `HUMAN`.

The script plays the human in this variant.  For the version where *you* are
the human, use step 12's console flow.

## Step 12 — the console flow (**costs credits**)

Needs `make console` running **not** headless, and `GOOGLE_API_KEY`.

```bash
python scripts/console_evidence.py --capture approval-gate   # free
python scripts/console_evidence.py --capture escalation      # manual, free
python scripts/console_evidence.py --capture discovery       # credits
python scripts/console_evidence.py --check                   # verify only
```

Run `approval-gate` first: it is free and exercises the same console
process, so a broken setup surfaces before credits are spent.

`approval-gate` seeds `read_savings_balance@2.0` as a draft and approves it
through the console's own route, which leaves that artifact **approved** and
superseding v1.0 in the catalog.  Decide deliberately whether to ship it
that way or revert it to draft.

On `discovery`, watch the verification step.  The form collects a **second
parameter set** — verification refuses a capability with declared inputs and
only one record, and that refusal lands after the model has already run.

---

## Afterwards

Rebuild `evidence/README.md` so every directory present is named in it, then:

```bash
pytest tests/test_evidence_hygiene.py
```

That checks every directory is indexed, none is empty, console run
directories are not committed wholesale, and the copies in
`evidence/example-artifact/` are byte-identical to `artifacts/`.

Delete any empty directory a partial run left behind — name each one in
full, one command per line, no globs.
