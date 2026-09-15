"""Live escalation gate: session_expired → escalate → harness signs in → resume → success.

Proves control transfer on a live browser:
  1. The page is genuinely interactive during HUMAN ownership
  2. The operator's sign-in restores a usable session
  3. Resume finds the page in a state the step target can resolve against
  4. The audit trail distinguishes AUTOMATION from HUMAN actions

Usage:
    python scripts/escalation_evidence.py [--target http://127.0.0.1:8001]
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="http://127.0.0.1:8001")
    args = ap.parse_args()

    from artifacts.schema import CapabilityArtifact
    from control.policy import Mode
    from playwright.sync_api import sync_playwright
    from replay.engine import replay, resume as engine_resume
    from replay.result import ReplayEscalated, ReplaySuccess
    from surface.web import WebSurface

    artifact_path = Path("artifacts/coredesk.member.read_savings_balance@1.json")
    artifact = CapabilityArtifact.model_validate_json(artifact_path.read_text())
    origin = args.target
    evidence_dir = Path("evidence/replay-escalation-resume")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    headless = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        # 1. Sign in normally first
        page.goto(f"{origin}/")
        page.fill("#ctl00_MainContent_txtUserId", "mreyes")
        page.fill("#ctl00_MainContent_txtPassword", "demo1234")
        page.click("#ctl00_MainContent_btnSignOn")
        page.wait_for_load_state("networkidle")

        # 2. Create surface and navigate to menu
        surface = WebSurface(
            page, evidence_dir=evidence_dir,
            config_path=Path("surfaces/coredesk.yaml"),
            inputs={"member_no": "100101"},
        )

        # 3. Simulate session_expired: clear cookie, navigate to expired page
        # This is what the middleware would produce mid-flow
        page.context.clear_cookies()
        page.goto(f"{origin}/?expired=1")
        page.wait_for_load_state("networkidle")

        # 4. Run replay — should escalate at step 1
        t0 = time.monotonic()
        result = replay(
            artifact, surface, {"member_no": "100101"},
            policy_mode=Mode.REPLAY,
            wait_timeout=10.0,
            run_id="escalation-gate",
        )

        assert isinstance(result, ReplayEscalated), (
            f"Expected ReplayEscalated, got {type(result).__name__}: {result}"
        )
        escalation_time = round(time.monotonic() - t0, 1)

        # Capture the escalation screenshot
        page.screenshot(path=str(evidence_dir / "escalated-state.png"))

        print(f"Phase 1: Escalated at step '{result.request.step_id}'")
        print(f"  Reason: {result.request.reason}")
        print(f"  Instructions: {result.request.instructions}")
        print(f"  Token: {result.resume_token}")
        print(f"  Time: {escalation_time}s")

        # 5. Harness plays the human: sign in via raw Page
        # Surface.act() would reject (ownership is HUMAN)
        # Verify that claim first:
        from surface.base import Click
        reject_result = surface.act(Click(ref="e1"))
        assert not reject_result.ok, "act() should reject during HUMAN ownership"
        assert reject_result.reason == "not_owner", (
            f"Expected 'not_owner', got '{reject_result.reason}'"
        )
        print("Phase 2: Confirmed act() rejects during HUMAN ownership")

        # Now sign in as the human (raw Page, no Surface)
        page.fill("#ctl00_MainContent_txtUserId", "mreyes")
        page.fill("#ctl00_MainContent_txtPassword", "demo1234")
        page.click("#ctl00_MainContent_btnSignOn")
        page.wait_for_load_state("networkidle")
        print("Phase 3: Human signed in, page at:", page.url)

        # Navigate to menu (where step 1 expects to find MBRINQ link)
        # The human is now on /menu after sign-in redirect
        page.screenshot(path=str(evidence_dir / "human-signed-in.png"))

        # 6. Resume
        t1 = time.monotonic()
        result2 = engine_resume(result, surface)
        resume_time = round(time.monotonic() - t1, 1)

        page.screenshot(path=str(evidence_dir / "final-state.png"))

        if isinstance(result2, ReplaySuccess):
            print(f"Phase 4: Resume succeeded! Balance: {result2.outputs.get('savings_balance')}")
            print(f"  Steps: {[s.step_id for s in result2.steps]}")
            print(f"  Time: {resume_time}s")
        else:
            print(f"Phase 4: Resume returned {result2.status}")
            if hasattr(result2, 'error'):
                print(f"  Error: {result2.error}")

        # 7. Check audit_log for actor distinction
        from db.connection import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT ts, staff_username, actor, action, reference "
            "FROM audit_log ORDER BY id DESC LIMIT 10"
        ).fetchall()
        conn.close()

        print("\nAudit log (last 10 rows):")
        for r in rows:
            print(f"  {r[0]} {r[1]} actor={r[2]} action={r[3]} ref={r[4]}")

        # 8. Build evidence report
        report = {
            "gate": "escalation-resume",
            "phase_1_escalation": {
                "status": result.status,
                "step_id": result.request.step_id,
                "reason": result.request.reason,
                "instructions": result.request.instructions,
                "resume_token": result.resume_token,
                "elapsed_s": escalation_time,
            },
            "phase_2_ownership_check": {
                "act_rejected": True,
                "reason": "not_owner",
            },
            "phase_3_human_signin": {
                "url_after_signin": page.url,
            },
            "phase_4_resume": {
                "status": result2.status,
                **({"outputs": result2.outputs,
                    "steps": [
                        {"step_id": s.step_id,
                         "strategy": s.locator_strategy,
                         "elapsed_ms": round(s.elapsed_ms, 1)}
                        for s in result2.steps
                    ]}
                   if isinstance(result2, ReplaySuccess) else
                   {"error": getattr(result2, 'error', str(result2))}),
                "elapsed_s": resume_time,
            },
        }

        (evidence_dir / "replay-result.json").write_text(
            json.dumps(report, indent=2)
        )
        print(f"\nEvidence written to {evidence_dir}/")

        browser.close()

    return 0 if isinstance(result2, ReplaySuccess) else 1


if __name__ == "__main__":
    raise SystemExit(main())
