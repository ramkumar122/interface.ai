"""Tests for agent/compile.py + agent/prune.py.

No browser, no LLM. Compiles real and synthetic transcripts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.compile import CompilationError, compile_transcript, _build_frame_ref
from agent.loop import DiscoveryResult, StepRecord
from agent.prune import prune
from artifacts.schema import CapabilityArtifact, FrameRef
from surface.base import NamedRegion, ResolutionTrace, TableContext

REPO = Path(__file__).resolve().parents[1]

# The canonical discovery transcript, pinned as a **fixture** — not evidence.
#
# It lived in `evidence/discover-gemini-3.6-flash-65b48ef0/` by accident, and
# that made the test suite depend on a directory whose whole purpose is to be
# regenerated.  Nine tests and eleven errors followed from moving it aside.
#
# It must not be regenerated.  These tests assert the compiler's exact output
# against it — step ids, locator strategies, the rejected `dom_id`, the
# synthesised intents — so a fresh run with different turns would not be a
# newer fixture, it would be a different one, and the assertions would be
# rewritten to match whatever it happened to produce.  That is the opposite
# of a regression test.
#
# `discovery-summary.json` is its sibling from the same run, carrying the real
# run id and model.  `scripts/seed_draft_artifact.py` stamps provenance from
# it, so the two travel together.
TRANSCRIPT_PATH = REPO / "tests" / "fixtures" / "discovery-transcript.jsonl"
TRANSCRIPT_SUMMARY_PATH = REPO / "tests" / "fixtures" / "discovery-summary.json"
HAND_WRITTEN_PATH = REPO / "artifacts" / "coredesk.member.read_savings_balance@1.json"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _load_records() -> list[StepRecord]:
    """Load StepRecords from the real transcript."""
    records = []
    for line in TRANSCRIPT_PATH.read_text().splitlines():
        d = json.loads(line)
        trace = None
        if d.get("trace"):
            t = d["trace"]
            enclosing = [
                NamedRegion(role=r["role"], name=r["name"])
                for r in (t.get("enclosing_named_regions") or [])
            ]
            tc_d = t.get("table_context")
            tc = None
            if tc_d:
                tc = TableContext(
                    table_name=tc_d["table_name"],
                    column_header=tc_d.get("column_header"),
                    row_key=tc_d.get("row_key"),
                    col_index=tc_d["col_index"],
                    row_index=tc_d["row_index"],
                )
            trace = ResolutionTrace(
                frame=t["frame"],
                role=t["role"],
                name=t["name"],
                name_unique_in_frame=t["name_unique_in_frame"],
                enclosing_named_regions=enclosing,
                table_context=tc,
                dom_id_observed=t.get("dom_id_observed"),
                elapsed_ms=t.get("elapsed_ms", 0),
            )
        records.append(StepRecord(
            step=d["step"],
            obs_hash=d["obs_hash"],
            action_kind=d["action"],
            action_args=d["args"],
            target_role=d.get("target_role"),
            target_name=d.get("target_name"),
            result_ok=d["ok"],
            result_reason=d.get("reason"),
            result_detail=d.get("detail"),
            trace=trace,
            verdict_allowed=d["verdict"]["allowed"],
            verdict_risk=d["verdict"]["risk"],
            prompt_tokens=d.get("prompt_tokens", 0),
            output_tokens=d.get("output_tokens", 0),
            hash_changed=d.get("hash_changed", True),
        ))
    return records


def _load_hand_written() -> CapabilityArtifact:
    return CapabilityArtifact.model_validate_json(HAND_WRITTEN_PATH.read_text())


def _make_result(records: list[StepRecord], outputs: dict | None = None) -> DiscoveryResult:
    return DiscoveryResult(
        status="done",
        steps=len(records),
        outputs_filled=outputs or {"savings_balance": "12,845.50"},
        transcript=records,
        prompt_tokens=0,
        output_tokens=0,
        wall_seconds=9.0,
    )


def _synth_record(
    step: int, action: str = "click", obs_hash: str = "h1",
    target_name: str = "Btn", target_role: str = "button",
    ok: bool = True, risk: str = "safe", hash_changed: bool = True,
    trace_unique: bool = True, trace_frame: str = "main",
    dom_id: str | None = None,
    enclosing: list[NamedRegion] | None = None,
    table_context: TableContext | None = None,
    args: dict | None = None,
) -> StepRecord:
    trace = ResolutionTrace(
        frame=trace_frame,
        role=target_role,
        name=target_name,
        name_unique_in_frame=trace_unique,
        enclosing_named_regions=enclosing or [],
        table_context=table_context,
        dom_id_observed=dom_id,
        elapsed_ms=1.0,
    )
    return StepRecord(
        step=step,
        obs_hash=obs_hash,
        action_kind=action,
        action_args=args or {"ref": "e1"},
        target_role=target_role,
        target_name=target_name,
        result_ok=ok,
        result_reason=None,
        result_detail=None,
        trace=trace if action not in ("done", "escalate", "give_up") else None,
        verdict_allowed=True,
        verdict_risk=risk,
        prompt_tokens=0,
        output_tokens=0,
        hash_changed=hash_changed,
    )


# --------------------------------------------------------------------------- #
# Semantic equality comparison
# --------------------------------------------------------------------------- #


def assert_semantically_equal(
    compiled: CapabilityArtifact,
    hand: CapabilityArtifact,
) -> None:
    """Compare compiled artifact to hand-written, field by field.

    Compared: step count, per-step action/risk/target fields, value source,
    rejected presence, intent non-empty, inputs, outputs, checkpoint refs.

    Ignored (with reason): intent wording, step_id slug, created_from,
    description, version, escalation prose, known_outcomes, overrides.
    """
    # Step count
    assert len(compiled.steps) == len(hand.steps), (
        f"step count: compiled={len(compiled.steps)}, hand={len(hand.steps)}"
    )

    for i, (cs, hs) in enumerate(zip(compiled.steps, hand.steps)):
        pfx = f"step {i+1}"

        # Action
        assert cs.action == hs.action, f"{pfx}: action {cs.action} != {hs.action}"

        # Risk
        assert cs.risk == hs.risk, f"{pfx}: risk {cs.risk} != {hs.risk}"

        # Target strategy
        assert cs.target.strategy == hs.target.strategy, (
            f"{pfx}: strategy {cs.target.strategy} != {hs.target.strategy}"
        )

        # Target role
        assert cs.target.role == hs.target.role, (
            f"{pfx}: target role {cs.target.role} != {hs.target.role}"
        )

        # Target name (may be None for ax_relative)
        assert cs.target.name == hs.target.name, (
            f"{pfx}: target name {cs.target.name!r} != {hs.target.name!r}"
        )

        # Target column_header (for ax_relative)
        assert cs.target.column_header == hs.target.column_header, (
            f"{pfx}: column_header {cs.target.column_header!r} != {hs.target.column_header!r}"
        )

        # Target row_key (for ax_relative)
        assert cs.target.row_key == hs.target.row_key, (
            f"{pfx}: row_key {cs.target.row_key!r} != {hs.target.row_key!r}"
        )

        # Target frame
        assert cs.target.frame.match == hs.target.frame.match, (
            f"{pfx}: frame match {cs.target.frame.match} != {hs.target.frame.match}"
        )
        if cs.target.frame.match == "exact":
            assert cs.target.frame.value == hs.target.frame.value, (
                f"{pfx}: frame value {cs.target.frame.value!r} != {hs.target.frame.value!r}"
            )
        else:
            assert cs.target.frame.name_prefix == hs.target.frame.name_prefix, (
                f"{pfx}: frame prefix {cs.target.frame.name_prefix!r} != {hs.target.frame.name_prefix!r}"
            )

        # Value source
        if hs.value is not None:
            assert cs.value is not None, f"{pfx}: compiled has no value but hand does"
            assert cs.value.source == hs.value.source, (
                f"{pfx}: value source {cs.value.source} != {hs.value.source}"
            )
            if hs.value.source == "input":
                assert cs.value.input_name == hs.value.input_name
            elif hs.value.source == "output":
                assert cs.value.output_name == hs.value.output_name

        # Intent is present and non-empty (text not compared — compiled is
        # mechanical, hand-written is prose)
        assert cs.intent.strip(), f"{pfx}: compiled intent is empty"

        # Rejected: non-empty where hand-written has entries
        if hs.rejected:
            assert cs.rejected, (
                f"{pfx}: hand-written has rejected entries but compiled doesn't"
            )

    # Inputs
    compiled_inputs = {p.name: p.type for p in compiled.inputs}
    hand_inputs = {p.name: p.type for p in hand.inputs}
    assert compiled_inputs == hand_inputs, (
        f"inputs: {compiled_inputs} != {hand_inputs}"
    )

    # Outputs
    compiled_outputs = {p.name: p.type for p in compiled.outputs}
    hand_outputs = {p.name: p.type for p in hand.outputs}
    assert compiled_outputs == hand_outputs, (
        f"outputs: {compiled_outputs} != {hand_outputs}"
    )

    # Checkpoint evidence_refs
    assert set(compiled.checkpoint.evidence_refs) == set(hand.checkpoint.evidence_refs), (
        f"checkpoint refs: {compiled.checkpoint.evidence_refs} != {hand.checkpoint.evidence_refs}"
    )

    # Deliberately ignored fields (documented here for reviewers):
    # - intent wording: compiled generates from action+target, hand is prose
    # - step_id slug: generated vs hand-chosen
    # - created_from: different run ids
    # - description: compiler generates generic text
    # - version: hand-written sets it; compiler defaults
    # - escalation_policy: not compiled from transcript
    # - known_outcomes: hand-written from domain knowledge
    # - overrides: hand-written from config


# --------------------------------------------------------------------------- #
# Tests against the real transcript
# --------------------------------------------------------------------------- #


class TestRealTranscript:
    """Compile the real 65b48ef0 transcript and compare to hand-written."""

    @pytest.fixture
    def compiled(self):
        records = _load_records()
        result = _make_result(records)
        return compile_transcript(
            records, result,
            capability_id="coredesk.member.read_savings_balance",
            inputs={"member_no": "100101"},
            outputs={"savings_balance": "money"},
            description="Read savings balance",
        )

    @pytest.fixture
    def hand_written(self):
        return _load_hand_written()

    def test_compile_validates(self, compiled):
        """Compiled artifact passes schema validation."""
        CapabilityArtifact.model_validate(compiled.model_dump())

    def test_compile_semantic_equality(self, compiled, hand_written):
        """Compiled matches hand-written on all compared fields."""
        assert_semantically_equal(compiled, hand_written)

    def test_compile_populates_rejected(self, compiled):
        """Step 2 had dom_id_observed; compiled must have a rejected entry."""
        step2 = compiled.steps[1]
        assert len(step2.rejected) > 0
        assert step2.rejected[0].strategy == "dom_id"
        assert "ctl00" in step2.rejected[0].value

    def test_compile_ax_relative_locator(self, compiled):
        """Step 5 produces ax_relative with column and row_key."""
        step5 = compiled.steps[4]
        assert step5.target.strategy == "ax_relative"
        assert step5.target.column_header == "AVAILABLE"
        assert step5.target.row_key == "0000"

    def test_compile_frame_ref_role_name(self, compiled):
        """Step 5's frame uses role_name with prefix."""
        step5 = compiled.steps[4]
        assert step5.target.frame.match == "role_name"
        assert step5.target.frame.name_prefix == "Share accounts for member"

    def test_compile_parameterizes_input(self, compiled):
        """Step 2 has from_input; compiled value is source=input."""
        step2 = compiled.steps[1]
        assert step2.value is not None
        assert step2.value.source == "input"
        assert step2.value.input_name == "member_no"

    def test_compile_checkpoint_from_done(self, compiled):
        """Checkpoint references savings_balance."""
        assert "savings_balance" in compiled.checkpoint.evidence_refs

    def test_compile_risk_from_verdict(self, compiled):
        """All steps have risk=safe."""
        for step in compiled.steps:
            assert step.risk == "safe"

    def test_compile_wait_for_click_steps(self, compiled):
        """Click steps get waits; type/read steps get null."""
        # Step 1: click → wait for step 2 target
        assert compiled.steps[0].wait_for is not None
        assert compiled.steps[0].wait_for.role == "textbox"
        assert compiled.steps[0].wait_for.name == "Member Number"

        # Step 2: type → null
        assert compiled.steps[1].wait_for is None

        # Step 3: click → wait for step 4 target
        assert compiled.steps[2].wait_for is not None
        assert compiled.steps[2].wait_for.role == "link"
        assert compiled.steps[2].wait_for.name == "SEL"

        # Step 4: click, cross-frame → wait for table landmark
        assert compiled.steps[3].wait_for is not None
        assert compiled.steps[3].wait_for.role == "table"
        assert compiled.steps[3].wait_for.name == "Share accounts"

        # Step 5: read → null
        assert compiled.steps[4].wait_for is None

    def test_compile_cross_frame_wait_uses_landmark(self, compiled):
        """Step 4's wait is for the table in the iframe, not the cell."""
        wait = compiled.steps[3].wait_for
        assert wait is not None
        assert wait.frame.match == "role_name"
        assert wait.frame.name_prefix == "Share accounts for member"
        assert wait.role == "table"

    def test_compile_read_target_has_no_name(self, compiled):
        """ax_relative locators don't use name (it's the runtime value)."""
        step5 = compiled.steps[4]
        assert step5.target.name is None


# --------------------------------------------------------------------------- #
# Pruning tests with synthetic transcripts
# --------------------------------------------------------------------------- #


class TestPrune:
    def test_prune_removes_detour(self):
        """A->B->C->B->D becomes A->B->D (B->C->B detour removed)."""
        records = [
            _synth_record(1, obs_hash="A", target_name="a"),
            _synth_record(2, obs_hash="B", target_name="b1"),
            _synth_record(3, obs_hash="C", target_name="c"),
            _synth_record(4, obs_hash="B", target_name="b2"),
            _synth_record(5, obs_hash="D", target_name="d"),
            _synth_record(6, action="done", obs_hash="D"),
        ]
        pruned = prune(records)
        action_hashes = [r.obs_hash for r in pruned if r.action_kind != "done"]
        assert action_hashes == ["A", "B", "D"]

    def test_prune_write_barrier(self):
        """A->B->C(write)->B->D keeps the write segment."""
        records = [
            _synth_record(1, obs_hash="A", target_name="a"),
            _synth_record(2, obs_hash="B", target_name="b1"),
            _synth_record(3, obs_hash="C", target_name="c", risk="guarded_write"),
            _synth_record(4, obs_hash="B", target_name="b2"),
            _synth_record(5, obs_hash="D", target_name="d"),
            _synth_record(6, action="done", obs_hash="D"),
        ]
        pruned = prune(records)
        action_hashes = [r.obs_hash for r in pruned if r.action_kind != "done"]
        assert action_hashes == ["A", "B", "C", "B", "D"]

    def test_prune_no_op_on_clean(self):
        """The real transcript has no loops; pruning is identity."""
        records = _load_records()
        action_before = [r for r in records if r.action_kind != "done"]
        pruned = prune(records)
        action_after = [r for r in pruned if r.action_kind != "done"]
        assert len(action_before) == len(action_after)

    def test_prune_preserves_done(self):
        """The done step is always kept through pruning."""
        records = [
            _synth_record(1, obs_hash="A", target_name="a"),
            _synth_record(2, action="done", obs_hash="A"),
        ]
        pruned = prune(records)
        assert any(r.action_kind == "done" for r in pruned)


# --------------------------------------------------------------------------- #
# Compilation failure tests
# --------------------------------------------------------------------------- #


class TestCompilationFailures:
    def test_compile_fails_on_ambiguous_locator(self):
        """Not unique, no scope, no table context -> CompilationError."""
        records = [
            _synth_record(1, obs_hash="A", target_name="Ambiguous",
                          trace_unique=False),
            _synth_record(2, action="done", obs_hash="A"),
        ]
        result = _make_result(records, outputs={})
        with pytest.raises(CompilationError, match="cannot locate"):
            compile_transcript(
                records, result,
                capability_id="test", inputs={}, outputs={},
            )

    def test_compile_fails_on_no_done(self):
        """Transcript without done raises CompilationError."""
        records = [
            _synth_record(1, obs_hash="A"),
        ]
        result = _make_result(records, outputs={})
        with pytest.raises(CompilationError, match="done"):
            compile_transcript(
                records, result,
                capability_id="test", inputs={}, outputs={},
            )

    def test_compile_fails_on_missing_output(self):
        """Done called but declared output never read."""
        records = [
            _synth_record(1, obs_hash="A"),
            _synth_record(2, action="done", obs_hash="A"),
        ]
        result = _make_result(records, outputs={})
        with pytest.raises(CompilationError, match="never read"):
            compile_transcript(
                records, result,
                capability_id="test", inputs={},
                outputs={"balance": "money"},
            )

    def test_compile_fails_on_no_trace(self):
        """Action step with trace=None raises CompilationError."""
        rec = _synth_record(1, obs_hash="A")
        rec = StepRecord(
            step=1, obs_hash="A", action_kind="click", action_args={"ref": "e1"},
            target_role="button", target_name="Btn",
            result_ok=True, result_reason=None, result_detail=None,
            trace=None, verdict_allowed=True, verdict_risk="safe",
            prompt_tokens=0, output_tokens=0, hash_changed=True,
        )
        records = [
            rec,
            _synth_record(2, action="done", obs_hash="A"),
        ]
        result = _make_result(records, outputs={})
        with pytest.raises(CompilationError, match="no ResolutionTrace"):
            compile_transcript(
                records, result,
                capability_id="test", inputs={}, outputs={},
            )


# --------------------------------------------------------------------------- #
# Parameterization tests
# --------------------------------------------------------------------------- #


class TestParameterize:
    def test_literal_flagged_review_required(self):
        """Free text that doesn't match any input -> literal, review_required."""
        records = [
            _synth_record(1, action="type", obs_hash="A", target_name="Field",
                          target_role="textbox",
                          args={"ref": "e1", "text": "some random text"}),
            _synth_record(2, action="done", obs_hash="A",
                          args={"rationale": "done"}),
        ]
        result = _make_result(records, outputs={})
        art = compile_transcript(
            records, result,
            capability_id="test", inputs={"x": "other_value"}, outputs={},
        )
        assert art.steps[0].value is not None
        assert art.steps[0].value.source == "literal"
        assert art.steps[0].value.review_required is True

    def test_literal_matching_input_parameterized(self):
        """Literal that equals a declared input value -> parameterized.

        This is correction A: the compiler must not depend on the model
        using from_input. A model that types '100101' directly instead of
        referencing the input enum shouldn't produce an artifact hardcoded
        to member 100101.
        """
        records = [
            _synth_record(1, action="type", obs_hash="A", target_name="MemberNo",
                          target_role="textbox",
                          args={"ref": "e1", "text": "100101"}),
            _synth_record(2, action="done", obs_hash="A",
                          args={"rationale": "done"}),
        ]
        result = _make_result(records, outputs={})
        art = compile_transcript(
            records, result,
            capability_id="test",
            inputs={"member_no": "100101"},
            outputs={},
        )
        assert art.steps[0].value is not None
        assert art.steps[0].value.source == "input"
        assert art.steps[0].value.input_name == "member_no"

    def test_from_input_parameterized(self):
        """Model used from_input explicitly -> source=input."""
        records = [
            _synth_record(1, action="type", obs_hash="A", target_name="MemberNo",
                          target_role="textbox",
                          args={"ref": "e1", "from_input": "member_no"}),
            _synth_record(2, action="done", obs_hash="A",
                          args={"rationale": "done"}),
        ]
        result = _make_result(records, outputs={})
        art = compile_transcript(
            records, result,
            capability_id="test",
            inputs={"member_no": "100101"},
            outputs={},
        )
        assert art.steps[0].value is not None
        assert art.steps[0].value.source == "input"
        assert art.steps[0].value.input_name == "member_no"


# --------------------------------------------------------------------------- #
# Frame reference tests
# --------------------------------------------------------------------------- #


class TestFrameRef:
    def test_main_frame(self):
        ref = _build_frame_ref("main", {})
        assert ref.match == "exact"
        assert ref.value == "main"

    def test_iframe_with_input_value(self):
        ref = _build_frame_ref(
            "frame[Share accounts for member 100101]",
            {"member_no": "100101"},
        )
        assert ref.match == "role_name"
        assert ref.role == "iframe"
        assert ref.name_prefix == "Share accounts for member"

    def test_iframe_no_input_found(self):
        """Frame with no matching input value falls back to exact."""
        ref = _build_frame_ref(
            "frame[Static sidebar panel]",
            {"member_no": "100101"},
        )
        assert ref.match == "exact"
        assert ref.value == "frame[Static sidebar panel]"

    def test_bare_string(self):
        """Non-frame, non-main strings fall back to exact."""
        ref = _build_frame_ref("something-else", {})
        assert ref.match == "exact"
        assert ref.value == "something-else"


# --------------------------------------------------------------------------- #
# Regression: no locator may bake in the value being read
# --------------------------------------------------------------------------- #


class TestLocatorNeverContainsReadValue:
    """A read step's locator must not use the value it reads as a name.

    The first locator-ladder ordering (ax_scoped before ax_relative) would
    have located step 5 by cell "12,845.50" scoped to the Share accounts
    table.  That resolves correctly for member 100101 and returns the wrong
    answer — or fails — for every other member, because the balance value
    is baked into the locator.

    This test catches the class, not the instance: no compiled read step's
    ``target.name`` may equal the value that step reads into an output.
    """

    def test_real_transcript_read_locators(self):
        """Compiled read steps must not use the output value as a name."""
        records = _load_records()
        result = _make_result(records)
        art = compile_transcript(
            records, result,
            capability_id="coredesk.member.read_savings_balance",
            inputs={"member_no": "100101"},
            outputs={"savings_balance": "money"},
        )
        output_values = set(result.outputs_filled.values())
        for step in art.steps:
            if step.action == "read":
                assert step.target.name not in output_values, (
                    f"step {step.step_id}: locator name {step.target.name!r} "
                    f"equals a read output value — the locator would only "
                    f"resolve for the recorded member"
                )

    def test_no_step_id_contains_a_read_output_value(self):
        """The third field the value leaked into.

        `_make_step_id` built its slug from the observed element name, and
        for an `ax_relative` cell that name is the balance — so a compiled
        step was called `read_12_845_50`.  `step_id` is not internal: it is
        on the review screen's step list, it keys tenant overrides, and it
        is in every failure diagnostic.  Found by reading the output of
        `scripts/verify_gate.py --variant wrong-value`.
        """
        records = _load_records()
        result = _make_result(records)
        art = compile_transcript(
            records, result,
            capability_id="coredesk.member.read_savings_balance",
            inputs={"member_no": "100101"},
            outputs={"savings_balance": "money"},
        )
        # Slugified the same way the id is, since "12,845.50" becomes
        # "12_845_50" by the time it reaches a step id.
        import re as _re

        def slug(v: str) -> str:
            return _re.sub(r"[^a-z0-9]+", "_", v.lower()).strip("_")

        for step in art.steps:
            for value in result.outputs_filled.values():
                assert slug(value) not in step.step_id, (
                    f"step id {step.step_id!r} contains the read value "
                    f"{value!r} — it names one record, not the capability"
                )

    def test_a_read_step_is_named_for_the_output_it_fills(self):
        records = _load_records()
        result = _make_result(records)
        art = compile_transcript(
            records, result,
            capability_id="coredesk.member.read_savings_balance",
            inputs={"member_no": "100101"},
            outputs={"savings_balance": "money"},
        )
        read = next(s for s in art.steps if s.action == "read")
        assert read.step_id == "read_savings_balance"

    def test_no_step_intent_contains_a_read_output_value(self):
        """The same rule, one layer up: an ``intent`` must not quote a value
        the capability reads.

        `_make_intent` used the *observed* element name, and for an
        ax_relative cell that name is the balance.  The compiled intent read
        ``Read cell "12,845.50" into savings_balance`` — true of member
        100101, false of everyone else, and never wrong in a way that
        fails.  A reviewer answers "what does this do?" from ``intent``, so
        this misinforms silently and forever.  Found by the operator
        console's review screen, which is the argument for having one.
        """
        records = _load_records()
        result = _make_result(records)
        art = compile_transcript(
            records, result,
            capability_id="coredesk.member.read_savings_balance",
            inputs={"member_no": "100101"},
            outputs={"savings_balance": "money"},
        )
        output_values = set(result.outputs_filled.values())
        for step in art.steps:
            for value in output_values:
                assert value not in step.intent, (
                    f"step {step.step_id}: intent {step.intent!r} contains "
                    f"the read value {value!r} — it describes one discovery "
                    f"run, not the capability"
                )

    def test_read_intent_describes_the_locator_not_the_value(self):
        """Positive form: the intent names the structure that finds the cell."""
        records = _load_records()
        result = _make_result(records)
        art = compile_transcript(
            records, result,
            capability_id="coredesk.member.read_savings_balance",
            inputs={"member_no": "100101"},
            outputs={"savings_balance": "money"},
        )
        read_step = next(s for s in art.steps if s.action == "read")
        assert read_step.intent == (
            'Read the cell under column "AVAILABLE" in table '
            '"Share accounts" into savings_balance'
        )

    def test_synthetic_read_uses_structural_locator(self):
        """A non-unique cell with table context must use ax_relative."""
        records = [
            _synth_record(
                1, action="read", obs_hash="A",
                target_name="42.00", target_role="cell",
                trace_unique=False,
                trace_frame="main",
                table_context=TableContext(
                    table_name="Balances",
                    column_header="AMOUNT",
                    row_key="0000",
                    col_index=1, row_index=0,
                ),
                args={"ref": "e1", "into_output": "balance"},
            ),
            _synth_record(2, action="done", obs_hash="A",
                          args={"rationale": "done"}),
        ]
        result = _make_result(records, outputs={"balance": "42.00"})
        art = compile_transcript(
            records, result,
            capability_id="test",
            inputs={},
            outputs={"balance": "money"},
        )
        read_step = art.steps[0]
        assert read_step.target.strategy == "ax_relative"
        assert read_step.target.name is None, (
            "ax_relative must not set name — it would bake in the read value"
        )


class TestLocatorNeverBakesInTheAnswer:
    """Rung 1 must not take a name that is the value the step reads.

    `test_real_transcript_read_locators` asserted this against the savings
    transcript, where the balance cell was *not* unique in frame, so rung 2
    won and the guard never had to fire.  Compiling the card-lock capability
    found the hole: `cell "LOCKED"` IS unique on that page, so rung 1 took
    it and produced a locator that only resolves for a member whose card is
    already locked.  Ordering plus one transcript was not a structural
    guarantee.
    """

    def _read_record(self, **kw):
        defaults = dict(
            action="read", obs_hash="A", target_role="cell",
            trace_unique=True, trace_frame="main",
            args={"ref": "e1", "into_output": "card_status"},
        )
        defaults.update(kw)
        return _synth_record(1, **defaults)

    def test_unique_name_equal_to_the_read_value_is_refused(self):
        rec = self._read_record(target_name="LOCKED")
        rec.result_detail = "LOCKED"
        records = [rec, _synth_record(2, action="done", obs_hash="A",
                                      args={"rationale": "done"})]
        result = _make_result(records, outputs={"card_status": "LOCKED"})

        with pytest.raises(CompilationError, match="value it reads"):
            compile_transcript(
                records, result, capability_id="test",
                inputs={}, outputs={"card_status": "text"},
            )

    def test_it_falls_through_to_a_structural_locator_when_one_exists(self):
        """Refusing rung 1 is not refusing the step — rung 2 still applies."""
        rec = self._read_record(
            target_name="LOCKED",
            table_context=TableContext(
                table_name="Card detail", column_header="VALUE",
                row_key="Status", col_index=1, row_index=3,
            ),
        )
        rec.result_detail = "LOCKED"
        records = [rec, _synth_record(2, action="done", obs_hash="A",
                                      args={"rationale": "done"})]
        result = _make_result(records, outputs={"card_status": "LOCKED"})

        art = compile_transcript(
            records, result, capability_id="test",
            inputs={}, outputs={"card_status": "text"},
        )
        step = art.steps[0]
        assert step.target.strategy == "ax_relative"
        assert step.target.name is None
        # The refusal is recorded, not silently dropped — a reviewer should
        # see that a name-based locator was available and turned down.
        assert any(r.strategy == "ax" and r.value == "LOCKED"
                   for r in step.rejected)

    def test_a_name_that_is_not_the_answer_still_uses_rung_one(self):
        """The guard must not fire on an ordinary read of a labelled cell."""
        rec = self._read_record(target_name="Available Balance")
        rec.result_detail = "412.09"
        records = [rec, _synth_record(2, action="done", obs_hash="A",
                                      args={"rationale": "done"})]
        result = _make_result(records, outputs={"card_status": "412.09"})

        art = compile_transcript(
            records, result, capability_id="test",
            inputs={}, outputs={"card_status": "text"},
        )
        assert art.steps[0].target.strategy == "ax"
        assert art.steps[0].target.name == "Available Balance"


class TestUnnameableTargetFailsCleanly:
    """A banner paragraph carries its text as content, not as a name.

    An empty name is trivially "unique in frame", so rung 1 took it and
    built `ax` with `name=""` — which the schema rejects with a pydantic
    error from three frames down rather than the compilation failure this
    ladder exists to raise.
    """

    def test_empty_name_raises_a_compilation_error_not_a_validation_error(self):
        rec = _synth_record(
            1, action="read", obs_hash="A",
            target_role="paragraph", target_name="",
            trace_unique=True, trace_frame="main",
            dom_id="ctl00_MainContent_lblBanner",
            args={"ref": "e23", "into_output": "reference"},
        )
        rec.result_detail = "Card updated. Reference: CRD-57970."
        records = [rec, _synth_record(2, action="done", obs_hash="A",
                                      args={"rationale": "done"})]
        result = _make_result(
            records, outputs={"reference": "Card updated. Reference: CRD-57970."}
        )

        with pytest.raises(CompilationError) as exc:
            compile_transcript(
                records, result, capability_id="test",
                inputs={}, outputs={"reference": "text"},
            )
        assert "no accessible name" in str(exc.value)
        assert "step 1" in str(exc.value)


class TestDeclaredRiskMatchesTheSteps:
    """`approval.risk_class` was hardcoded `safe` at compile time.

    A card-lock capability therefore compiled as `safe` while carrying a
    `guarded_write` Apply step. The console showed the disagreement, which
    is right, but the disagreement should not have been possible.
    """

    def test_a_safe_capability_compiles_as_safe(self):
        records = _load_records()
        result = _make_result(records)
        art = compile_transcript(
            records, result, capability_id="test",
            inputs={"member_no": "100101"},
            outputs={"savings_balance": "money"},
        )
        assert art.approval.risk_class == "safe"
        assert art.approval.risk_class == art.derived_risk_class()

    def test_a_guarded_write_step_makes_the_capability_guarded_write(self):
        records = [
            _synth_record(1, action="click", target_name="Apply",
                          target_role="button", obs_hash="A",
                          risk="guarded_write"),
            _synth_record(2, action="done", obs_hash="A",
                          args={"rationale": "done"}),
        ]
        result = _make_result(records, outputs={})
        art = compile_transcript(
            records, result, capability_id="test.write",
            inputs={}, outputs={},
        )
        assert art.approval.risk_class == "guarded_write"
        assert art.approval.risk_class == art.derived_risk_class()

    def test_declared_can_never_disagree_with_derived(self):
        """The property, rather than two examples of it."""
        for risk in ("safe", "guarded_write", "irreversible"):
            records = [
                _synth_record(1, action="click", target_name="Btn",
                              obs_hash="A", risk=risk),
                _synth_record(2, action="done", obs_hash="A",
                              args={"rationale": "done"}),
            ]
            art = compile_transcript(
                records, _make_result(records, outputs={}),
                capability_id="t", inputs={}, outputs={},
            )
            assert art.approval.risk_class == art.derived_risk_class() == risk


class TestDescriptionWarning:
    """`description` is human prose, so the rule is a warning and narrow.

    `card.lock` was first discovered with the goal "Find member 100101...",
    which became its description — a capability that takes the member as a
    parameter, described in terms of one record.
    """

    def test_a_description_naming_an_input_value_warns(self):
        from agent.compile import description_warnings

        w = description_warnings(
            "Find member 100101 and lock their card.",
            {"member_no": "100101"},
        )
        assert len(w) == 1
        assert "100101" in w[0] and "member_no" in w[0]

    def test_a_description_naming_the_parameter_does_not(self):
        from agent.compile import description_warnings

        assert description_warnings(
            "Find the member and lock their card using the given reason.",
            {"member_no": "100101", "reason": "Suspected fraud"},
        ) == []

    def test_it_warns_rather_than_failing_the_compile(self, capsys):
        """A working artifact is not thrown away over wording."""
        records = _load_records()
        result = _make_result(records)
        art = compile_transcript(
            records, result, capability_id="t",
            inputs={"member_no": "100101"},
            outputs={"savings_balance": "money"},
            description="Read the balance for member 100101.",
        )
        assert art is not None
        assert "COMPILE WARNING" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# One rule, six fields
# --------------------------------------------------------------------------- #


class TestNoGeneratedFieldHoldsRecordData:
    """The rule, stated once and checked everywhere.

    Five times the compiler wrote a record's data into a field that should
    describe structure — intent, step_id, a row key, a literal value, a wait
    condition. One root cause: these are built from the element as it
    resolved on the recorded run, and that element carries that record's
    data. Four patches preceded the rule.
    """

    VALUES = {"member_no": "100101", "city": "Tempe"}
    OUTPUTS = {"balance": "12,845.50"}

    def _step(self, **over):
        from artifacts.schema import Locator, Step, WaitCondition

        kw = dict(
            step_id="click_go", ordinal=1, intent="Click Go", action="click",
            target=Locator(
                strategy="ax", frame=FrameRef(match="exact", value="main"),
                role="link", name="Go",
            ),
        )
        target_over = over.pop("target", None)
        wait_over = over.pop("wait_for", None)
        kw.update(over)
        if target_over:
            kw["target"] = target_over
        if wait_over:
            kw["wait_for"] = wait_over
        return Step(**kw)

    @pytest.mark.parametrize("field,step_kwargs", [
        ("intent", {"intent": 'Read cell "12,845.50" into balance'}),
        ("step_id", {"step_id": "read_12_845_50"}),
        ("target.name", {"target": None}),          # filled below
        ("target.row_key", {"target": None}),
        ("target.column_header", {"target": None}),
        ("wait_for.name", {"wait_for": None}),
    ])
    def test_each_generated_field_is_checked(self, field, step_kwargs):
        from agent.compile import record_data_violations
        from artifacts.schema import Locator, WaitCondition

        if field == "target.name":
            step_kwargs = {"target": Locator(
                strategy="ax", frame=FrameRef(match="exact", value="main"),
                role="cell", name="12,845.50")}
        elif field == "target.row_key":
            step_kwargs = {"target": Locator(
                strategy="ax_relative",
                frame=FrameRef(match="exact", value="main"), role="cell",
                table_name="Cards on file", column_header="STATUS",
                row_key="CRD-100101-1", row_match="exact")}
        elif field == "target.column_header":
            step_kwargs = {"target": Locator(
                strategy="ax_relative",
                frame=FrameRef(match="exact", value="main"), role="cell",
                table_name="T", column_header="Tempe", row_key="r",
                row_match="exact")}
        elif field == "wait_for.name":
            step_kwargs = {"wait_for": WaitCondition(
                frame=FrameRef(match="exact", value="main"),
                role="cell", name="Tempe")}

        v = record_data_violations(
            [self._step(**step_kwargs)],
            input_values=self.VALUES, output_values=self.OUTPUTS,
        )
        assert v, f"{field} violation not caught"
        assert field in v[0]

    def test_a_clean_step_passes(self):
        from agent.compile import record_data_violations

        assert record_data_violations(
            [self._step()],
            input_values=self.VALUES, output_values=self.OUTPUTS,
        ) == []

    def test_no_declared_values_means_nothing_to_violate(self):
        from agent.compile import record_data_violations

        assert record_data_violations(
            [self._step(intent="anything at all")],
            input_values={}, output_values={},
        ) == []


class TestTheFiveHistoricalInstances:
    """One regression per bug, so none can return individually."""

    def _compile(self, **over):
        records = _load_records()
        result = _make_result(records)
        kw = dict(capability_id="t", inputs={"member_no": "100101"},
                  outputs={"savings_balance": "money"})
        kw.update(over)
        return compile_transcript(records, result, **kw)

    def test_1_intent_does_not_quote_the_balance(self):
        art = self._compile()
        for s in art.steps:
            assert "12,845.50" not in s.intent

    def test_2_step_id_is_not_read_12_845_50(self):
        art = self._compile()
        assert "read_savings_balance" in [s.step_id for s in art.steps]
        for s in art.steps:
            assert "12_845_50" not in s.step_id

    def test_3_a_row_key_holding_an_input_refuses_to_compile(self):
        """card.lock's case. There is no structural substitute for a row
        key, so the honest outcome is refusal rather than an artifact that
        silently works for one member."""
        from artifacts.schema import Locator

        records = [
            _synth_record(
                1, action="read", obs_hash="A", target_name="LOCKED",
                target_role="cell", trace_unique=False,
                table_context=TableContext(
                    table_name="Cards on file", column_header="STATUS",
                    row_key="CRD-100101-1", col_index=3, row_index=0),
                args={"ref": "e1", "into_output": "card_status"},
            ),
            _synth_record(2, action="done", obs_hash="A",
                          args={"rationale": "done"}),
        ]
        records[0].result_detail = "LOCKED"
        result = _make_result(records, outputs={"card_status": "LOCKED"})

        with pytest.raises(CompilationError) as exc:
            compile_transcript(
                records, result, capability_id="coredesk.card.lock",
                inputs={"member_no": "100101"},
                outputs={"card_status": "text"},
            )
        assert "row_key" in str(exc.value)
        assert "CRD-100101-1" in str(exc.value)
        assert "schema change" in str(exc.value)

    def test_4_a_frozen_literal_is_flagged_for_review(self):
        """The effective date. Not a compile failure — a literal is legal —
        but `review_required` must be set so a reviewer sees it."""
        records = [
            _synth_record(1, action="type", obs_hash="A",
                          target_name="Effective Date", target_role="textbox",
                          args={"ref": "e1", "text": "09/14/2026"}),
            _synth_record(2, action="done", obs_hash="A",
                          args={"rationale": "done"}),
        ]
        art = compile_transcript(
            records, _make_result(records, outputs={}),
            capability_id="t", inputs={"member_no": "100101"}, outputs={},
        )
        v = art.steps[0].value
        assert v.source == "literal" and v.review_required is True

    def test_5_a_wait_does_not_name_a_value(self):
        """The address-update bug: `wait_for` was `cell "Tempe"`, so replay
        with any other city waited ten seconds for a cell that never came."""
        from surface.base import NamedRegion

        records = [
            _synth_record(1, action="click", obs_hash="A",
                          target_name="Continue", target_role="button"),
            _synth_record(
                2, action="read", obs_hash="B", target_name="Tempe",
                target_role="cell", trace_unique=False,
                enclosing=[NamedRegion(role="table",
                                       name="Address change review")],
                table_context=TableContext(
                    table_name="Address change review", column_header="NEW",
                    row_key="City", col_index=2, row_index=1),
                args={"ref": "e2", "into_output": "confirmed_city"},
            ),
            _synth_record(3, action="done", obs_hash="B",
                          args={"rationale": "done"}),
        ]
        records[1].result_detail = "Tempe"
        art = compile_transcript(
            records, _make_result(records, outputs={"confirmed_city": "Tempe"}),
            capability_id="t", inputs={"city": "Tempe"},
            outputs={"confirmed_city": "text"},
        )
        wait = art.steps[0].wait_for
        assert wait is not None, "should fall back to structure, not drop"
        assert wait.name == "Address change review"
        assert wait.name != "Tempe"
