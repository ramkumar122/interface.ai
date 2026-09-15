# The saved artifacts

Byte-identical copies of the two approved capabilities in `artifacts/`.
They are here because the brief asks for the saved artifact in `evidence/`,
and a reviewer opening this directory should not have to know where the
live ones live.

| File | What it is |
|---|---|
| `coredesk.member.read_savings_balance@1.json` | A **read**. Five steps, one input, one money output, two declared outcomes. Replayed by `../replay-success/`, `../replay-determinism/` and `../replay-cross-tenant/`. |
| `coredesk.member.update_address@1.json` | A **write**, `guarded_write`. Six inputs, all parameterised, no frozen literals. Replayed by `../replay-address-update/`. |

Read them against `REPORT.md` §2, which says what a reviewer should be able
to answer from the JSON alone: what it does (`steps[].intent`), what it needs
(`inputs`), what it returns (`outputs`), what else can happen
(`known_outcomes`), and what happens if it cannot finish (`checkpoint`,
`escalation_policy`).

`approval.content_hash` is a SHA-256 over the six fields that affect
execution.  Recompute it and it must match, or replay refuses before a step
runs — see `../approval-gate/`.

**These are copies, so they can drift.**
`tests/test_evidence_hygiene.py::test_the_example_artifacts_match_the_shipped_ones`
asserts they are byte-identical to `artifacts/`, so a stale copy fails the
suite rather than misleading a reader.
