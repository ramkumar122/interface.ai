"""Capture the two-gate approval evidence: machine gate, then human gate.

`evidence/approval-gate/gate-result.json` was hand-assembled and no script
produced it, so the one gate that makes approval tamper-evident could not
be regenerated.  This reproduces its three phases.

Nothing here reimplements `control/approval.py` — it calls `approve()`,
`check_approval()` and `content_hash()` and records what they say.  A
second implementation of the hash would be a second thing to keep in
agreement with the one the engine actually consults.

Every artifact is a deep copy in a temp directory.  `artifacts/` is never
touched: this script deliberately tampers with an approved artifact, and
doing that in place would leave a repo whose shipped capability fails its
own hash check.

Needs no browser, no model and no CoreDesk — the whole point of the gate
is that it refuses before a step executes.

Usage:
    python scripts/approval_gate_evidence.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ARTIFACT = Path("artifacts/coredesk.member.read_savings_balance@1.json")
OUT_DIR = Path("evidence/approval-gate")
APPROVER = "D. PARK — Risk / Ops"


def main() -> int:
    from artifacts.schema import CapabilityArtifact
    from control.approval import (
        ApprovalPreconditionError,
        approve,
        check_approval,
        content_hash,
        write_artifact,
    )
    from control.policy import Mode
    from replay.engine import replay

    if not ARTIFACT.is_file():
        print(
            f"ERROR: {ARTIFACT} not found.\n"
            f"  This gate needs one approved artifact to work from.",
            file=sys.stderr,
        )
        return 2

    source = ARTIFACT.read_text()
    report: dict = {"gate": "approval-two-gate"}

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)

        # ---- Phase 1: approved, hash valid -> the check passes ----------- #
        art = CapabilityArtifact.model_validate_json(source)
        ok, reason = check_approval(art)
        report["phase_1_approved_valid"] = {
            "status": art.approval.status,
            "hash": art.approval.content_hash,
            "verified_runs": art.meta.verified_runs,
            "check_passed": ok,
            "check_reason": reason,
        }
        print(f"Phase 1  approved + valid hash  -> check_passed={ok}")

        # ---- Phase 2: unverified -> approve() refuses -------------------- #
        # The machine gate runs before the human one.  An artifact nothing
        # has replayed cannot be approved, however willing the reviewer.
        data = json.loads(source)
        data["meta"]["verified_runs"] = None
        data["approval"] = {"status": "draft", "risk_class": "safe"}
        unverified = CapabilityArtifact.model_validate(data)

        blocked, why = False, None
        try:
            approve(unverified, APPROVER)
        except ApprovalPreconditionError as exc:
            blocked, why = True, str(exc)
        report["phase_2_unverified_blocked"] = {
            "verified_runs": unverified.meta.verified_runs,
            "approve_blocked": blocked,
            "reason": why,
        }
        print(f"Phase 2  unverified             -> approve_blocked={blocked}")

        # ---- Phase 3: tampered after approval -> replay refuses ---------- #
        # One character in one locator.  The stored hash covers the
        # execution-affecting fields, so the edit is caught before any step
        # runs — no browser is involved in noticing.
        tampered_data = json.loads(source)
        original = tampered_data["steps"][0]["target"]["name"]
        tampered_data["steps"][0]["target"]["name"] = original[:-1] + "X"
        tampered = CapabilityArtifact.model_validate(tampered_data)

        t_ok, t_reason = check_approval(tampered)
        result = replay(tampered, _NoSurface(), {"member_no": "100101"},
                        policy_mode=Mode.REPLAY)
        report["phase_3_tampered_refused"] = {
            "edit": (
                f"step[0].target.name: {original} -> "
                f"{tampered_data['steps'][0]['target']['name']}"
            ),
            "current_hash": content_hash(tampered),
            "check_passed": t_ok,
            "check_reason": t_reason,
            "replay_status": result.status,
            "replay_error": getattr(result, "error", None),
            "replay_refused_at_step": getattr(result, "step_id", None),
            "browser_used": False,
        }
        print(f"Phase 3  tampered               -> replay={result.status} "
              f"at {getattr(result, 'step_id', '?')}")

        # ---- Phase 4: write_artifact refuses a stale approved artifact --- #
        # Not in the hand-assembled original, and worth having: the guard
        # that stops a modified approved artifact reaching disk at all.
        from control.approval import ApprovalIntegrityError

        write_blocked, write_why = False, None
        try:
            write_artifact(tmpdir / "tampered.json", tampered)
        except ApprovalIntegrityError as exc:
            write_blocked, write_why = True, str(exc)
        report["phase_4_stale_write_refused"] = {
            "write_blocked": write_blocked,
            "reason": write_why,
        }
        print(f"Phase 4  writing the tampered   -> write_blocked={write_blocked}")

    report["note"] = (
        "No browser, no model, no CoreDesk. Every refusal here happens "
        "before a step executes, which is the point: approval is checked "
        "against a hash of the artifact's execution fields, not against a "
        "status flag anyone can edit."
    )
    report["artifacts_mutated"] = False

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "gate-result.json").write_text(json.dumps(report, indent=2))
    print(f"\nWrote {OUT_DIR / 'gate-result.json'}")

    # The shipped artifact must be exactly as we found it.
    if ARTIFACT.read_text() != source:
        print("ERROR: the source artifact changed on disk.", file=sys.stderr)
        return 1

    every_gate_held = (
        report["phase_1_approved_valid"]["check_passed"]
        and report["phase_2_unverified_blocked"]["approve_blocked"]
        and not report["phase_3_tampered_refused"]["check_passed"]
        and report["phase_4_stale_write_refused"]["write_blocked"]
    )
    print("All four gates held." if every_gate_held
          else "A GATE DID NOT HOLD — read the report.")
    return 0 if every_gate_held else 1


class _NoSurface:
    """A surface that fails if anything reaches it.

    Phase 3 asserts the refusal happens *before* execution.  A mock that
    quietly returned observations would let the run get further than it
    should and still look like a pass.
    """

    def observe(self, screenshot: bool = False):
        raise AssertionError(
            "replay touched the surface — the approval check should have "
            "refused before any step executed"
        )

    def act(self, action):
        raise AssertionError("replay acted despite a hash mismatch")

    def evidence(self, label: str):
        raise AssertionError("replay captured evidence despite a hash mismatch")

    def release(self) -> None: ...
    def reacquire(self) -> None: ...


if __name__ == "__main__":
    raise SystemExit(main())
