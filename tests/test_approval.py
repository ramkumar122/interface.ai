"""Tests for control/approval.py — content hash, approve/revoke, review checklist."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from artifacts.schema import (
    Approval,
    CapabilityArtifact,
    Checkpoint,
    CreatedFrom,
    EscalationPolicy,
    FrameRef,
    InputParam,
    KnownOutcome,
    Locator,
    Meta,
    OutputParam,
    Recoverable,
    Step,
    ValueSpec,
    WaitCondition,
)
from control.approval import (
    ApprovalIntegrityError,
    ApprovalPreconditionError,
    approve,
    check_approval,
    content_hash,
    review_checklist,
    revoke,
    write_artifact,
)


def _make_artifact(**kwargs) -> CapabilityArtifact:
    return CapabilityArtifact(
        capability_id="test.read",
        version="1.0",
        description="Test",
        surface_id="test",
        created_from=CreatedFrom(
            run_id="test", model="test",
            steps_in_transcript=1, wall_seconds=1.0,
        ),
        inputs=[InputParam(name="key", type="string",
                           description="key", example="0000")],
        outputs=[OutputParam(name="val", type="text",
                             shape="value", description="val")],
        steps=[
            Step(
                step_id="read_it", ordinal=1,
                intent="Read a value", action="read", risk="safe",
                target=Locator(
                    strategy="ax",
                    frame=FrameRef(match="exact", value="main"),
                    role="cell", name="Balance",
                ),
                value=ValueSpec(source="output", output_name="val"),
            ),
        ],
        known_outcomes=kwargs.get("known_outcomes", []),
        checkpoint=Checkpoint(
            description="done", evidence_refs=["val"],
            proposed_by="compiler",
        ),
        recoverables=kwargs.get("recoverables", []),
        escalation_policy=EscalationPolicy(
            on_stuck="x", on_unknown_state="x", on_blocked="x",
        ),
        approval=Approval(
            status="draft", approved_by=None,
            approved_at=None, risk_class="safe",
        ),
        meta=Meta(schema_version="1.0", verified_runs=kwargs.get("verified_runs", 2)),
    )


class TestContentHash:
    def test_stable(self):
        """Same artifact produces the same hash."""
        art = _make_artifact()
        h1 = content_hash(art)
        h2 = content_hash(art)
        assert h1 == h2
        assert h1.startswith("sha256:")
        assert len(h1) == len("sha256:") + 64  # full SHA-256

    def test_changes_on_step_edit(self):
        """Editing a step changes the hash."""
        art = _make_artifact()
        h1 = content_hash(art)
        data = art.model_dump()
        data["steps"][0]["target"]["name"] = "Changed"
        art2 = CapabilityArtifact.model_validate(data)
        h2 = content_hash(art2)
        assert h1 != h2

    def test_ignores_approval(self):
        """Changing approval status does not change the hash."""
        art = _make_artifact()
        h1 = content_hash(art)
        approved = approve(art, "tester")
        h2 = content_hash(approved)
        assert h1 == h2

    def test_ignores_meta(self):
        """Changing meta does not change the hash."""
        art = _make_artifact()
        h1 = content_hash(art)
        data = art.model_dump()
        data["meta"]["compiler_version"] = "99.0"
        art2 = CapabilityArtifact.model_validate(data)
        h2 = content_hash(art2)
        assert h1 == h2


class TestApproveRevoke:
    def test_approve_refuses_without_verification(self):
        """Machine gate must pass before human gate."""
        art = _make_artifact(verified_runs=None)
        with pytest.raises(ApprovalPreconditionError, match="verified_runs"):
            approve(art, "D. PARK")

    def test_approve_sets_hash(self):
        art = _make_artifact()
        approved = approve(art, "D. PARK")
        assert approved.approval.status == "approved"
        assert approved.approval.approved_by == "D. PARK"
        assert approved.approval.content_hash is not None
        assert approved.approval.content_hash.startswith("sha256:")

    def test_revoke_clears_hash(self):
        art = _make_artifact()
        approved = approve(art, "D. PARK")
        revoked = revoke(approved)
        assert revoked.approval.status == "revoked"
        assert revoked.approval.content_hash is None

    def test_check_draft_passes(self):
        """Draft artifacts pass the check — no hash required."""
        art = _make_artifact()
        ok, reason = check_approval(art)
        assert ok

    def test_check_approved_valid(self):
        """Approved artifact with matching hash passes."""
        approved = approve(_make_artifact(), "tester")
        ok, reason = check_approval(approved)
        assert ok

    def test_check_approved_modified(self):
        """Approved artifact with changed content fails."""
        approved = approve(_make_artifact(), "tester")
        data = approved.model_dump()
        data["steps"][0]["target"]["name"] = "Tampered"
        tampered = CapabilityArtifact.model_validate(data)
        ok, reason = check_approval(tampered)
        assert not ok
        assert "mismatch" in reason.lower()

    def test_check_approved_no_hash(self):
        """Approved status but no hash fails."""
        art = _make_artifact()
        data = art.model_dump()
        data["approval"]["status"] = "approved"
        data["approval"]["content_hash"] = None
        bad = CapabilityArtifact.model_validate(data)
        ok, reason = check_approval(bad)
        assert not ok


class TestWriteArtifact:
    def test_write_draft(self, tmp_path):
        """Draft artifacts write without error."""
        art = _make_artifact()
        path = tmp_path / "test.json"
        write_artifact(path, art)
        assert path.exists()

    def test_write_approved_valid(self, tmp_path):
        """Approved artifact with valid hash writes."""
        approved = approve(_make_artifact(), "tester")
        path = tmp_path / "test.json"
        write_artifact(path, approved)
        assert path.exists()

    def test_write_approved_modified_raises(self, tmp_path):
        """Writing an approved artifact with stale hash raises."""
        approved = approve(_make_artifact(), "tester")
        data = approved.model_dump()
        data["steps"][0]["target"]["name"] = "Tampered"
        tampered = CapabilityArtifact.model_validate(data)
        path = tmp_path / "test.json"
        with pytest.raises(ApprovalIntegrityError, match="stale hash"):
            write_artifact(path, tampered)


class TestReviewChecklist:
    def test_basic_output(self):
        art = _make_artifact()
        text = review_checklist(art)
        assert "test.read@1.0" in text
        assert "read_it" in text
        assert "Status: draft" in text

    def test_flags_undetectable_outcomes(self):
        """An outcome with no detection fields gets a ⚠ warning."""
        art = _make_artifact(
            known_outcomes=[
                KnownOutcome(
                    id="PHANTOM",
                    condition="Cannot happen",
                    business_meaning="Test",
                    caller_action="return",
                ),
            ],
        )
        text = review_checklist(art)
        assert "⚠ NO DETECTION FIELDS" in text
        assert "PHANTOM" in text

    def test_no_warning_for_detectable_outcomes(self):
        """An outcome with detection fields gets no warning."""
        art = _make_artifact(
            known_outcomes=[
                KnownOutcome(
                    id="FOUND",
                    condition="Has text",
                    business_meaning="Test",
                    caller_action="return",
                    page_text_pattern="Not found",
                ),
            ],
        )
        text = review_checklist(art)
        assert "⚠" not in text

    def test_shows_verified_runs(self):
        art = _make_artifact(verified_runs=3)
        text = review_checklist(art)
        assert "Verified runs: 3" in text

    def test_shows_no_verified_runs(self):
        art = _make_artifact(verified_runs=None)
        text = review_checklist(art)
        assert "Verified runs: none recorded" in text

    def test_shows_rejected_locators_count(self):
        """Rejected locators count is surfaced with precise wording."""
        art = _make_artifact()
        text = review_checklist(art)
        assert "0 of 1 steps recorded a rejected locator" in text


def test_review_checklist_warns_about_frozen_literals():
    """A literal is a value frozen at discovery time. `cli review` says so.

    `card.lock` compiled "Suspected fraud" into its Reason select, so every
    invocation would have filed that reason. The compiler set
    `review_required`; nothing printed it.
    """
    from artifacts.schema import (
        Approval, CapabilityArtifact, Checkpoint, CreatedFrom,
        EscalationPolicy, FrameRef, Locator, Meta, Step, ValueSpec,
    )
    from control.approval import review_checklist

    art = CapabilityArtifact(
        capability_id="t.lock", version="1.0", description="d",
        surface_id="coredesk",
        created_from=CreatedFrom(run_id="r", model="m",
                                 steps_in_transcript=1, wall_seconds=1.0),
        inputs=[], outputs=[],
        steps=[Step(
            step_id="select_reason", ordinal=1, intent="Choose",
            action="select",
            target=Locator(strategy="ax",
                           frame=FrameRef(match="exact", value="main"),
                           role="combobox", name="Reason"),
            value=ValueSpec(source="literal", literal="Suspected fraud",
                            review_required=True),
        )],
        known_outcomes=[],
        checkpoint=Checkpoint(description="d", evidence_refs=[],
                              proposed_by="compiler"),
        escalation_policy=EscalationPolicy(on_stuck="x", on_unknown_state="x",
                                           on_blocked="x"),
        approval=Approval(status="draft", risk_class="guarded_write"),
        meta=Meta(schema_version="1.0"),
    )

    out = review_checklist(art)
    assert "literal — review this" in out
    assert 'uses the literal "Suspected fraud"' in out
    assert "it belongs in `inputs`" in out
