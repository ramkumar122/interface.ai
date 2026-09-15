"""Re-run verify_artifact on the hand-written artifact and stamp N.

``verified_runs`` means "this replayed cleanly N times with matching
outputs."  It is set only by ``verify_artifact``, never counted from
mixed evidence scenarios.

Two parameter sets, not one.  ``verify_artifact`` refuses a capability
with declared inputs unless a second record is supplied: the same values
twice prove the replay is deterministic and say nothing about whether the
artifact generalises past the record it was discovered from (REPORT §7).
The count it stamps is every run across every set.

Usage:
    HEADLESS=1 python scripts/stamp_verified_runs.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    from artifacts.schema import CapabilityArtifact
    from control.approval import write_artifact
    from playwright.sync_api import sync_playwright
    from replay.verify import verify_artifact
    from surface.web import WebSurface

    path = Path("artifacts/coredesk.member.read_savings_balance@1.json")
    artifact = CapabilityArtifact.model_validate_json(path.read_text())
    n = int(os.environ.get("VERIFY_N", "2"))
    origin = os.environ.get("COREDESK_ORIGIN", "http://127.0.0.1:8001")
    user = os.environ.get("COREDESK_USER", "mreyes")
    pw = os.environ.get("COREDESK_PASS", "demo1234")
    evidence_dir = Path("evidence/verify-stamp")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    inputs = {"member_no": "100101"}
    # A different member, deliberately one whose balance is nothing like
    # 100101's: a pass on 74,209.99 cannot come from 12,845.50 having been
    # baked into the artifact.  Outputs are not compared across sets —
    # each set only has to succeed and fill every declared output.
    also = [{"member_no": "100110"}]

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
                config_path=Path("surfaces/coredesk.yaml"),
                inputs=inputs,
            )
            page.goto(f"{origin}/menu")
            page.wait_for_load_state("networkidle")
            return surface

        vr = verify_artifact(
            artifact, surface_factory, inputs,
            expected_outputs={"savings_balance": "12,845.50"},
            n=n, wait_timeout=10.0, also=also,
        )
        browser.close()

    # `WebSurface` needs somewhere to put evidence, but a clean headless
    # verification produces none — so this script was leaving an empty
    # directory behind every time it ran, which `test_no_empty_evidence_
    # directories` then failed on.  `rmdir` removes an empty directory and
    # nothing else: if a run did capture something, it stays.
    try:
        evidence_dir.rmdir()
    except OSError:
        pass

    if not vr.passed:
        print(f"VERIFICATION FAILED: {vr.failure_summary}", file=sys.stderr)
        return 1

    write_artifact(path, artifact)
    members = ", ".join(
        [inputs["member_no"]] + [s["member_no"] for s in also]
    )
    print(
        f"Stamped verified_runs={artifact.meta.verified_runs} "
        f"across {len(also) + 1} parameter sets (members {members}); "
        f"{n} matching-output run(s) on the first."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
