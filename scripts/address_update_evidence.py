"""Capture the address-update capability completing on the replay path.

The second write capability, and the first where N-run verification works
without special handling: setting the same address twice yields the same
state and the same output, so `--verify-runs 2` is safe.  `card.lock` is
not like that — its second run hits an already-locked card — which is why
idempotency is a property worth checking per capability rather than
assuming.

Three things are checked, and the third is the one that matters:

  1. the member's address really changes to the values passed in
  2. an audit row records the change
  3. the address stored is the one passed at **call time**, not one baked
     into the artifact

Three is the point.  A capability that always writes the address it was
discovered with would look identical on screen — the reference comes back,
the banner appears — and be silently wrong.  So the check compares the
database against the inputs, not against the page.

The output is `confirmed_city`, read from the review screen's NEW column
before the commit.  Not the `ADR-#####` reference: that is newly generated
on every run, so a capability returning it could never pass the
cross-run stability check that verification performs.

Usage:
    HEADLESS=1 python scripts/address_update_evidence.py
    HEADLESS=1 python scripts/address_update_evidence.py --city Phoenix
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ARTIFACT = Path("artifacts/coredesk.member.update_address@1.json")
OUT = Path("evidence/replay-address-update")
MEMBER = "100101"


def _address(member_no: str) -> dict | None:
    from db.connection import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT addr_line1, city, state, zip FROM member WHERE member_no = ?",
            (member_no,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"street": row[0], "city": row[1], "state": row[2], "zip": row[3]}


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
    # Deliberately not the values it was discovered with.
    ap.add_argument("--street", default="88 Cedar Lane")
    ap.add_argument("--city", default="Portland")
    ap.add_argument("--state", default="OR")
    ap.add_argument("--zip", dest="zip_code", default="97205")
    ap.add_argument("--effective-date", default="11/01/2026")
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
            f"  A guarded write needs an approved artifact.\n"
            f"  Approve it:  python -m control approve {ARTIFACT} "
            f'--approver "Your Name"',
            file=sys.stderr,
        )
        return 2

    before = _address(args.member)
    inputs = {
        "member_no": args.member,
        "street": args.street,
        "city": args.city,
        "state": args.state,
        "zip": args.zip_code,
        "effective_date": args.effective_date,
    }
    if before == {k: inputs[k] for k in ("street", "city", "state", "zip")}:
        print(
            f"Member {args.member} already has this address, so the run would "
            f"prove nothing.\n  Reset first:  python reset_db.py",
            file=sys.stderr,
        )
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    origin = args.target.split("//")[0] + "//" + args.target.split("//")[1].split("/")[0]
    user = os.environ.get("COREDESK_USER", "mreyes")
    pw = os.environ.get("COREDESK_PASS", "demo1234")

    print(f"Setting {args.member}'s address to {args.street}, {args.city}")
    print(f"  discovered with '1216 E Vista Del Cerro Dr, Tempe' — if that is "
          f"what lands, the values are baked into the artifact")

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
            run_id="replay-address-update",
        )
        elapsed = round(time.monotonic() - t0, 1)
        page.screenshot(path=str(OUT / "final-state.png"))
        browser.close()

    after = _address(args.member)
    audit = _latest_audit() or {}
    wanted = {k: inputs[k] for k in ("street", "city", "state", "zip")}

    report = {
        "gate": "replay-address-update",
        "what": "The second write capability, and the first where N-run "
                "verification needs no special handling — setting the same "
                "address twice yields the same state and the same output.",
        "status": result.status,
        "member_no": args.member,
        "elapsed_s": elapsed,
        "policy": {
            "risk_class": artifact.approval.risk_class,
            "guarded_write_steps": [
                s.step_id for s in artifact.steps if s.risk == "guarded_write"
            ],
        },
        "state_change": {
            "address_before": before,
            "address_after": after,
            "changed": before != after,
        },
        "audit_row": audit,
        "values_are_parameters": {
            "requested": wanted,
            "stored": after,
            "matches": after == wanted,
            "note": "Discovered with '1216 E Vista Del Cerro Dr, Tempe, AZ "
                    "85281'. If `stored` were that rather than `requested`, "
                    "the address would be baked into the artifact. All six "
                    "inputs are parameterised, including the effective date "
                    "— which was a frozen literal until `review_required` "
                    "surfaced it in cli review.",
        },
        "idempotency": {
            "verify_runs": artifact.meta.verified_runs,
            "note": "Safe at N=2: the same address applied twice is the same "
                    "state and the same output. card.lock is not idempotent "
                    "in the same way — its second run meets an already-locked "
                    "card — so this is a property to check per capability, "
                    "not to assume.",
        },
        "output_choice": {
            "returns": "confirmed_city",
            "not": "the ADR-##### reference",
            "why": "The reference is generated fresh per run, so a capability "
                   "returning it could never pass verification's cross-run "
                   "stability check.",
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

    (OUT / "replay-result.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))

    ok = (
        result.status == "success"
        and report["state_change"]["changed"]
        and report["values_are_parameters"]["matches"]
    )
    print("\nWrite path proven." if ok else "\nSOMETHING DID NOT HOLD.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
