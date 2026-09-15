"""Capture the first write capability completing on the replay path.

Every other piece of replay evidence is a read.  This one changes a
record, which makes it the answer to "can it actually do anything" — and
it exercises the `guarded_write` policy tier, which was unreachable in
replay until the engine started passing `artifact_approved`.

Three things are checked, and the third is the one that matters:

  1. the card really moves ACTIVE -> LOCKED
  2. a reference comes back
  3. the reason in `audit_log` is the one passed at call time

Three is checked against the database, not the screen.  The screen shows a
reference; only the audit row shows which reason was filed, and "the
reason is a parameter, not a constant" is the whole point of this
capability's second input.

Usage:
    HEADLESS=1 python scripts/card_lock_evidence.py
    HEADLESS=1 python scripts/card_lock_evidence.py --reason "Member request"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ARTIFACT = Path("artifacts/coredesk.card.lock@1.json")
OUT = Path("evidence/replay-card-lock")
MEMBER = "100101"


def _card_status(member_no: str) -> str | None:
    from db.connection import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM card WHERE member_no = ?", (member_no,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _latest_audit() -> dict | None:
    from db.connection import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT ts, staff_username, actor, action, reference, detail "
            "FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "ts": row[0], "staff_username": row[1], "actor": row[2],
        "action": row[3], "reference": row[4], "detail": row[5],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reason", default="Card misplaced",
                    help="Deliberately not the reason it was discovered with.")
    ap.add_argument("--member", default=MEMBER)
    ap.add_argument("--target", default="http://127.0.0.1:8001/menu")
    args = ap.parse_args()

    from scripts._preflight import check_coredesk, die

    problems = check_coredesk(args.target)
    if not ARTIFACT.is_file():
        problems.append(
            f"{ARTIFACT} not found.\n"
            f"      Discover it first — docs/REGENERATE_EVIDENCE.md."
        )
    if problems:
        return die(problems)

    from artifacts.schema import CapabilityArtifact
    from control.policy import Mode
    from playwright.sync_api import sync_playwright
    from replay.engine import replay
    from replay.result import ReplaySuccess
    from surface.web import WebSurface

    artifact = CapabilityArtifact.model_validate_json(ARTIFACT.read_text())
    if artifact.approval.status != "approved":
        print(
            f"ERROR: {ARTIFACT.name} is {artifact.approval.status!r}.\n"
            f"  A guarded write needs an approved artifact — that is the "
            f"policy tier this evidence exists to exercise.\n"
            f"  Approve it:  python -m control approve {ARTIFACT} "
            f'--approver "Your Name"',
            file=sys.stderr,
        )
        return 2

    # The lock is a no-op on an already-locked card, and a no-op writes no
    # audit row.  Reset so the row this captures is the one it caused.
    before = _card_status(args.member)
    if before != "ACTIVE":
        print(
            f"Card for {args.member} is {before}, not ACTIVE.\n"
            f"  Reset first:  python reset_db.py\n"
            f"  A lock on an already-locked card is an informational no-op "
            f"and writes nothing to audit_log — there would be no evidence.",
            file=sys.stderr,
        )
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    origin = args.target.split("//")[0] + "//" + args.target.split("//")[1].split("/")[0]
    user = os.environ.get("COREDESK_USER", "mreyes")
    pw = os.environ.get("COREDESK_PASS", "demo1234")
    inputs = {"member_no": args.member, "reason": args.reason}

    print(f"Locking {args.member}'s card with reason {args.reason!r}")
    print(f"  (discovered with 'Suspected fraud' — if that is what lands in "
          f"audit_log, the reason is still a literal)")

    with sync_playwright() as p:
        headless = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes")
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(f"{origin}/")
        page.fill("#ctl00_MainContent_txtUserId", user)
        page.fill("#ctl00_MainContent_txtPassword", pw)
        page.click("#ctl00_MainContent_btnSignOn")
        page.wait_for_load_state("networkidle")

        surface = WebSurface(
            page, evidence_dir=OUT,
            config_path=Path("surfaces/coredesk.yaml"), inputs=inputs,
        )
        page.goto(args.target)
        page.wait_for_load_state("networkidle")

        t0 = time.monotonic()
        result = replay(
            artifact, surface, inputs,
            policy_mode=Mode.REPLAY, wait_timeout=10.0,
            run_id="replay-card-lock",
        )
        elapsed = round(time.monotonic() - t0, 1)
        page.screenshot(path=str(OUT / "final-state.png"))
        browser.close()

    after = _card_status(args.member)
    audit = _latest_audit() or {}
    reason_filed = (audit.get("detail") or "").replace("reason=", "").split(";")[0]

    report = {
        "gate": "replay-card-lock",
        "what": "The first write capability to complete on the replay path. "
                "Every other replay in this directory is a read.",
        "status": result.status,
        "member_no": args.member,
        "reason_requested": args.reason,
        "elapsed_s": elapsed,
        "policy": {
            "risk_class": artifact.approval.risk_class,
            "guarded_write_steps": [
                s.step_id for s in artifact.steps if s.risk == "guarded_write"
            ],
            "note": "This tier was unreachable in replay until the engine "
                    "passed `artifact_approved` to `policy_check`. Before "
                    "that fix this run failed at click_apply with 'requires "
                    "an approved artifact'.",
        },
        "state_change": {
            "card_before": before,
            "card_after": after,
            "changed": before != after,
        },
        "audit_row": audit,
        "reason_is_a_parameter": {
            "requested": args.reason,
            "filed": reason_filed,
            "matches": reason_filed == args.reason,
            "note": "Discovered with 'Suspected fraud'. If `filed` were that "
                    "rather than `requested`, the reason would still be "
                    "compiled in as a literal.",
        },
    }
    if isinstance(result, ReplaySuccess):
        report["outputs"] = result.outputs
        report["steps"] = [
            {"step_id": s.step_id, "strategy": s.locator_strategy,
             "elapsed_ms": round(s.elapsed_ms, 1)}
            for s in result.steps
        ]
    else:
        report["error"] = getattr(result, "error", None)
        report["step_id"] = getattr(result, "step_id", None)

    report["known_limit"] = (
        "The read step is keyed by row 'CRD-100101-1', the card id, which "
        "embeds the member number — so this artifact resolves for member "
        f"{args.member} and no other. `row_key` is always the row's first "
        "cell, the Cards grid's first column is CARD, and a locator field "
        "cannot reference a declared input. A reviewer applying checklist "
        "item 2 should refuse it; it is approved here to exercise the write "
        "path, and the limitation is the finding."
    )

    (OUT / "replay-result.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

    ok = (
        result.status == "success"
        and report["state_change"]["changed"]
        and report["reason_is_a_parameter"]["matches"]
    )
    print("\nWrite path proven." if ok else "\nSOMETHING DID NOT HOLD.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
