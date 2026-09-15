"""Capture three-way result evidence against live CoreDesk.

Runs the hand-written artifact with different inputs and injections to
produce evidence files showing all three result statuses, recovery
behaviours, and determinism across member parameters.

Usage:
    python scripts/replay_evidence.py --variant success
    python scripts/replay_evidence.py --variant business-outcome
    python scripts/replay_evidence.py --variant failure
    python scripts/replay_evidence.py --variant no-savings
    python scripts/replay_evidence.py --variant maintenance-dismiss
    python scripts/replay_evidence.py --variant slow
    python scripts/replay_evidence.py --variant db-timeout
    python scripts/replay_evidence.py --variant session-expired
    python scripts/replay_evidence.py --variant determinism
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

VARIANTS = [
    "success", "business-outcome", "failure", "no-savings",
    "maintenance-dismiss", "slow", "db-timeout", "session-expired",
    "determinism",
]


def _sign_in(page, origin: str) -> None:
    user = os.environ.get("COREDESK_USER", "mreyes")
    pw = os.environ.get("COREDESK_PASS", "demo1234")
    page.goto(f"{origin}/")
    page.fill("#ctl00_MainContent_txtUserId", user)
    page.fill("#ctl00_MainContent_txtPassword", pw)
    page.click("#ctl00_MainContent_btnSignOn")
    page.wait_for_load_state("networkidle")


def _run_one(
    artifact, page, origin: str, evidence_dir: Path,
    member_no: str, inject: str | None, wait_timeout: float = 10.0,
    inject_count: int | None = None,
) -> dict:
    """Run one replay and return the evidence dict."""
    from control.policy import Mode
    from replay.engine import replay
    from replay.result import (
        ReplayBusinessOutcome, ReplayEscalated, ReplayFailure, ReplaySuccess,
    )
    from surface.web import WebSurface

    config_path = Path("surfaces/coredesk.yaml")
    inputs = {"member_no": member_no}

    surface = WebSurface(
        page, evidence_dir=evidence_dir,
        config_path=config_path, inputs=inputs,
    )

    # For session_expired: delete the session cookie and navigate to the
    # expired login page.  This simulates what the middleware produces:
    # a 302 to /?expired=1 with the cookie deleted.
    if inject == "session_expired":
        page.context.clear_cookies()
        page.goto(f"{origin}/?expired=1")
        page.wait_for_load_state("networkidle")
    else:
        page.goto(f"{origin}/menu")
        page.wait_for_load_state("networkidle")

    # Set up injection AFTER initial navigation — fires during replay only
    injected_count = 0
    inject_for_route = inject if inject != "session_expired" else None

    if inject_for_route and inject_count is not None and inject_count > 0:
        def _route_handler(route):
            nonlocal injected_count
            if injected_count < inject_count:
                injected_count += 1
                route.continue_(headers={**route.request.headers,
                                          "X-CoreDesk-Inject": inject_for_route})
            else:
                route.continue_()
        page.route("**/*", _route_handler)
    elif inject_for_route and inject_count is None:
        page.set_extra_http_headers({"X-CoreDesk-Inject": inject_for_route})

    t0 = time.monotonic()
    result = replay(
        artifact, surface, inputs,
        policy_mode=Mode.REPLAY,
        wait_timeout=wait_timeout,
    )
    elapsed_s = round(time.monotonic() - t0, 1)

    # Capture screenshot
    screenshot_path = evidence_dir / "final-state.png"
    page.screenshot(path=str(screenshot_path))

    # Clean up route handler
    if inject_for_route and inject_count is not None and inject_count > 0:
        page.unroute("**/*")
    if inject_for_route and inject_count is None:
        page.set_extra_http_headers({})

    # Build report
    report: dict = {
        "status": result.status,
        "member_no": member_no,
        "injection": inject,
        "inject_count": inject_count,
        "elapsed_s": elapsed_s,
        "screenshot": "final-state.png",
    }

    if isinstance(result, ReplaySuccess):
        report["outputs"] = result.outputs
        report["drift_signals"] = [
            {"step_id": ds.step_id, "primary": ds.primary_strategy,
             "matched": ds.matched_strategy, "detail": ds.detail}
            for ds in result.drift_signals
        ]
        report["steps"] = [
            {
                "step_id": s.step_id, "ref": s.ref_used,
                "strategy": s.locator_strategy,
                "elapsed_ms": round(s.elapsed_ms, 1),
                **({"recoveries": [
                    {"detector_id": r.detector_id,
                     "recovery_action": r.recovery_action,
                     "attempt": r.attempt}
                    for r in s.recoveries
                ]} if s.recoveries else {}),
            }
            for s in result.steps
        ]
    elif isinstance(result, ReplayBusinessOutcome):
        report["outcome_id"] = result.outcome_id
        report["condition"] = result.condition
        report["message"] = result.message
        report["caller_action"] = result.caller_action
        report["at_step"] = result.at_step
        report["steps_completed"] = [
            {"step_id": s.step_id, "elapsed_ms": round(s.elapsed_ms, 1)}
            for s in result.steps
        ]
    elif isinstance(result, ReplayEscalated):
        req = result.request
        report["step_id"] = req.step_id
        report["step_intent"] = req.step_intent
        report["reason"] = req.reason
        report["instructions"] = req.instructions
        report["resume_token"] = result.resume_token
        report["steps_completed"] = [
            {"step_id": s.step_id, "elapsed_ms": round(s.elapsed_ms, 1)}
            for s in result.steps
        ]
    elif isinstance(result, ReplayFailure):
        report["step_id"] = result.step_id
        report["step_intent"] = result.step_intent
        report["expected"] = result.expected
        report["observed"] = result.observed
        report["locator_rung"] = result.locator_rung
        report["error"] = result.error
        report["steps_completed"] = [
            {"step_id": s.step_id, "elapsed_ms": round(s.elapsed_ms, 1)}
            for s in result.steps
        ]

    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, choices=VARIANTS)
    ap.add_argument("--target", default="http://127.0.0.1:8001")
    ap.add_argument(
        "--member", nargs="?", const="ASK", default=None,
        help="Member number. Omit to use the variant default. "
             "Pass --member alone to be prompted.",
    )
    args = ap.parse_args()

    # Before a browser launches: is CoreDesk there, and will it let the
    # harness in?  A wrong password otherwise surfaces as a mystifying
    # locate failure on step one.
    from scripts._preflight import check_coredesk, die

    problems = check_coredesk(args.target)
    if problems:
        return die(problems)

    from artifacts.schema import CapabilityArtifact
    from playwright.sync_api import sync_playwright

    artifact_path = Path("artifacts/coredesk.member.read_savings_balance@1.json")
    artifact = CapabilityArtifact.model_validate_json(artifact_path.read_text())

    origin = args.target

    with sync_playwright() as p:
        headless = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes")
        browser = p.chromium.launch(headless=headless)

        if args.variant == "determinism":
            # Multi-member run: same artifact, different inputs
            members = [
                ("100110", "74,209.99"),  # large balance, commas
                ("100111", "0.00"),       # zero balance
                ("100115", "2,405.60"),   # non-ASCII name (Bergström)
            ]
            evidence_dir = Path("evidence/replay-determinism")
            evidence_dir.mkdir(parents=True, exist_ok=True)

            results = []
            for member_no, expected_balance in members:
                page = browser.new_page()
                _sign_in(page, origin)
                report = _run_one(artifact, page, origin, evidence_dir,
                                  member_no, inject=None)
                report["expected_balance"] = expected_balance
                actual = report.get("outputs", {}).get("savings_balance")
                report["match"] = actual == expected_balance
                results.append(report)
                page.close()

            out = {
                "variant": "determinism",
                "description": "Same artifact, three members with different balance shapes",
                "all_passed": all(r.get("match") for r in results),
                "runs": results,
            }
            (evidence_dir / "replay-result.json").write_text(json.dumps(out, indent=2))
            print(json.dumps(out, indent=2))
            browser.close()
            return 0 if out["all_passed"] else 1

        # Single-variant configuration
        variant_config: dict = {
            "success":              {"member": "100101", "inject": None, "count": None, "timeout": 10.0},
            "business-outcome":     {"member": "999999", "inject": None, "count": None, "timeout": 10.0},
            "failure":              {"member": "100101", "inject": "app_error", "count": None, "timeout": 10.0},
            "no-savings":           {"member": "100102", "inject": None, "count": None, "timeout": 10.0},
            "maintenance-dismiss":  {"member": "100101", "inject": "maintenance_notice", "count": 1, "timeout": 10.0},
            "slow":                 {"member": "100101", "inject": "slow", "count": 1, "timeout": 15.0},
            "db-timeout":           {"member": "100101", "inject": "db_timeout", "count": 1, "timeout": 10.0},
            "session-expired":      {"member": "100101", "inject": "session_expired", "count": 0, "timeout": 10.0},
        }

        cfg = variant_config[args.variant]
        if args.member == "ASK":
            member_no = input("Member number: ").strip()
        elif args.member:
            member_no = args.member
        else:
            member_no = cfg["member"]
        evidence_dir = Path(f"evidence/replay-{args.variant}")
        evidence_dir.mkdir(parents=True, exist_ok=True)

        page = browser.new_page()
        _sign_in(page, origin)

        report = _run_one(
            artifact, page, origin, evidence_dir,
            member_no=member_no,
            inject=cfg["inject"],
            wait_timeout=cfg["timeout"],
            inject_count=cfg["count"],
        )
        report["variant"] = args.variant

        (evidence_dir / "replay-result.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        page.close()
        browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
