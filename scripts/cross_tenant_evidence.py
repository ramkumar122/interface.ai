"""Gate: one artifact, two tenants, both succeed.

Riverbend uses the base locators.  Summit applies the artifact's
``overrides.summit`` block (menu code, field label, select link, column
header).  A positional locator would read the wrong results-grid cell on
Summit with no error; these locators are by role and name.

Usage:
    HEADLESS=1 python scripts/cross_tenant_evidence.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _sign_in(page, origin: str) -> None:
    user = os.environ.get("COREDESK_USER", "mreyes")
    pw = os.environ.get("COREDESK_PASS", "demo1234")
    page.goto(f"{origin}/")
    page.fill("#ctl00_MainContent_txtUserId", user)
    page.fill("#ctl00_MainContent_txtPassword", pw)
    page.click("#ctl00_MainContent_btnSignOn")
    page.wait_for_load_state("networkidle")


def _run(
    artifact, browser, origin: str, tenant: str, evidence_dir: Path,
) -> dict:
    from control.policy import Mode
    from replay.engine import replay
    from replay.result import ReplaySuccess
    from surface.web import WebSurface

    page = browser.new_page()
    _sign_in(page, origin)
    page.goto(f"{origin}/menu")
    page.wait_for_load_state("networkidle")

    surface = WebSurface(
        page, evidence_dir=evidence_dir,
        config_path=Path("surfaces/coredesk.yaml"),
        inputs={"member_no": "100101"},
    )
    t0 = time.monotonic()
    result = replay(
        artifact, surface, {"member_no": "100101"},
        policy_mode=Mode.REPLAY,
        tenant=tenant,
    )
    elapsed = round(time.monotonic() - t0, 1)
    shot = evidence_dir / f"{tenant or 'riverbend'}-final.png"
    page.screenshot(path=str(shot))
    page.close()

    report: dict = {
        "tenant": tenant or "riverbend",
        "origin": origin,
        "status": result.status,
        "elapsed_s": elapsed,
        "screenshot": shot.name,
    }
    if isinstance(result, ReplaySuccess):
        report["outputs"] = result.outputs
        report["steps"] = [
            {"step_id": s.step_id, "strategy": s.locator_strategy,
             "elapsed_ms": round(s.elapsed_ms, 1)}
            for s in result.steps
        ]
    else:
        report["error"] = getattr(result, "error", None) or str(result)
    return report


def main() -> int:
    from artifacts.schema import CapabilityArtifact
    from playwright.sync_api import sync_playwright

    artifact = CapabilityArtifact.model_validate_json(
        Path("artifacts/coredesk.member.read_savings_balance@1.json").read_text()
    )
    evidence_dir = Path("evidence/replay-cross-tenant")
    evidence_dir.mkdir(parents=True, exist_ok=True)

    expected = "12,845.50"
    runs = []
    with sync_playwright() as p:
        headless = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes")
        browser = p.chromium.launch(headless=headless)
        runs.append(_run(
            artifact, browser, "http://127.0.0.1:8001", "", evidence_dir,
        ))
        runs.append(_run(
            artifact, browser, "http://127.0.0.1:8002", "summit", evidence_dir,
        ))
        browser.close()

    both_ok = all(
        r.get("status") == "success"
        and r.get("outputs", {}).get("savings_balance") == expected
        for r in runs
    )
    out = {
        "gate": "cross-tenant",
        "artifact": "coredesk.member.read_savings_balance@1",
        "expected_balance": expected,
        "both_succeeded": both_ok,
        "runs": runs,
    }
    (evidence_dir / "replay-result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0 if both_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
