# Evidence index

This directory is **committed**, not gitignored.  The brief asks for a saved
artifact plus logs from a discovery run, a replay run, and a replay that hits
an error — so a reviewer should be able to open these files without running
anything.  `REPORT.md` §4, §5 and §6 cite paths inside here.

`docs/REGENERATE_EVIDENCE.md` is how to rebuild it all from a clean database.
That runbook is the instructions, not a substitute for shipping the files.

## Read these four first

Fifteen directories is more than anyone opens.  These four are the system;
everything after them is a supporting case.

1. **`example-artifact/`** — the two approved capabilities as JSON. What a
   risk reviewer actually signs off: typed inputs, typed outputs, named
   locators, declared outcomes, a content hash.
2. **`replay-success/`** — the production path. That artifact replayed
   against live CoreDesk: `12,845.50`, zero tokens, 1.8 seconds, no model
   in the loop.
3. **`replay-address-update/`** — a **write** completing, and proven
   portable: the stored values are the ones passed at call time, not the
   ones it was discovered with, across two members in different cities.
4. **`replay-escalation-resume/`** — the loop closing. Automation stops, a
   human takes the session, hands it back, and the capability finishes. The
   audit log names who did which half.

Then `operator-console/discovery-flow/` for the whole arc in one place:
a sentence of English in, an approved capability out.

The tables below are the rest.

## Kept — live discovery runs, each refused at a gate

Real model runs against live CoreDesk, kept because they cost credits and
because each shows a gate refusing something a test cannot stage.  Cited in
`REPORT.md` §3 and §7.

| Directory | What happened |
|---|---|
| `discover-gemini-3.6-flash-b05eb401/` | **Act one.** Compilation crashed. Surfaced two locator-ladder bugs: a banner paragraph carries its text as content rather than as an accessible name, and `cell "LOCKED"` was taken as a locator name — which is the value the step reads. Both fixed, so this exact crash can no longer be reproduced. |
| `discover-gemini-3.6-flash-85f552ae/` | **Act two,** after the fix. Compiled cleanly, 11 steps, `card_status: LOCKED`. No artifact: the run passed `--verify-runs 0` and the writer keeps nothing unverified. |
| `discover-gemini-3.6-flash-4357e668/` | **The record-data rule catching a live run.** An address capability: 15 steps, 49,224 prompt tokens, the model reached `done` and filled its output. Compilation then refused it — `cannot locate cell "1216 E Vista del Cerro Dr": its accessible name is the value it reads, and no table context or named region offers a structural alternative`. Every other gate in this directory is demonstrated against a staged fixture; this is the rule from `REPORT.md` §3 refusing a real discovery, after the model had already succeeded. |

## Replay — the result taxonomy against live CoreDesk

| Directory | What it demonstrates | Requirement |
|---|---|---|
| `replay-success/` | The happy path. `status: success`, `savings_balance: 12,845.50`, zero tokens. | Replay |
| `replay-business-outcome/` | `MEMBER_NOT_FOUND` on member 999999 — a declared outcome, reported as an answer rather than an error. | Error case |
| `replay-no-savings/` | `NO_SAVINGS_ACCOUNT` on member 100102, detected structurally: shares table present *and* row `0000` absent, with no page-level message to match on. | Error case |
| `replay-failure/` | A hard failure — `status: failure`, `APP_ERROR` in the diagnostic. | Error case |
| `replay-db-timeout/` | Recovery that works. `status: success` **with** a `recoveries` entry on one step. A recovery that leaves no trace is indistinguishable from nothing having happened. | Replay, Error case |
| `replay-session-expired/` | Escalation as a recovery action. `status: escalated`, instructions naming the step and the member, plus `001-escalation.png`. | Escalation |
| `replay-escalation-resume/` | **The full control transfer.** Four phases: the engine escalates and releases the lock, `Surface.act()` is confirmed to refuse while ownership is `HUMAN`, a person signs in on the raw page, and `resume()` finishes the capability — `12,845.50`. The audit log shows where automation stopped. | Escalation |
| `replay-determinism/` | One artifact, three members — 100110 (`74,209.99`), 100111 (`0.00`), 100115 (`2,405.60`). Comma grouping, a zero that must not render blank, a non-ASCII name. | Verify |
| `replay-cross-tenant/` | Same artifact, two tenants. Riverbend and Summit both return `12,845.50` through the `overrides.summit` block. | Heterogeneity |
| `replay-address-update/` | **The write path.** `state_change` from Springfield OR to Portland OR, `audit_row` with `actor: AUTOMATION ADDRESS_UPDATE`, and `values_are_parameters.matches: true` — the stored values are the ones passed at call time, not the ones it was discovered with. | Replay, Safety, Verify |

## The artifact itself

| Directory | What it holds | Requirement |
|---|---|---|
| `example-artifact/` | The two approved capabilities, byte-identical copies of `artifacts/`. A read and a `guarded_write`. A test fails if they drift. | Artifact |

## Gates — the refusals, which are results too

| Directory | What it demonstrates | Requirement |
|---|---|---|
| `approval-gate/` | Four phases holding: approved+valid hash passes, unverified blocks approval, a tampered artifact fails at `(pre-replay)`, and writing the tampered one is blocked. Works on copies in a temp directory and re-checks the shipped artifact is byte-identical afterwards. | Safety |
| `verify-gate-wrong-value/` | **The silent-wrong-answer catch.** The balance column is renamed `STATUS`, which resolves cleanly — no locate error — and returns `OPEN` where money was expected. Diagnostic: `'OPEN' is not valid money`. A pass here would mean the gate is broken. | Verify |

## Still to produce

`operator-console/` — the console's own captures (discovery flow, approval-gate
flow, escalation handoff).  Steps 11 and 12 of the runbook: one needs a person
at the browser window, the other spends API credits.

## Notes

`replay-card-lock/` is gone.  The tightened rule on record data in generated
fields refuses to compile `card.lock`, whose row key held a member number
inside a card id, and the capability was revoked.  See `REPORT.md` §7.

Earlier runs named during development — `discover-gemini-3.6-flash-65b48ef0`,
`…-ff82c09c`, `…-74d8fcba` — are **not retained**: they were accurate when
written and record what was decided at the time, not instructions to follow
now.  One is still reachable — `65b48ef0`'s transcript is the pinned
compiler fixture at `tests/fixtures/discovery-transcript.jsonl`, with its
summary beside it, moved there because the test suite must not depend on a
directory whose purpose is to be regenerated.

`pytest tests/test_evidence_hygiene.py` checks that every directory present is
named in this file, that none is empty, and that console run directories are
not committed wholesale.
