"""Compile the real discovery transcript into a draft artifact.

Demo prep, not console behaviour.  `artifacts/` ships one artifact and it
is already approved, so the review queue has nothing in it and the
console's human gate cannot be demonstrated.  This writes a genuine
draft — compiled from the same transcript the shipped artifact came from,
through the same `compile_transcript()` — rather than a hand-written
stand-in that would prove nothing about the compiler.

Two states are worth seeding, and they are different screens:

    python scripts/seed_draft_artifact.py
        draft, `verified_runs` unset.  The review queue shows it blocked,
        because `approve()` refuses an unverified artifact.

    HEADLESS=1 python scripts/seed_draft_artifact.py --verify
        draft, verified against live CoreDesk on :8001.  Approvable —
        this is the one the approve demo needs.

Usage:
    python scripts/seed_draft_artifact.py [--verify] [--version 2.0]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_VERSION = "2.0"
EXPECTED_BALANCE = "12,845.50"
MEMBER_NO = "100101"
# A second record. `verify_artifact` refuses a capability with declared
# inputs and only one parameter set — the same values twice prove
# determinism, not portability (REPORT §7).
SECOND_MEMBER_NO = "100110"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--verify", action="store_true",
        help="Replay against live CoreDesk to stamp verified_runs. "
             "Without it the artifact is blocked at the review queue.",
    )
    ap.add_argument("--version", default=DEFAULT_VERSION)
    ap.add_argument("--target", default="http://127.0.0.1:8001")
    ap.add_argument("--runs", type=int, default=2)
    args = ap.parse_args()

    from artifacts.schema import CapabilityArtifact
    from control.approval import write_artifact

    # These helpers parse the shipped transcript into StepRecords.  Reusing
    # them keeps one loader; a second would drift from the transcript format.
    from tests.test_compile import _load_records, _make_result
    from agent.compile import compile_transcript

    records = _load_records()
    result = _make_result(records)
    provenance = _provenance()
    artifact = compile_transcript(
        records, result,
        capability_id="coredesk.member.read_savings_balance",
        inputs={"member_no": MEMBER_NO},
        outputs={"savings_balance": "money"},
        description=(
            "Read the available balance of a member's primary savings "
            "account (suffix 0000). Compiled from the discovery transcript "
            "and pending review."
        ),
        run_id=provenance.get("run_id"),
        model=provenance.get("model"),
    )

    # The compiler emits 1.0, which is the shipped artifact's file name.
    # A second version keeps both on disk and exercises the console's
    # id@version resolver.
    data = artifact.model_dump()
    data["version"] = args.version

    # The transcript's own summary is the source for steps/seconds too —
    # `_make_result` fills those from the record count, which is the pruned
    # figure rather than what discovery actually ran.
    for field in ("steps_in_transcript", "wall_seconds"):
        if field in provenance:
            data["created_from"][field] = provenance[field]
    artifact = CapabilityArtifact.model_validate(data)

    print(f"Compiled {len(artifact.steps)} steps at v{artifact.version}")
    print(f"  status: {artifact.approval.status}")

    if args.verify:
        verified_runs = _verify(artifact, args)
        if verified_runs is None:
            return 1
        print(f"  verified_runs: {verified_runs}")
    else:
        print("  verified_runs: unset — the review queue will show it blocked")

    from control.approval import artifact_filename

    path = Path("artifacts") / artifact_filename(artifact)
    write_artifact(path, artifact)
    print(f"Wrote {path}")
    return 0


def _provenance() -> dict:
    """Real run id and model, read from the transcript's own summary.

    Falls back to whatever the compiler inferred rather than inventing a
    run id, so a missing summary degrades to "unknown" instead of to a
    plausible-looking fiction.
    """
    import json

    # Named explicitly rather than derived as a sibling of the transcript:
    # the two are pinned fixtures that happen to live together, and deriving
    # one from the other's parent broke silently when the fixture moved.
    from tests.test_compile import TRANSCRIPT_SUMMARY_PATH

    summary_path = TRANSCRIPT_SUMMARY_PATH
    if not summary_path.is_file():
        return {}
    try:
        summary = json.loads(summary_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}

    stamped = {}
    if summary.get("run_id"):
        stamped["run_id"] = summary["run_id"]
    if summary.get("model"):
        stamped["model"] = summary["model"]
    if summary.get("steps"):
        stamped["steps_in_transcript"] = summary["steps"]
    if summary.get("seconds"):
        stamped["wall_seconds"] = summary["seconds"]
    return stamped


def _verify(artifact, args) -> int | None:
    """Replay N times against live CoreDesk; `verify_artifact` stamps the count."""
    from playwright.sync_api import sync_playwright
    from replay.verify import verify_artifact
    from surface.web import WebSurface

    origin = args.target
    user = os.environ.get("COREDESK_USER", "mreyes")
    pw = os.environ.get("COREDESK_PASS", "demo1234")
    evidence_dir = Path("evidence/seed-draft-verify")
    evidence_dir.mkdir(parents=True, exist_ok=True)
    inputs = {"member_no": MEMBER_NO}
    also = [{"member_no": SECOND_MEMBER_NO}]

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
            expected_outputs={"savings_balance": EXPECTED_BALANCE},
            n=args.runs, wait_timeout=10.0, also=also,
        )
        browser.close()

    # A clean headless verification captures nothing, so this would otherwise
    # leave an empty directory that `test_no_empty_evidence_directories`
    # fails on. `rmdir` removes an empty directory and nothing else.
    try:
        evidence_dir.rmdir()
    except OSError:
        pass

    if not vr.passed:
        print(f"VERIFICATION FAILED: {vr.failure_summary}", file=sys.stderr)
        print("No artifact written.", file=sys.stderr)
        return None
    return artifact.meta.verified_runs


if __name__ == "__main__":
    raise SystemExit(main())
