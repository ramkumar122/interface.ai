"""Tests for artifacts/schema.py — the capability artifact schema.

All tests are pure unit tests. The hand-written artifact is the gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

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
    NamedRegionRef,
    OutputParam,
    OverrideFieldPatch,
    OverrideSet,
    OverrideTargetPatch,
    Recoverable,
    RejectedLocator,
    Step,
    StepOverride,
    ValueSpec,
    WaitCondition,
)

REPO = Path(__file__).resolve().parents[1]
ARTIFACT_PATH = REPO / "artifacts" / "coredesk.member.read_savings_balance@1.json"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_artifact() -> CapabilityArtifact:
    data = json.loads(ARTIFACT_PATH.read_text())
    return CapabilityArtifact.model_validate(data)


def _main_frame() -> FrameRef:
    return FrameRef(match="exact", value="main")


def _minimal_step(**overrides) -> dict:
    base = {
        "step_id": "test_step",
        "ordinal": 1,
        "intent": "Do something",
        "action": "click",
        "target": {
            "strategy": "ax",
            "frame": {"match": "exact", "value": "main"},
            "role": "button",
            "name": "OK",
        },
        "rejected": [],
    }
    base.update(overrides)
    return base


def _minimal_artifact(**overrides) -> dict:
    base = {
        "capability_id": "test.cap",
        "version": "1.0",
        "description": "Test capability",
        "surface_id": "test",
        "created_from": {
            "run_id": "test-run",
            "model": "test-model",
            "steps_in_transcript": 1,
            "wall_seconds": 1.0,
        },
        "inputs": [],
        "outputs": [],
        "steps": [_minimal_step()],
        "known_outcomes": [],
        "checkpoint": {
            "description": "Done",
            "evidence_refs": [],
            "proposed_by": "human",
        },
        "escalation_policy": {
            "on_stuck": "escalate",
            "on_unknown_state": "escalate",
            "on_blocked": "return",
        },
        "approval": {
            "status": "draft",
            "approved_by": None,
            "approved_at": None,
            "risk_class": "safe",
        },
        "meta": {"schema_version": "1.0"},
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# 1. Round-trip
# --------------------------------------------------------------------------- #


def test_artifact_roundtrip():
    """Serialize to JSON, parse back, assert equality."""
    art = _load_artifact()
    json_str = art.model_dump_json(indent=2)
    art2 = CapabilityArtifact.model_validate_json(json_str)
    assert art == art2


# --------------------------------------------------------------------------- #
# 2. Hand-written artifact validates
# --------------------------------------------------------------------------- #


def test_artifact_validates():
    """The hand-written artifact loads and validates without error."""
    art = _load_artifact()
    assert art.capability_id == "coredesk.member.read_savings_balance"
    assert art.version == "1.0"
    assert len(art.steps) == 5
    assert len(art.known_outcomes) == 2  # MEMBER_NOT_FOUND, NO_SAVINGS_ACCOUNT
    assert len(art.inputs) == 1
    assert len(art.outputs) == 1
    assert art.inputs[0].name == "member_no"
    assert art.outputs[0].name == "savings_balance"


# --------------------------------------------------------------------------- #
# 3. Rejects invalid
# --------------------------------------------------------------------------- #


def test_artifact_rejects_missing_fields():
    with pytest.raises(ValidationError):
        CapabilityArtifact.model_validate({"capability_id": "x"})


def test_artifact_rejects_bad_version():
    data = _minimal_artifact(version="v1")
    with pytest.raises(ValidationError, match="MAJOR.MINOR"):
        CapabilityArtifact.model_validate(data)


# --------------------------------------------------------------------------- #
# 4. Locator strategy-tagged
# --------------------------------------------------------------------------- #


def test_locator_ax_requires_name():
    with pytest.raises(ValidationError, match="name"):
        Locator(
            strategy="ax",
            frame=_main_frame(),
            role="button",
            name=None,
        )


def test_locator_ax_scoped_requires_within():
    with pytest.raises(ValidationError, match="within"):
        Locator(
            strategy="ax_scoped",
            frame=_main_frame(),
            role="cell",
            name="balance",
        )


def test_locator_ax_relative_requires_table():
    with pytest.raises(ValidationError, match="table_name"):
        Locator(
            strategy="ax_relative",
            frame=_main_frame(),
            role="cell",
        )


def test_locator_ax_relative_requires_column():
    with pytest.raises(ValidationError, match="column_header"):
        Locator(
            strategy="ax_relative",
            frame=_main_frame(),
            role="cell",
            table_name="Share accounts",
        )


def test_locator_ax_relative_requires_row_key():
    with pytest.raises(ValidationError, match="row_key"):
        Locator(
            strategy="ax_relative",
            frame=_main_frame(),
            role="cell",
            table_name="Share accounts",
            column_header="AVAILABLE",
        )


def test_locator_ax_relative_valid():
    loc = Locator(
        strategy="ax_relative",
        frame=_main_frame(),
        role="cell",
        table_name="Share accounts",
        column_header="AVAILABLE",
        row_key="0000",
        row_match="exact",
    )
    assert loc.strategy == "ax_relative"
    assert loc.row_key == "0000"


# --------------------------------------------------------------------------- #
# 5. ValueSpec exactly-one
# --------------------------------------------------------------------------- #


def test_value_spec_input_requires_name():
    with pytest.raises(ValidationError, match="input_name"):
        ValueSpec(source="input")


def test_value_spec_literal_requires_literal():
    with pytest.raises(ValidationError, match="literal"):
        ValueSpec(source="literal")


def test_value_spec_output_requires_name():
    with pytest.raises(ValidationError, match="output_name"):
        ValueSpec(source="output")


def test_value_spec_input_valid():
    v = ValueSpec(source="input", input_name="member_no")
    assert v.source == "input"


def test_value_spec_output_valid():
    v = ValueSpec(source="output", output_name="savings_balance")
    assert v.source == "output"


# --------------------------------------------------------------------------- #
# 6. Read action requires output source
# --------------------------------------------------------------------------- #


def test_read_action_requires_output_source():
    step_data = _minimal_step(
        action="read",
        value={"source": "input", "input_name": "x"},
    )
    with pytest.raises(ValidationError, match="source='output'"):
        Step.model_validate(step_data)


def test_output_source_only_on_read():
    step_data = _minimal_step(
        action="click",
        value={"source": "output", "output_name": "x"},
    )
    with pytest.raises(ValidationError, match="only valid for action='read'"):
        Step.model_validate(step_data)


# --------------------------------------------------------------------------- #
# 7. Known outcomes: no SUCCESS
# --------------------------------------------------------------------------- #


def test_known_outcomes_no_success():
    with pytest.raises(ValidationError, match="SUCCESS"):
        KnownOutcome(
            id="SUCCESS",
            condition="It worked",
            business_meaning="Normal",
            caller_action="return",
        )


def test_known_outcomes_valid():
    ko = KnownOutcome(
        id="MEMBER_NOT_FOUND",
        condition="No match",
        business_meaning="Member doesn't exist",
        caller_action="return",
    )
    assert ko.caller_action == "return"


# --------------------------------------------------------------------------- #
# 8. caller_action values
# --------------------------------------------------------------------------- #


def test_caller_action_values():
    for action in ("return", "escalate", "retry_with_different_input"):
        ko = KnownOutcome(
            id="TEST", condition="c", business_meaning="m",
            caller_action=action,
        )
        assert ko.caller_action == action


def test_caller_action_rejects_invalid():
    with pytest.raises(ValidationError):
        KnownOutcome(
            id="TEST", condition="c", business_meaning="m",
            caller_action="do_nothing",
        )


# --------------------------------------------------------------------------- #
# 9. Rejected locator has reason
# --------------------------------------------------------------------------- #


def test_rejected_locator_has_reason():
    with pytest.raises(ValidationError, match="reason"):
        RejectedLocator(strategy="dom_id", value="foo", reason="")


def test_rejected_locator_valid():
    rl = RejectedLocator(
        strategy="dom_id", value="ctl00_btn",
        reason="WebForms IDs are unstable",
    )
    assert rl.reason


# --------------------------------------------------------------------------- #
# 10. Override from/to assertion
# --------------------------------------------------------------------------- #


def test_override_from_to_assertion_passes():
    art = _load_artifact()
    patched = art.apply_overrides("summit")
    step1 = next(s for s in patched.steps if s.step_id == "click_menu")
    assert step1.target.name == "INQ01"


def test_override_from_to_assertion_fails():
    """If from_value doesn't match base, override raises."""
    art = _load_artifact()
    # Tamper with the base to make the override's from_value wrong
    data = art.model_dump()
    for s in data["steps"]:
        if s["step_id"] == "click_menu":
            s["target"]["name"] = "WRONG"
    art2 = CapabilityArtifact.model_validate(data)
    with pytest.raises(ValueError, match="expected base value"):
        art2.apply_overrides("summit")


# --------------------------------------------------------------------------- #
# 11. Overrides keyed by step_id
# --------------------------------------------------------------------------- #


def test_override_keyed_by_step_id():
    art = _load_artifact()
    summit = art.overrides["summit"]
    assert "click_menu" in summit.steps
    assert "click_select" in summit.steps
    # No ordinal keys
    assert "1" not in summit.steps
    assert "4" not in summit.steps


def test_override_unknown_step_id_rejected():
    data = _minimal_artifact(
        overrides={"summit": {"steps": {"nonexistent": {"target": None}}}}
    )
    with pytest.raises(ValidationError, match="unknown step_id"):
        CapabilityArtifact.model_validate(data)


# --------------------------------------------------------------------------- #
# 12. Risk class is max of steps
# --------------------------------------------------------------------------- #


def test_risk_class_derived_safe():
    """All steps in read_savings_balance are safe reads/clicks."""
    art = _load_artifact()
    assert art.derived_risk_class() == "safe"


def test_risk_class_derived_guarded():
    """A capability with one guarded_write step is guarded_write overall."""
    data = _minimal_artifact(
        steps=[
            _minimal_step(step_id="s1", ordinal=1, risk="safe"),
            _minimal_step(step_id="s2", ordinal=2, risk="guarded_write"),
        ]
    )
    art = CapabilityArtifact.model_validate(data)
    assert art.derived_risk_class() == "guarded_write"


# --------------------------------------------------------------------------- #
# 13. FrameRef is structured
# --------------------------------------------------------------------------- #


def test_frame_ref_exact():
    f = FrameRef(match="exact", value="main")
    assert f.value == "main"


def test_frame_ref_exact_requires_value():
    with pytest.raises(ValidationError, match="value"):
        FrameRef(match="exact")


def test_frame_ref_role_name():
    f = FrameRef(match="role_name", role="iframe", name_prefix="Share accounts")
    assert f.name_prefix == "Share accounts"


def test_frame_ref_role_name_requires_role():
    with pytest.raises(ValidationError, match="role"):
        FrameRef(match="role_name", name_prefix="Share accounts")


def test_frame_ref_role_name_requires_prefix():
    with pytest.raises(ValidationError, match="name_prefix"):
        FrameRef(match="role_name", role="iframe")


# --------------------------------------------------------------------------- #
# 14. Version format
# --------------------------------------------------------------------------- #


def test_version_format_valid():
    data = _minimal_artifact(version="2.3")
    art = CapabilityArtifact.model_validate(data)
    assert art.version == "2.3"


def test_version_format_invalid():
    data = _minimal_artifact(version="v1")
    with pytest.raises(ValidationError, match="MAJOR.MINOR"):
        CapabilityArtifact.model_validate(data)


# --------------------------------------------------------------------------- #
# 15. step_id unique
# --------------------------------------------------------------------------- #


def test_step_id_unique():
    data = _minimal_artifact(
        steps=[
            _minimal_step(step_id="a", ordinal=1),
            _minimal_step(step_id="a", ordinal=2),
        ]
    )
    with pytest.raises(ValidationError, match="duplicate step_id"):
        CapabilityArtifact.model_validate(data)


# --------------------------------------------------------------------------- #
# 16. Summit overrides produce valid artifact
# --------------------------------------------------------------------------- #


def test_summit_override_full():
    """Apply all summit overrides and verify the patched artifact validates."""
    art = _load_artifact()
    patched = art.apply_overrides("summit")

    # Check each patched step
    by_id = {s.step_id: s for s in patched.steps}
    assert by_id["click_menu"].target.name == "INQ01"
    assert by_id["click_menu"].wait_for is not None
    assert by_id["click_menu"].wait_for.name == "Member ID"
    assert by_id["type_member_no"].target.name == "Member ID"
    assert by_id["click_search"].wait_for is not None
    assert by_id["click_search"].wait_for.name == "VIEW"
    assert by_id["click_select"].target.name == "VIEW"
    assert by_id["read_balance"].target.column_header == "AVAIL BAL"

    # Still validates
    _ = CapabilityArtifact.model_validate(patched.model_dump())
