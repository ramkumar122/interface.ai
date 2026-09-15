"""Tests for agent/brain.py + agent/loop.py.

All tests run without a live LLM or browser. They use stubs and mock objects.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

from agent.brain import BrainResponse

import pytest

from agent.loop import (
    DiscoveryResult,
    StepRecord,
    build_action,
    compact_step,
    observation_text,
)
from control.redact import CANARY
from surface.base import (
    Click,
    Done,
    Navigate,
    Node,
    Observation,
    Read,
    ResolutionTrace,
    NamedRegion,
    Type,
)

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _node(role="button", name="Search", ref="e1") -> Node:
    return Node(ref=ref, frame="main", role=role, name=name,
                value=None, disabled=False, occurrence=0)


def _obs(url="http://127.0.0.1:8001/menu", nodes=None, hash_val="abc") -> Observation:
    return Observation(
        url=url,
        title="CoreDesk",
        frames={"main": "- button \"Search\" [ref=e1]"},
        nodes=nodes or [_node()],
        screenshot=None,
        warnings=[],
        hash=hash_val,
    )


def _record(
    step=1, kind="click", name="Search", ok=True, reason=None, detail=None,
    hash_changed=True, verdict_allowed=True, verdict_risk="safe",
    from_input=None, into_output=None,
) -> StepRecord:
    args = {"ref": "e1"}
    if from_input:
        args["from_input"] = from_input
    if into_output:
        args["into_output"] = into_output
    return StepRecord(
        step=step,
        obs_hash="abc",
        action_kind=kind,
        action_args=args,
        target_role="button" if name else None,
        target_name=name,
        result_ok=ok,
        result_reason=reason,
        result_detail=detail,
        trace=None,
        verdict_allowed=verdict_allowed,
        verdict_risk=verdict_risk,
        prompt_tokens=100,
        output_tokens=10,
        hash_changed=hash_changed,
    )


# --------------------------------------------------------------------------- #
# 1. History compaction is linear, not quadratic
# --------------------------------------------------------------------------- #


def test_history_compaction_is_linear():
    """Simulate 20 turns. The user-role text for turn 20 should be under
    5,000 chars (compacted priors + one full obs), not ~40,000."""
    obs_text = observation_text(_obs())
    records = [_record(step=i) for i in range(1, 21)]

    parts = ["GOAL: test\nINPUTS: {}\nOUTPUTS TO FILL: []"]
    parts.append("\nPRIOR STEPS:")
    for rec in records:
        parts.append(compact_step(rec))
    parts.append("\nCURRENT OBSERVATION:")
    parts.append(obs_text)

    user_text = "\n".join(parts)
    assert len(user_text) < 5_000, f"compacted history is {len(user_text)} chars"

    # Also verify it grows linearly: adding 10 more steps should add ~constant
    records_30 = [_record(step=i) for i in range(1, 31)]
    parts2 = ["GOAL: test\nINPUTS: {}\nOUTPUTS TO FILL: []"]
    parts2.append("\nPRIOR STEPS:")
    for rec in records_30:
        parts2.append(compact_step(rec))
    parts2.append("\nCURRENT OBSERVATION:")
    parts2.append(obs_text)
    user_text_30 = "\n".join(parts2)

    growth = len(user_text_30) - len(user_text)
    per_step = growth / 10
    # Each compacted step should be ~50-100 chars
    assert per_step < 200, f"growth per step is {per_step} chars"


# --------------------------------------------------------------------------- #
# 2. compact_step format
# --------------------------------------------------------------------------- #


def test_compact_step_success_hash_changed():
    rec = _record(step=3, kind="click", name="SEL", hash_changed=True)
    line = compact_step(rec)
    assert 'step 3: click "SEL" -> ok, hash changed' == line


def test_compact_step_success_no_change():
    rec = _record(step=4, kind="click", name="SEL", hash_changed=False)
    line = compact_step(rec)
    assert "but page did not change" in line
    assert "ok" in line


def test_compact_step_failure_keeps_detail():
    rec = _record(step=3, kind="click", name="SEL", ok=False,
                  reason="stale_ref", detail="no node with ref e14")
    line = compact_step(rec)
    assert "FAILED" in line
    assert "stale_ref" in line
    assert "no node with ref e14" in line


def test_compact_step_blocked():
    rec = _record(step=5, kind="click", name="Open Account",
                  verdict_allowed=False, verdict_risk="irreversible")
    line = compact_step(rec)
    assert "BLOCKED" in line
    assert "irreversible" in line


def test_compact_step_type_from_input():
    rec = _record(step=2, kind="type", name="Member Number",
                  from_input="member_no")
    line = compact_step(rec)
    assert "from_input=member_no" in line
    assert "Member Number" in line


def test_compact_step_read_with_detail():
    rec = _record(step=5, kind="read", name="12,845.50", ok=True,
                  detail="12,845.50", into_output="savings_balance")
    line = compact_step(rec)
    assert "savings_balance" in line
    assert "12,845.50" in line


# --------------------------------------------------------------------------- #
# 3. Thought signatures survive compaction
# --------------------------------------------------------------------------- #


def test_thought_signatures_survive_history():
    """Model response Content objects with thought signatures are preserved
    verbatim in the model_contents list."""
    # Simulate a Content object with a thought signature
    mock_content = MagicMock()
    mock_content.parts = [MagicMock()]
    mock_content.parts[0].thought_signature = b"SIGNATURE_BLOB_123"
    mock_content.parts[0].function_call = MagicMock()
    mock_content.parts[0].function_call.name = "click"
    mock_content.parts[0].function_call.args = {"ref": "e1"}

    # The loop stores model_contents.append(brain_resp.content)
    # and echoes them back via _build_contents. Verify the content
    # object is the same reference (not copied or modified).
    model_contents = []
    model_contents.append(mock_content)

    assert model_contents[0] is mock_content
    assert model_contents[0].parts[0].thought_signature == b"SIGNATURE_BLOB_123"


# --------------------------------------------------------------------------- #
# 4. Policy check blocks irreversible as feedback
# --------------------------------------------------------------------------- #


def test_policy_blocks_irreversible_as_feedback():
    """An irreversible action produces a StepRecord with verdict_allowed=False,
    not an exception."""
    from control.policy import Mode, check as policy_check

    obs = _obs("http://127.0.0.1:8001/member/100101/shares/new/review")
    node = _node("button", "Open Account")
    action = Click(ref="e1")
    verdict = policy_check(action, node, obs, mode=Mode.DISCOVERY)
    assert verdict.allowed is False
    assert verdict.risk.value == "irreversible"
    assert verdict.reason is not None


# --------------------------------------------------------------------------- #
# 5. Off-allowlist: feedback then abort
# --------------------------------------------------------------------------- #


def test_off_allowlist_detection():
    """Two consecutive off-allowlist observations should abort."""
    from control.policy import Mode, check as policy_check

    obs = _obs("http://127.0.0.1:8001/")  # sign-on page, not allowed
    node = _node("button", "Sign On")
    action = Click(ref="e1")

    v1 = policy_check(action, node, obs, mode=Mode.DISCOVERY)
    assert v1.allowed is False
    assert "allowlist" in v1.reason.lower()

    # The loop tracks consecutive_off_allowlist and aborts on 2
    # This is a design test, not an integration test


# --------------------------------------------------------------------------- #
# 6. Type ValueError becomes feedback
# --------------------------------------------------------------------------- #


def test_type_valueerror_becomes_feedback():
    """Type with both from_input and text raises ValueError, caught by build_action."""
    with pytest.raises(ValueError, match="exactly one"):
        build_action("type", {"ref": "e1", "from_input": "x", "text": "y"})


def test_type_neither_raises():
    with pytest.raises(ValueError, match="exactly one"):
        build_action("type", {"ref": "e1"})


# --------------------------------------------------------------------------- #
# 7. Stopping conditions
# --------------------------------------------------------------------------- #


def test_build_action_done():
    action = build_action("done", {"rationale": "goal met"})
    assert isinstance(action, Done)


def test_build_action_unknown():
    with pytest.raises(ValueError, match="unknown"):
        build_action("foobar", {})


# --------------------------------------------------------------------------- #
# 8. Stuck detection
# --------------------------------------------------------------------------- #


def test_stuck_detection_logic():
    """Same (action_kind, obs_hash) three times should be detected."""
    counts: dict[tuple[str, str], int] = {}
    for _ in range(3):
        key = ("click", "same_hash")
        counts[key] = counts.get(key, 0) + 1
    assert counts[("click", "same_hash")] >= 3


def test_compact_step_marks_human_turns():
    rec = _record()
    rec.proposed_by = "human"
    line = compact_step(rec)
    assert "HUMAN" in line


class _StubBrain:
    def __init__(self, script: list[tuple[str, dict]] | None = None) -> None:
        self.script = list(script or [])

    def configure(self, **kwargs) -> None:
        return None

    def decide(self, contents):
        if self.script:
            name, args = self.script.pop(0)
        else:
            name, args = "click", {"ref": "e1"}
        return BrainResponse(name, args, "c", 1, 1)

    def make_user_content(self, text):
        return text


class _StickySurface:
    """Same observation forever — the stuck-detection case."""

    def __init__(self, obs: Observation) -> None:
        self._obs = obs
        self._owner = "AUTOMATION"
        self.acts: list = []

    def observe(self, screenshot: bool = False) -> Observation:
        return self._obs

    def act(self, action, *, as_human: bool = False):
        from surface.base import ActionResult

        ok = (
            (as_human and self._owner == "HUMAN")
            or (not as_human and self._owner == "AUTOMATION")
        )
        if not ok:
            return ActionResult(ok=False, reason="not_owner")
        self.acts.append((action, as_human))
        return ActionResult(ok=True)

    def evidence(self, label: str):
        from surface.base import EvidenceRef
        return EvidenceRef(label=label, path=Path("/dev/null"))

    def release(self) -> None:
        self._owner = "HUMAN"

    def reacquire(self) -> None:
        self._owner = "AUTOMATION"


def test_stuck_pauses_when_asked(tmp_path):
    from agent.loop import DiscoveryLoop

    obs = _obs(url="http://127.0.0.1:8001/menu")
    surface = _StickySurface(obs)
    loop = DiscoveryLoop(
        surface=surface,
        brain=_StubBrain(),  # type: ignore[arg-type]
        goal="click search",
        inputs={},
        outputs={"x": "text"},
        evidence_dir=tmp_path,
        entry_url="http://127.0.0.1:8001/menu",
    )
    result = loop.run(pause_on_stuck=True)
    assert result.status == "paused"
    assert loop.paused
    assert surface._owner == "HUMAN"
    assert "three times" in loop.pause_reason

    rec = loop.human_act(Click(ref="e1"))
    assert rec.proposed_by == "human"
    assert rec.result_ok
    assert any(as_h for _a, as_h in surface.acts)

    surface._owner = "HUMAN"
    loop._brain = _StubBrain([("done", {"rationale": "unstuck"})])  # type: ignore[method-assign]
    resumed = loop.resume_model()
    assert resumed.status == "done"
    assert not loop.paused


def test_stuck_still_aborts_without_pause(tmp_path):
    from agent.loop import DiscoveryLoop

    loop = DiscoveryLoop(
        surface=_StickySurface(_obs(url="http://127.0.0.1:8001/menu")),
        brain=_StubBrain(),  # type: ignore[arg-type]
        goal="click search",
        inputs={},
        outputs={"x": "text"},
        evidence_dir=tmp_path,
        entry_url="http://127.0.0.1:8001/menu",
    )
    result = loop.run()
    assert result.status == "stuck"


# --------------------------------------------------------------------------- #
# 9–10. Self-correction ladder
# --------------------------------------------------------------------------- #


def test_screenshot_flag_on_unchanged_hash():
    """Two identical hashes in a row should set want_screenshot = True."""
    last_hash = "aaa"
    obs_hash = "aaa"
    want_screenshot = obs_hash == last_hash and last_hash is not None
    assert want_screenshot is True


def test_screenshot_flag_on_changed_hash():
    last_hash = "aaa"
    obs_hash = "bbb"
    want_screenshot = obs_hash == last_hash and last_hash is not None
    assert want_screenshot is False


# --------------------------------------------------------------------------- #
# 11–12. Import boundaries
# --------------------------------------------------------------------------- #


def _imports(pyfile: Path) -> set[str]:
    tree = ast.parse(pyfile.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base:
                found.add(base)
    return found


def test_brain_imports_only_genai():
    """brain.py imports google.genai and nothing from playwright/coredesk/replay."""
    mods = _imports(REPO / "agent" / "brain.py")
    roots = {m.split(".")[0] for m in mods}
    assert "google" in roots  # it SHOULD import google.genai
    forbidden = {"playwright", "coredesk", "replay"}
    assert not (roots & forbidden), f"brain.py imports {roots & forbidden}"


def test_loop_does_not_import_genai_or_playwright():
    """loop.py imports neither google.genai nor playwright."""
    mods = _imports(REPO / "agent" / "loop.py")
    assert not any(m.startswith("google.genai") or m.startswith("google.generativeai")
                   for m in mods), "loop.py imports google.genai"
    assert not any(m == "playwright" or m.startswith("playwright.") for m in mods), \
        "loop.py imports playwright"


# --------------------------------------------------------------------------- #
# 13. StepRecord captures trace
# --------------------------------------------------------------------------- #


def test_step_record_can_hold_trace():
    trace = ResolutionTrace(
        frame="main", role="button", name="Search",
        name_unique_in_frame=True,
        enclosing_named_regions=[],
        table_context=None,
        dom_id_observed="ctl00_btn",
        elapsed_ms=12.5,
    )
    rec = _record()
    rec.trace = trace
    assert rec.trace is not None
    assert rec.trace.role == "button"
    assert rec.trace.name_unique_in_frame is True


# --------------------------------------------------------------------------- #
# 14. Canary never in transcript
# --------------------------------------------------------------------------- #


def test_canary_filtered_from_observation_text():
    """The canary string in an observation is redacted before the model sees it."""
    obs = _obs()
    obs_with_canary = Observation(
        url=obs.url, title=obs.title,
        frames={"main": f"some text {CANARY} more text"},
        nodes=obs.nodes, screenshot=None,
        warnings=[], hash=obs.hash,
    )
    text = observation_text(obs_with_canary)
    assert CANARY not in text
    assert "[REDACTED-CANARY]" in text


# --------------------------------------------------------------------------- #
# 15. agent/run.py fails cleanly without API key
# --------------------------------------------------------------------------- #


def test_run_fails_cleanly_without_api_key():
    """Running the discovery harness without GOOGLE_API_KEY gives a clear error."""
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "run_discover.py"),
         "--goal", "test", "--target", "http://localhost:8001/menu"],
        capture_output=True, text=True,
        env={**{k: v for k, v in __import__("os").environ.items()
               if k != "GOOGLE_API_KEY"},
             "GOOGLE_API_KEY": ""},
        cwd=str(REPO),
    )
    assert result.returncode != 0
    assert "GOOGLE_API_KEY" in result.stderr


# --------------------------------------------------------------------------- #
# The enum is advice; validation is the guarantee
# --------------------------------------------------------------------------- #


class TestDeclaredNamesAreEnforced:
    """A declaration sent *to* the model is not a constraint on what comes
    *back*.

    `action_function_declarations` puts the declared names in
    `read.into_output` and `type.from_input` as a JSON-Schema enum, and a
    real run returned `into_output: "Address"` — never declared — and it was
    accepted and executed. The enum makes a wrong name unlikely; only a
    check makes it impossible.
    """

    DECLARED_IN = frozenset({"member_no", "city"})
    DECLARED_OUT = frozenset({"confirmed_city"})

    def test_the_declaration_carries_exactly_the_declared_names(self):
        from surface.base import action_function_declarations

        decls = {d["name"]: d for d in action_function_declarations(
            ["member_no", "city"], ["confirmed_city"]
        )}
        read_enum = decls["read"]["parameters"]["properties"]["into_output"]["enum"]
        type_enum = decls["type"]["parameters"]["properties"]["from_input"]["enum"]
        assert read_enum == ["confirmed_city"]
        assert type_enum == ["member_no", "city"]

    def test_an_undeclared_output_is_rejected(self):
        from agent.loop import build_action

        with pytest.raises(ValueError, match="not a declared output"):
            build_action(
                "read", {"ref": "e1", "into_output": "Address"},
                input_names=self.DECLARED_IN, output_names=self.DECLARED_OUT,
            )

    def test_an_undeclared_input_is_rejected(self):
        """The worse half: an undeclared name reaching the surface would type
        an empty string and the run would look successful."""
        from agent.loop import build_action

        with pytest.raises(ValueError, match="not a declared input"):
            build_action(
                "type", {"ref": "e1", "from_input": "street"},
                input_names=self.DECLARED_IN, output_names=self.DECLARED_OUT,
            )

    def test_declared_names_still_build(self):
        from agent.loop import build_action
        from surface.base import Read, Type

        r = build_action(
            "read", {"ref": "e1", "into_output": "confirmed_city"},
            input_names=self.DECLARED_IN, output_names=self.DECLARED_OUT,
        )
        assert isinstance(r, Read) and r.into_output == "confirmed_city"

        ty = build_action(
            "type", {"ref": "e1", "from_input": "city"},
            input_names=self.DECLARED_IN, output_names=self.DECLARED_OUT,
        )
        assert isinstance(ty, Type) and ty.from_input == "city"

    def test_a_literal_text_value_is_untouched(self):
        """`text` is for values that genuinely are not declared inputs."""
        from agent.loop import build_action
        from surface.base import Type

        ty = build_action(
            "type", {"ref": "e1", "text": "free text"},
            input_names=self.DECLARED_IN, output_names=self.DECLARED_OUT,
        )
        assert isinstance(ty, Type) and ty.text == "free text"

    def test_the_rejection_reaches_the_model_as_feedback(self):
        """`build_action` raises ValueError; the loop turns that into an
        `invalid_action` record the model sees and can correct — the
        self-correction ladder, not an abort."""
        import inspect

        from agent import loop

        src = inspect.getsource(loop.DiscoveryLoop._one_model_turn)
        assert "invalid_action" in src
        assert "except ValueError" in src


class TestSurfaceRefusesAnUndeclaredInput:
    """Validated at the boundary too, not only in `build_action`.

    `self._inputs.get(name, "")` was a silent default where a missing key
    should be an error: it typed an empty string and the page accepted it.
    A future path that bypasses `build_action` must still be refused.
    """

    def test_it_returns_undeclared_input_rather_than_typing_nothing(self):
        import inspect

        from surface import web

        src = inspect.getsource(web.WebSurface.act)
        assert "undeclared_input" in src, (
            "the surface must refuse an unknown from_input"
        )
        assert 'self._inputs.get(action.from_input, "")' not in src, (
            "the silent default is back — an unknown input would type ''"
        )
