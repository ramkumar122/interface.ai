"""Gate script: compile the real transcript, mutate the artifact, verify.

Proves the write-suppression path end-to-end against a live CoreDesk.
No LLM needed — verification is replay-only.

Usage:
    python scripts/verify_gate.py --variant break   # nonexistent column
    python scripts/verify_gate.py --variant wrong    # LEDGER on savings
    python scripts/verify_gate.py --variant clean    # unmodified (should pass)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_compile import _load_records, _make_result
from agent.compile import compile_transcript


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--variant",
        choices=["break", "wrong", "wrong-value", "clean"],
        required=True,
    )
    ap.add_argument("--target", default="http://127.0.0.1:8001/menu")
    args = ap.parse_args()

    from scripts._preflight import check_coredesk, die

    problems = check_coredesk(args.target)
    if problems:
        return die(problems)

    # 1. Compile the real transcript
    records = _load_records()
    result = _make_result(records)
    artifact = compile_transcript(
        records, result,
        capability_id="coredesk.member.read_savings_balance",
        inputs={"member_no": "100101"},
        outputs={"savings_balance": "money"},
        description="Read savings balance",
    )
    print(f"Compiled: {len(artifact.steps)} steps")

    # 2. Mutate
    if args.variant == "break":
        artifact.steps[4].target.column_header = "AVAILABLE BALANCE"
        print("Mutated: column_header -> 'AVAILABLE BALANCE' (nonexistent)")
    elif args.variant == "wrong":
        artifact.steps[4].target.column_header = "LEDGER"
        print("Mutated: column_header -> 'LEDGER' (resolves but wrong value)")
    elif args.variant == "wrong-value":
        # The sharp catch (REPORT §3).  STATUS resolves cleanly and returns
        # "OPEN" where money was expected: no locate error, so only output
        # comparison and the `display_to_cents` coercion catch it.  This is
        # the variant that replaced LEDGER, which passed green on 100101
        # because that share's available and ledger balances are identical.
        artifact.steps[4].target.column_header = "STATUS"
        print("Mutated: column_header -> 'STATUS' "
              "(resolves cleanly, returns 'OPEN' where money was expected)")

    # 3. Verify against live CoreDesk
    from playwright.sync_api import sync_playwright
    from replay.verify import verify_artifact
    from surface.web import WebSurface

    inputs = {"member_no": "100101"}
    # A second record, because `verify_artifact` refuses a capability with
    # declared inputs and only one parameter set — the same tightened gate
    # that revoked card.lock.  Without this the script is refused before it
    # reaches the comparison it exists to demonstrate, and the evidence
    # reads as "no second set" rather than "the gate caught a wrong value".
    also = [{"member_no": "100110"}]
    config_path = Path(__file__).resolve().parents[1] / "surfaces" / "coredesk.yaml"
    evidence_dir = Path("evidence") / f"verify-gate-{args.variant}"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    origin = args.target.split("//")[0] + "//" + args.target.split("//")[1].split("/")[0]
    user = os.environ.get("COREDESK_USER", "mreyes")
    pw = os.environ.get("COREDESK_PASS", "demo1234")

    with sync_playwright() as p:
        headless = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes")
        browser = p.chromium.launch(headless=headless)

        def surface_factory():
            page = browser.new_page()
            page.goto(f"{origin}/")
            page.fill("#ctl00_MainContent_txtUserId", user)
            page.fill("#ctl00_MainContent_txtPassword", pw)
            page.click("#ctl00_MainContent_btnSignOn")
            page.wait_for_load_state("networkidle")
            surface = WebSurface(
                page, evidence_dir=evidence_dir,
                config_path=config_path, inputs=inputs,
            )
            page.goto(args.target)
            page.wait_for_load_state("networkidle")
            return surface

        vr = verify_artifact(
            artifact, surface_factory, inputs,
            expected_outputs=result.outputs_filled,
            n=1, wait_timeout=10.0, also=also,
        )
        browser.close()

    # 4. Report
    from replay.result import (
        ReplaySuccess, ReplayFailure, ReplayBusinessOutcome, ReplayEscalated,
    )

    def _run_detail(r):
        detail = {"status": r.status}
        if isinstance(r, ReplaySuccess):
            detail["outputs"] = r.outputs
            detail["steps"] = [
                {"step_id": s.step_id, "elapsed_ms": round(s.elapsed_ms, 1)}
                for s in r.steps
            ]
        elif isinstance(r, ReplayFailure):
            detail["error"] = r.error
            detail["step_id"] = r.step_id
            detail["steps"] = [
                {"step_id": s.step_id, "elapsed_ms": round(s.elapsed_ms, 1)}
                for s in r.steps
            ]
        elif isinstance(r, ReplayEscalated):
            detail["step_id"] = r.request.step_id
            detail["reason"] = r.request.reason
            detail["resume_token"] = r.resume_token
        elif isinstance(r, ReplayBusinessOutcome):
            detail["outcome_id"] = r.outcome_id
            detail["at_step"] = r.at_step
            detail["caller_action"] = r.caller_action
        return detail

    out = {
        "variant": args.variant,
        "passed": vr.passed,
        "failure_summary": vr.failure_summary,
        "runs": len(vr.runs),
        "run_details": [_run_detail(r) for r in vr.runs],
    }
    report_path = evidence_dir / "verify-result.json"
    report_path.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))

    # 5. Would-write check
    artifact_path = Path("artifacts") / f"{artifact.capability_id}@{artifact.version}.json"
    if vr.passed:
        print(f"\n✓ Verification PASSED — artifact would be saved to {artifact_path}")
    else:
        print(f"\n✗ Verification FAILED — artifact NOT saved")
        print(f"  Diagnostic: {vr.failure_summary}")

    return 0 if vr.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
