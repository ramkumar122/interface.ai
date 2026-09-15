"""Tests for replay/locate.py, replay/engine.py, replay/verify.py, replay/detect.py.

No browser, no LLM.  Mock surface for engine and verification tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from artifacts.schema import (
    AbsenceCheck,
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
    PresenceCheck,
    Recoverable,
    Step,
    ValueSpec,
    WaitCondition,
)
from control.policy import Mode
from replay.detect import (
    Detector,
    DetectorHit,
    OutcomeReclassification,
    build_detectors,
    build_reclassifications,
    run_detectors,
    try_reclassify,
)
from replay.engine import replay
from replay.locate import LocateError, locate, match_frame
from replay.result import (
    ReplayBusinessOutcome,
    ReplayEscalated,
    ReplayFailure,
    ReplayResult,
    ReplaySuccess,
)
from replay.verify import VerifyResult, verify_artifact
from surface.base import (
    Action,
    ActionResult,
    Click,
    EvidenceRef,
    Node,
    Observation,
    Read,
    Type,
)


# --------------------------------------------------------------------------- #
# Mock surface
# --------------------------------------------------------------------------- #


class MockSurface:
    """State-machine surface for testing replay without a browser.

    ``observations``: list of Observation objects indexed by state.
    ``read_values``: ref -> value returned for Read actions.
    Non-read act() advances state; read act() does not.
    """

    def __init__(
        self,
        observations: list[Observation],
        read_values: dict[str, str] | None = None,
    ) -> None:
        self._observations = observations
        self._read_values = read_values or {}
        self._state = 0
        self._owner = "AUTOMATION"

    def observe(self, **kwargs) -> Observation:
        idx = min(self._state, len(self._observations) - 1)
        return self._observations[idx]

    def act(self, action: Action, *, as_human: bool = False) -> ActionResult:
        owner_ok = (
            (as_human and self._owner == "HUMAN")
            or (not as_human and self._owner == "AUTOMATION")
        )
        if not owner_ok:
            return ActionResult(ok=False, reason="not_owner")
        if isinstance(action, Read) and action.ref in self._read_values:
            return ActionResult(ok=True, detail=self._read_values[action.ref])
        # Non-read actions advance state
        self._state = min(self._state + 1, len(self._observations) - 1)
        return ActionResult(ok=True)

    def evidence(self, label: str) -> EvidenceRef:
        return EvidenceRef(label=label, path=Path("/dev/null"))

    def release(self) -> None:
        self._owner = "HUMAN"

    def reacquire(self) -> None:
        self._owner = "AUTOMATION"


# --------------------------------------------------------------------------- #
# Observation builders
# --------------------------------------------------------------------------- #


def _obs(
    nodes: list[Node],
    frames: dict[str, str] | None = None,
    url: str = "http://test/menu",
) -> Observation:
    """Build a minimal Observation.  Auto-generates frame YAML if not given."""
    if frames is None:
        by_frame: dict[str, list[str]] = {}
        for n in nodes:
            by_frame.setdefault(n.frame, [])
            line = f'- {n.role}'
            if n.name:
                line += f' "{n.name}"'
            if n.value:
                line += f": {n.value}"
            line += f" [ref={n.ref}]"
            by_frame[n.frame].append(line)
        frames = {fp: "\n".join(lines) for fp, lines in by_frame.items()}

    return Observation(
        url=url,
        title="Test",
        frames=frames,
        nodes=nodes,
        screenshot=None,
        warnings=[],
        hash="h0",
    )


def _node(ref: str, role: str, name: str, frame: str = "main",
          value: str | None = None) -> Node:
    return Node(ref=ref, frame=frame, role=role, name=name,
                value=value, disabled=False, occurrence=0)


# --------------------------------------------------------------------------- #
# Shared test observations
# --------------------------------------------------------------------------- #


# State 0: has a link to click
OBS_MENU = _obs([_node("e1", "link", "Go")])

# State 1: has a table with AVAILABLE and LEDGER columns (values differ)
_TABLE_YAML = (
    '- table "Accounts" [ref=e9]\n'
    '  - row [ref=e10]\n'
    '    - columnheader "KEY" [ref=e11]\n'
    '    - columnheader "AVAILABLE" [ref=e12]\n'
    '    - columnheader "LEDGER" [ref=e13]\n'
    '  - row [ref=e20]\n'
    '    - cell "0000" [ref=e21]\n'
    '    - cell "412.09" [ref=e22]\n'
    '    - cell "460.09" [ref=e23]\n'
)

OBS_TABLE = Observation(
    url="http://test/member/100101",
    title="Test",
    frames={"main": _TABLE_YAML},
    nodes=[
        _node("e9", "table", "Accounts"),
        _node("e10", "row", ""),
        _node("e11", "columnheader", "KEY"),
        _node("e12", "columnheader", "AVAILABLE"),
        _node("e13", "columnheader", "LEDGER"),
        _node("e20", "row", ""),
        _node("e21", "cell", "0000"),
        _node("e22", "cell", "412.09"),
        _node("e23", "cell", "460.09"),
    ],
    screenshot=None, warnings=[], hash="h1",
)


# --------------------------------------------------------------------------- #
# Shared test artifacts
# --------------------------------------------------------------------------- #


def _make_artifact(column: str = "AVAILABLE", **kwargs) -> CapabilityArtifact:
    """2-step artifact: click Go → read cell from table."""
    return CapabilityArtifact(
        capability_id="test.read",
        version="1.0",
        description="Test read",
        surface_id="test",
        created_from=CreatedFrom(
            run_id="test", model="test",
            steps_in_transcript=2, wall_seconds=1.0,
        ),
        inputs=[InputParam(
            name="key", type="string", description="key", example="0000",
        )],
        outputs=[OutputParam(
            name="balance", type="money",
            shape="value", description="balance",
        )],
        steps=[
            Step(
                step_id="click_go", ordinal=1,
                intent="Click Go", action="click", risk="safe",
                target=Locator(
                    strategy="ax",
                    frame=FrameRef(match="exact", value="main"),
                    role="link", name="Go",
                ),
                wait_for=WaitCondition(
                    frame=FrameRef(match="exact", value="main"),
                    role="table", name="Accounts",
                ),
            ),
            Step(
                step_id="read_balance", ordinal=2,
                intent="Read balance", action="read", risk="safe",
                target=Locator(
                    strategy="ax_relative",
                    frame=FrameRef(match="exact", value="main"),
                    role="cell",
                    table_name="Accounts",
                    column_header=column,
                    row_key="0000",
                    row_match="exact",
                ),
                value=ValueSpec(source="output", output_name="balance"),
            ),
        ],
        known_outcomes=kwargs.get("known_outcomes", []),
        checkpoint=Checkpoint(
            description="done", evidence_refs=["balance"],
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
        meta=Meta(schema_version="1.0"),
    )


# --------------------------------------------------------------------------- #
# Locate tests
# --------------------------------------------------------------------------- #


class TestLocate:
    def test_ax_finds_unique_node(self):
        obs = _obs([_node("e1", "button", "Search")])
        ref = locate(
            Locator(strategy="ax", frame=FrameRef(match="exact", value="main"),
                    role="button", name="Search"),
            obs, "s1",
        )
        assert ref == "e1"

    def test_ax_rejects_ambiguous(self):
        obs = _obs([
            _node("e1", "link", "Go"),
            Node(ref="e2", frame="main", role="link", name="Go",
                 value=None, disabled=False, occurrence=1),
        ])
        with pytest.raises(LocateError, match="2 nodes match"):
            locate(
                Locator(strategy="ax", frame=FrameRef(match="exact", value="main"),
                        role="link", name="Go"),
                obs, "s1",
            )

    def test_ax_relative_by_column_and_row(self):
        ref = locate(
            Locator(strategy="ax_relative",
                    frame=FrameRef(match="exact", value="main"),
                    role="cell", table_name="Accounts",
                    column_header="AVAILABLE", row_key="0000",
                    row_match="exact"),
            OBS_TABLE, "s1",
        )
        assert ref == "e22"

    def test_ax_relative_ledger_column(self):
        """LEDGER is column index 2; returns a different ref than AVAILABLE."""
        ref = locate(
            Locator(strategy="ax_relative",
                    frame=FrameRef(match="exact", value="main"),
                    role="cell", table_name="Accounts",
                    column_header="LEDGER", row_key="0000",
                    row_match="exact"),
            OBS_TABLE, "s1",
        )
        assert ref == "e23"

    def test_ax_relative_survives_column_reorder(self):
        """Summit reorders columns. Header match is stable; position is not.

        Riverbend results: MBR NO, NAME, STATUS, JOINED, BRANCH
        Summit results:    MBR NO, NAME, STATUS, BRANCH, JOINED

        Index 3 is JOINED on Riverbend and BRANCH on Summit. A positional
        locator would return a plausible date-or-branch with no error.
        """
        riverbend_yaml = (
            '- table "Member search results" [ref=e1]\n'
            '  - row [ref=e2]\n'
            '    - columnheader "MBR NO" [ref=e3]\n'
            '    - columnheader "NAME" [ref=e4]\n'
            '    - columnheader "STATUS" [ref=e5]\n'
            '    - columnheader "JOINED" [ref=e6]\n'
            '    - columnheader "BRANCH" [ref=e7]\n'
            '  - row [ref=e10]\n'
            '    - cell "100101" [ref=e11]\n'
            '    - cell "Alice Nakamura" [ref=e12]\n'
            '    - cell "ACTIVE" [ref=e13]\n'
            '    - cell "03/12/2018" [ref=e14]\n'
            '    - cell "001" [ref=e15]\n'
        )
        summit_yaml = (
            '- table "Member search results" [ref=e1]\n'
            '  - row [ref=e2]\n'
            '    - columnheader "MBR NO" [ref=e3]\n'
            '    - columnheader "NAME" [ref=e4]\n'
            '    - columnheader "STATUS" [ref=e5]\n'
            '    - columnheader "BRANCH" [ref=e6]\n'
            '    - columnheader "JOINED" [ref=e7]\n'
            '  - row [ref=e10]\n'
            '    - cell "100101" [ref=e11]\n'
            '    - cell "Alice Nakamura" [ref=e12]\n'
            '    - cell "ACTIVE" [ref=e13]\n'
            '    - cell "001" [ref=e14]\n'
            '    - cell "03/12/2018" [ref=e15]\n'
        )
        loc = Locator(
            strategy="ax_relative",
            frame=FrameRef(match="exact", value="main"),
            role="cell", table_name="Member search results",
            column_header="JOINED", row_key="100101",
            row_match="exact",
        )
        obs_r = Observation(
            url="http://t/r", title="R", frames={"main": riverbend_yaml},
            nodes=[], screenshot=None, warnings=[], hash="r",
        )
        obs_s = Observation(
            url="http://t/s", title="S", frames={"main": summit_yaml},
            nodes=[], screenshot=None, warnings=[], hash="s",
        )
        # Header match finds JOINED in both layouts (index 3 vs index 4).
        assert locate(loc, obs_r, "s1") == "e14"  # "03/12/2018"
        assert locate(loc, obs_s, "s1") == "e15"  # "03/12/2018"
        # Position 3 is JOINED on Riverbend and BRANCH on Summit — same
        # index, different field. A positional locator would return "001"
        # on Summit and call it a join date.

    def test_ax_relative_nonexistent_column(self):
        with pytest.raises(LocateError, match="AVAILABLE BALANCE.*not found"):
            locate(
                Locator(strategy="ax_relative",
                        frame=FrameRef(match="exact", value="main"),
                        role="cell", table_name="Accounts",
                        column_header="AVAILABLE BALANCE", row_key="0000",
                        row_match="exact"),
                OBS_TABLE, "s1",
            )

    def test_ax_relative_lists_available_columns(self):
        """Error diagnostic lists the actual column names."""
        with pytest.raises(LocateError) as exc_info:
            locate(
                Locator(strategy="ax_relative",
                        frame=FrameRef(match="exact", value="main"),
                        role="cell", table_name="Accounts",
                        column_header="NOPE", row_key="0000",
                        row_match="exact"),
                OBS_TABLE, "s1",
            )
        assert "KEY" in str(exc_info.value)
        assert "AVAILABLE" in str(exc_info.value)
        assert "LEDGER" in str(exc_info.value)

    def test_frame_exact_match(self):
        assert match_frame(FrameRef(match="exact", value="main"), "main")
        assert not match_frame(FrameRef(match="exact", value="main"), "frame[X]")

    def test_frame_role_name_prefix(self):
        ref = FrameRef(match="role_name", role="iframe",
                       name_prefix="Share accounts for member")
        assert match_frame(ref, "frame[Share accounts for member 100101]")
        assert match_frame(ref, "frame[Share accounts for member 999999]")
        assert not match_frame(ref, "frame[Other panel]")
        assert not match_frame(ref, "main")

    def test_ax_scoped(self):
        """Node found within a named region."""
        yaml_text = (
            '- table "Results" [ref=e10]\n'
            '  - row [ref=e11]\n'
            '    - cell "hello" [ref=e12]\n'
        )
        obs = Observation(
            url="http://test", title="T",
            frames={"main": yaml_text},
            nodes=[
                _node("e10", "table", "Results"),
                _node("e11", "row", ""),
                _node("e12", "cell", "hello"),
            ],
            screenshot=None, warnings=[], hash="h",
        )
        ref = locate(
            Locator(
                strategy="ax_scoped",
                frame=FrameRef(match="exact", value="main"),
                role="cell", name="hello",
                within=NamedRegionRef(role="table", name="Results"),
            ),
            obs, "s1",
        )
        assert ref == "e12"


# --------------------------------------------------------------------------- #
# Detector tests
# --------------------------------------------------------------------------- #


class TestDetectors:
    def test_page_text_detector_fires(self):
        """Detector with page_text_pattern matches node text."""
        obs = _obs([_node("e1", "heading", "CoreDesk Application Error")])
        import re
        d = Detector(
            id="APP_ERROR", kind="hard_failure",
            page_text_pattern=re.compile(r"CoreDesk Application Error"),
        )
        hit = run_detectors([d], obs)
        assert hit is not None
        assert hit.kind == "hard_failure"
        assert hit.detector_id == "APP_ERROR"

    def test_page_text_detector_no_match(self):
        obs = _obs([_node("e1", "heading", "Welcome")])
        import re
        d = Detector(
            id="APP_ERROR", kind="hard_failure",
            page_text_pattern=re.compile(r"CoreDesk Application Error"),
        )
        assert run_detectors([d], obs) is None

    def test_role_match_detector(self):
        """Detector with page_role_match fires on matching node."""
        obs = _obs([_node("e1", "dialog", "Scheduled maintenance")])
        d = Detector(
            id="maintenance", kind="recoverable",
            page_role_match=("dialog", "Scheduled maintenance"),
            recovery_action="dismiss",
            max_attempts=1,
        )
        hit = run_detectors([d], obs)
        assert hit is not None
        assert hit.kind == "recoverable"
        assert hit.recovery_action == "dismiss"

    def test_detection_order_business_before_recoverable(self):
        """Business outcome fires before recoverable on same observation."""
        import re
        obs = _obs([
            _node("e1", "paragraph", "Member record not found."),
            _node("e2", "dialog", "Scheduled maintenance"),
        ])
        detectors = [
            Detector(id="MNF", kind="business_outcome",
                     page_text_pattern=re.compile(r"Member record not found"),
                     outcome_id="MEMBER_NOT_FOUND", caller_action="return"),
            Detector(id="maint", kind="recoverable",
                     page_role_match=("dialog", "Scheduled maintenance"),
                     recovery_action="dismiss", max_attempts=1),
        ]
        hit = run_detectors(detectors, obs)
        assert hit is not None
        assert hit.outcome_id == "MEMBER_NOT_FOUND"

    def test_build_detectors_from_artifact(self):
        """build_detectors produces detectors from known_outcomes and recoverables."""
        art = _make_artifact(
            known_outcomes=[
                KnownOutcome(
                    id="MEMBER_NOT_FOUND",
                    condition="Search returns no results",
                    business_meaning="Member not found",
                    caller_action="return",
                    page_text_pattern="Member record not found",
                ),
            ],
            recoverables=[
                Recoverable(
                    trigger="maintenance_notice",
                    max_retries=1,
                    strategy="dismiss",
                    trigger_role="dialog",
                    trigger_name="Scheduled maintenance",
                    recovery_action="dismiss",
                ),
            ],
        )
        detectors = build_detectors(art)
        # Business outcome first, then built-in APP_ERROR, then recoverables
        kinds = [d.kind for d in detectors]
        assert kinds.index("business_outcome") < kinds.index("recoverable")

    def test_reclassification_positive_evidence(self):
        """LocateError reclassified when table present AND row absent."""
        rc = OutcomeReclassification(
            outcome_id="NO_SAVINGS",
            at_step="read_balance",
            caller_action="return",
            condition="no savings share",
            business_meaning="no savings",
            requires_present=[{"role": "table", "name": "Accounts"}],
            requires_absent=[{"strategy": "ax_relative",
                              "table_name": "Accounts", "row_key": "0000"}],
        )
        # Table present but NO row "0000"
        yaml_no_row = (
            '- table "Accounts" [ref=e9]\n'
            '  - row [ref=e10]\n'
            '    - columnheader "KEY" [ref=e11]\n'
            '  - row [ref=e20]\n'
            '    - cell "0070" [ref=e21]\n'
        )
        obs_no_savings = Observation(
            url="http://test", title="T",
            frames={"main": yaml_no_row},
            nodes=[_node("e9", "table", "Accounts"),
                   _node("e21", "cell", "0070")],
            screenshot=None, warnings=[], hash="h",
        )
        error = LocateError("read_balance", "ax_relative",
                            'row with key "0000" not found')
        hit = try_reclassify(error, "read_balance", obs_no_savings, [rc])
        assert hit is not None
        assert hit.outcome_id == "NO_SAVINGS"

    def test_reclassification_fails_when_table_missing(self):
        """If the table itself is missing, LocateError stays a failure.

        This is the key safety property: iframe didn't load → no table →
        requires_present fails → error NOT reclassified as business outcome.
        """
        rc = OutcomeReclassification(
            outcome_id="NO_SAVINGS",
            at_step="read_balance",
            caller_action="return",
            condition="no savings share",
            business_meaning="no savings",
            requires_present=[{"role": "table", "name": "Accounts"}],
            requires_absent=[{"strategy": "ax_relative",
                              "table_name": "Accounts", "row_key": "0000"}],
        )
        # Empty page — no table at all
        obs_broken = _obs([_node("e1", "heading", "Error")])
        error = LocateError("read_balance", "ax_relative",
                            'row with key "0000" not found')
        hit = try_reclassify(error, "read_balance", obs_broken, [rc])
        assert hit is None  # NOT reclassified — stays a failure

    def test_reclassification_fails_when_row_present(self):
        """If the row IS present, the LocateError is NOT a business outcome.

        This catches the case where the locate error was for a different reason
        (e.g. column header changed) but the row actually exists.
        """
        rc = OutcomeReclassification(
            outcome_id="NO_SAVINGS",
            at_step="read_balance",
            caller_action="return",
            condition="no savings share",
            business_meaning="no savings",
            requires_present=[{"role": "table", "name": "Accounts"}],
            requires_absent=[{"strategy": "ax_relative",
                              "table_name": "Accounts", "row_key": "0000"}],
        )
        # Row "0000" IS present — should NOT reclassify
        hit = try_reclassify(
            LocateError("read_balance", "ax_relative", "column X not found"),
            "read_balance", OBS_TABLE, [rc],
        )
        assert hit is None


# --------------------------------------------------------------------------- #
# Engine tests
# --------------------------------------------------------------------------- #


class TestEngine:
    def test_happy_path(self):
        """Click → read, outputs filled."""
        mock = MockSurface(
            observations=[OBS_MENU, OBS_TABLE],
            read_values={"e22": "412.09"},
        )
        art = _make_artifact("AVAILABLE")
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)

        assert isinstance(result, ReplaySuccess)
        assert result.status == "success"
        assert result.outputs == {"balance": "412.09"}
        assert len(result.steps) == 2

    def test_tenant_override_applied_before_locate(self):
        """tenant='summit' remaps the click target; without it, locate fails."""
        art = _make_artifact("AVAILABLE")
        data = art.model_dump()
        data["overrides"] = {
            "summit": {
                "steps": {
                    "click_go": {
                        "target": {
                            "name": {"from_value": "Go", "to_value": "Proceed"},
                        },
                    },
                },
            },
        }
        art = CapabilityArtifact.model_validate(data)

        obs_summit = _obs([_node("e1", "link", "Proceed")])
        mock = MockSurface(
            observations=[obs_summit, OBS_TABLE],
            read_values={"e22": "412.09"},
        )
        without = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)
        assert isinstance(without, ReplayFailure)

        mock2 = MockSurface(
            observations=[obs_summit, OBS_TABLE],
            read_values={"e22": "412.09"},
        )
        with_override = replay(
            art, mock2, {"key": "0000"}, wait_timeout=0.1, tenant="summit",
        )
        assert isinstance(with_override, ReplaySuccess)
        assert with_override.outputs == {"balance": "412.09"}

    def test_locate_failure_is_structured(self):
        """Wrong name → ReplayFailure with diagnostic, not an exception."""
        art = _make_artifact("AVAILABLE")
        art.steps[0].target.name = "MISSING_LINK"

        mock = MockSurface(observations=[OBS_MENU, OBS_TABLE])
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)

        assert isinstance(result, ReplayFailure)
        assert result.status == "failure"
        assert result.step_id == "click_go"
        assert "MISSING_LINK" in result.error

    def test_policy_mode_passed_through(self):
        """Engine uses the explicit policy_mode, not a hidden default."""
        art = _make_artifact("AVAILABLE")
        mock = MockSurface(
            observations=[OBS_MENU, OBS_TABLE],
            read_values={"e22": "412.09"},
        )
        result = replay(art, mock, {"key": "0000"},
                        policy_mode=Mode.DISCOVERY, wait_timeout=0.1)
        assert isinstance(result, ReplaySuccess)

    def test_type_coercion_rejects_non_money(self):
        """Read returns 'OPEN' into a money output → step-level failure."""
        art = _make_artifact("AVAILABLE")
        mock = MockSurface(
            observations=[OBS_MENU, OBS_TABLE],
            read_values={"e22": "OPEN"},
        )
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)

        assert isinstance(result, ReplayFailure)
        assert result.step_id == "read_balance"
        assert "not valid money" in result.error

    def test_type_coercion_accepts_valid_money(self):
        """Read returns '412.09' into a money output → passes coercion."""
        art = _make_artifact("AVAILABLE")
        mock = MockSurface(
            observations=[OBS_MENU, OBS_TABLE],
            read_values={"e22": "412.09"},
        )
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)
        assert isinstance(result, ReplaySuccess)
        assert result.outputs["balance"] == "412.09"

    def test_business_outcome_via_detector(self):
        """Detector fires on app_error page → returns ReplayFailure (hard)."""
        obs_error = _obs([
            _node("e1", "heading", "CoreDesk Application Error"),
        ])
        art = _make_artifact("AVAILABLE")
        mock = MockSurface(observations=[obs_error])
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)

        assert isinstance(result, ReplayFailure)
        assert "APP_ERROR" in result.error

    def test_business_outcome_via_page_text(self):
        """Known outcome detected by page text pattern."""
        obs_mnf = _obs([
            _node("e1", "paragraph", "Member record not found."),
        ])
        art = _make_artifact(
            known_outcomes=[
                KnownOutcome(
                    id="MEMBER_NOT_FOUND",
                    condition="No results",
                    business_meaning="Member not found",
                    caller_action="return",
                    page_text_pattern="Member record not found",
                ),
            ],
        )
        mock = MockSurface(observations=[obs_mnf])
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)

        assert isinstance(result, ReplayBusinessOutcome)
        assert result.status == "business_outcome"
        assert result.outcome_id == "MEMBER_NOT_FOUND"
        assert result.caller_action == "return"
        assert result.at_step == "click_go"

    def test_reclassification_in_engine(self):
        """LocateError at read step reclassified as business outcome."""
        # Table present but row "0000" missing
        yaml_no_row = (
            '- table "Accounts" [ref=e9]\n'
            '  - row [ref=e10]\n'
            '    - columnheader "KEY" [ref=e11]\n'
            '    - columnheader "AVAILABLE" [ref=e12]\n'
            '  - row [ref=e20]\n'
            '    - cell "0070" [ref=e21]\n'
            '    - cell "500.00" [ref=e22]\n'
        )
        obs_no_savings = Observation(
            url="http://test/member/100102", title="T",
            frames={"main": yaml_no_row},
            nodes=[
                _node("e9", "table", "Accounts"),
                _node("e21", "cell", "0070"),
                _node("e22", "cell", "500.00"),
            ],
            screenshot=None, warnings=[], hash="h2",
        )
        art = _make_artifact(
            known_outcomes=[
                KnownOutcome(
                    id="NO_SAVINGS_ACCOUNT",
                    condition="No savings share",
                    business_meaning="No savings account",
                    caller_action="return",
                    at_step="read_balance",
                    requires_present=[
                        PresenceCheck(role="table", name="Accounts"),
                    ],
                    requires_absent=[
                        AbsenceCheck(strategy="ax_relative",
                                     table_name="Accounts", row_key="0000"),
                    ],
                ),
            ],
        )
        mock = MockSurface(
            observations=[OBS_MENU, obs_no_savings],
            read_values={},
        )
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)

        assert isinstance(result, ReplayBusinessOutcome)
        assert result.outcome_id == "NO_SAVINGS_ACCOUNT"
        assert result.at_step == "read_balance"

    def test_locate_error_not_reclassified_when_table_missing(self):
        """If table missing, LocateError stays a failure, not reclassified.

        Uses a 1-step artifact (read only, no click+wait) against a page
        with no table.  The LocateError should NOT be reclassified as
        NO_SAVINGS_ACCOUNT because requires_present (table) fails.
        """
        obs_broken = _obs(
            [_node("e1", "heading", "Error")],
            url="http://test/member/100102",
        )
        # 1-step artifact: just the read, no click+wait
        art = CapabilityArtifact(
            capability_id="test.read_only",
            version="1.0",
            description="Read only",
            surface_id="test",
            created_from=CreatedFrom(
                run_id="t", model="t", steps_in_transcript=1, wall_seconds=1,
            ),
            inputs=[InputParam(name="key", type="string",
                               description="k", example="0000")],
            outputs=[OutputParam(name="balance", type="money",
                                 shape="v", description="b")],
            steps=[Step(
                step_id="read_balance", ordinal=1,
                intent="Read balance", action="read", risk="safe",
                target=Locator(
                    strategy="ax_relative",
                    frame=FrameRef(match="exact", value="main"),
                    role="cell", table_name="Accounts",
                    column_header="AVAILABLE", row_key="0000",
                    row_match="exact",
                ),
                value=ValueSpec(source="output", output_name="balance"),
            )],
            known_outcomes=[
                KnownOutcome(
                    id="NO_SAVINGS_ACCOUNT",
                    condition="No savings share",
                    business_meaning="No savings account",
                    caller_action="return",
                    at_step="read_balance",
                    requires_present=[
                        PresenceCheck(role="table", name="Accounts"),
                    ],
                    requires_absent=[
                        AbsenceCheck(strategy="ax_relative",
                                     table_name="Accounts", row_key="0000"),
                    ],
                ),
            ],
            checkpoint=Checkpoint(description="d", evidence_refs=["balance"],
                                  proposed_by="compiler"),
            escalation_policy=EscalationPolicy(
                on_stuck="x", on_unknown_state="x", on_blocked="x"),
            approval=Approval(status="draft", approved_by=None,
                              approved_at=None, risk_class="safe"),
            meta=Meta(schema_version="1.0"),
        )
        mock = MockSurface(observations=[obs_broken])
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)

        assert isinstance(result, ReplayFailure)
        assert result.step_id == "read_balance"

    def test_recoverable_dismiss(self):
        """Maintenance dialog dismissed using declared target, step proceeds."""
        obs_with_dialog = _obs([
            _node("e1", "link", "Go"),
            _node("e5", "dialog", "Scheduled maintenance"),
            _node("e6", "link", "Close"),
        ])
        art = _make_artifact(
            recoverables=[
                Recoverable(
                    trigger="maintenance_notice",
                    max_retries=1,
                    strategy="dismiss",
                    trigger_role="dialog",
                    trigger_name="Scheduled maintenance",
                    recovery_action="dismiss",
                    recovery_target_role="link",
                    recovery_target_name="Close",
                ),
            ],
        )

        class RecoverySurface(MockSurface):
            """After dismiss, returns clean observation."""
            def __init__(self):
                super().__init__(
                    observations=[obs_with_dialog, OBS_MENU, OBS_TABLE],
                    read_values={"e22": "412.09"},
                )
                self._dismissed = False

            def act(self, action):
                if isinstance(action, Click) and action.ref == "e6":
                    self._dismissed = True
                    self._state = 1  # Jump to clean state
                    return ActionResult(ok=True)
                return super().act(action)

            def observe(self, **kwargs):
                if self._dismissed and self._state == 1:
                    return OBS_MENU
                return super().observe(**kwargs)

        mock = RecoverySurface()
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)
        assert isinstance(result, ReplaySuccess)

    def test_session_expired_escalates(self):
        """session_expired with recovery_action=escalate → ReplayEscalated."""
        obs_expired = _obs(
            [_node("e1", "paragraph", "Your session has expired.")],
            url="http://test/",
        )
        art = _make_artifact(
            recoverables=[
                Recoverable(
                    trigger="session_expired",
                    max_retries=0,
                    strategy="escalate",
                    trigger_pattern="Your session has expired",
                    recovery_action="escalate",
                ),
            ],
        )
        mock = MockSurface(observations=[obs_expired])
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)

        assert isinstance(result, ReplayEscalated)
        assert result.status == "escalated"
        assert result.request.step_id == "click_go"
        assert "session_expired" in result.request.reason
        assert result.resume_token  # non-empty

    def test_act_rejected_during_human_ownership(self):
        """Surface.act() returns not_owner while ownership is HUMAN."""
        mock = MockSurface(observations=[OBS_MENU])
        mock.release()
        result = mock.act(Click(ref="e1"))
        assert not result.ok
        assert result.reason == "not_owner"

    def test_resume_after_escalation(self):
        """Resume from an escalated state re-validates and continues."""
        from replay.engine import resume as engine_resume
        art = _make_artifact(
            recoverables=[
                Recoverable(
                    trigger="session_expired",
                    max_retries=0,
                    strategy="escalate",
                    trigger_pattern="Your session has expired",
                    recovery_action="escalate",
                ),
            ],
        )
        # Step 1 sees expired page → escalation
        obs_expired = _obs(
            [_node("e1", "paragraph", "Your session has expired.")],
            url="http://test/",
        )
        mock = MockSurface(observations=[obs_expired])
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)
        assert isinstance(result, ReplayEscalated)
        assert result._engine_state is not None

        # Human fixes: swap to normal flow (menu → table)
        mock._observations = [OBS_MENU, OBS_TABLE]
        mock._state = 0
        mock._read_values = {"e22": "412.09"}
        mock._owner = "HUMAN"  # currently held by human

        result2 = engine_resume(result, mock)
        assert isinstance(result2, ReplaySuccess)
        assert result2.outputs.get("balance") == "412.09"


# --------------------------------------------------------------------------- #
# Verification tests
# --------------------------------------------------------------------------- #


class TestVerify:
    def _factory(self, read_values=None):
        """Returns a factory that creates fresh MockSurfaces."""
        rv = read_values or {"e22": "412.09"}
        def make():
            return MockSurface(
                observations=[OBS_MENU, OBS_TABLE],
                read_values=dict(rv),
            )
        return make

    def test_happy_path(self):
        art = _make_artifact("AVAILABLE")
        vr = verify_artifact(
            art, self._factory(), {"key": "0000"},
            {"balance": "412.09"}, n=2, wait_timeout=0.1,
            also=[{"key": "0070"}],
        )
        assert vr.passed
        assert len(vr.runs) == 3      # 2 same-set + 1 second set
        assert art.meta.verified_runs == 3

    def test_rejects_wrong_output(self):
        """Read returns 412.09 but caller expects 999.99."""
        art = _make_artifact("AVAILABLE")
        vr = verify_artifact(
            art, self._factory(), {"key": "0000"},
            {"balance": "999.99"}, n=1, wait_timeout=0.1,
            also=[{"key": "0070"}],
        )
        assert not vr.passed
        assert "999.99" in (vr.failure_summary or "")

    def test_gate_nonexistent_column(self):
        """Column 'AVAILABLE BALANCE' doesn't exist → locate fails."""
        art = _make_artifact("AVAILABLE BALANCE")
        vr = verify_artifact(
            art, self._factory(), {"key": "0000"},
            {"balance": "412.09"}, n=1, wait_timeout=0.1,
            also=[{"key": "0070"}],
        )
        assert not vr.passed
        assert "AVAILABLE BALANCE" in (vr.failure_summary or "")
        assert "read_balance" in (vr.failure_summary or "")

    def test_gate_wrong_column_value(self):
        """Column 'LEDGER' resolves fine but returns 460.09, expected 412.09."""
        art = _make_artifact("LEDGER")
        factory = self._factory(read_values={"e23": "460.09"})
        vr = verify_artifact(
            art, factory, {"key": "0000"},
            {"balance": "412.09"},
            n=1, wait_timeout=0.1, also=[{"key": "0070"}],
        )
        assert not vr.passed
        assert "460.09" in (vr.failure_summary or "")
        assert "412.09" in (vr.failure_summary or "")

    def test_rejects_unstable_output(self):
        """Run 1 returns X, run 2 returns Y → stability check fails."""
        call_count = 0

        def unstable_factory():
            nonlocal call_count
            call_count += 1
            val = "412.09" if call_count == 1 else "999.00"
            return MockSurface(
                observations=[OBS_MENU, OBS_TABLE],
                read_values={"e22": val},
            )

        art = _make_artifact("AVAILABLE")
        vr = verify_artifact(
            art, unstable_factory, {"key": "0000"},
            {"balance": "412.09"}, n=2, wait_timeout=0.1,
        )
        # Run 2 returns "999.00" which doesn't equal expected "412.09"
        assert not vr.passed

    def test_string_exact_comparison(self):
        """'412.09' != '412.9' — no normalisation."""
        art = _make_artifact("AVAILABLE")
        factory = self._factory(read_values={"e22": "412.9"})
        vr = verify_artifact(
            art, factory, {"key": "0000"},
            {"balance": "412.09"}, n=1, wait_timeout=0.1,
            also=[{"key": "0070"}],
        )
        assert not vr.passed
        assert "412.9" in (vr.failure_summary or "")

    def test_business_outcome_fails_verification(self):
        """A business outcome during verification is a failure."""
        obs_mnf = _obs([
            _node("e1", "paragraph", "Member record not found."),
        ])
        art = _make_artifact(
            known_outcomes=[
                KnownOutcome(
                    id="MEMBER_NOT_FOUND",
                    condition="No results",
                    business_meaning="Member not found",
                    caller_action="return",
                    page_text_pattern="Member record not found",
                ),
            ],
        )

        def factory():
            return MockSurface(observations=[obs_mnf])

        vr = verify_artifact(
            art, factory, {"key": "0000"},
            {"balance": "412.09"}, n=1, wait_timeout=0.1, also=[{"key": "0070"}],
        )
        assert not vr.passed
        assert "business_outcome" in (vr.failure_summary or "")
        assert "MEMBER_NOT_FOUND" in (vr.failure_summary or "")


class TestOutcomeMessageIsConsistentAcrossDetectionPaths:
    """`message` must mean one thing regardless of which detector fired.

    It used to mean two: the reclassification path set it to the artifact's
    `business_meaning`, the page-text path to `f"detected: {id}"`.  A caller
    rendering `message` as an answer got "The member has no primary savings
    account" from one path and "detected: MEMBER_NOT_FOUND" from the other.
    The operator console worked around it by looking `business_meaning` up
    itself; the next consumer would not have known to.
    """

    def test_page_text_path_reports_the_business_meaning(self):
        from replay.detect import build_detectors, run_detectors

        art = _make_artifact(
            known_outcomes=[
                KnownOutcome(
                    id="MEMBER_NOT_FOUND",
                    condition="Search returns zero results",
                    business_meaning="The member number does not exist",
                    caller_action="return",
                    page_text_pattern="No records found",
                ),
            ],
        )
        obs = _obs([_node("e1", "paragraph", "No records found")])
        hit = run_detectors(build_detectors(art), obs)

        assert hit is not None
        assert hit.message == "The member number does not exist"
        assert hit.condition == "Search returns zero results"
        assert "detected:" not in hit.message

    def test_reclassification_path_reports_the_same_shape(self):
        from replay.detect import build_reclassifications

        art = _make_artifact(
            known_outcomes=[
                KnownOutcome(
                    id="NO_SAVINGS_ACCOUNT",
                    condition="Member exists but has no savings share",
                    business_meaning="The member has no primary savings account",
                    caller_action="return",
                    at_step="read_balance",
                    requires_present=[
                        PresenceCheck(role="table", name="Share accounts"),
                    ],
                ),
            ],
        )
        rc = build_reclassifications(art)[0]
        assert rc.business_meaning == "The member has no primary savings account"
        assert rc.condition == "Member exists but has no savings share"

    def test_a_recoverable_has_no_business_meaning_to_report(self):
        """`Recoverable` has no such field, and "the session expired" is not a
        business result — the human sentence lives in the instructions."""
        from replay.detect import build_detectors, run_detectors

        art = _make_artifact(
            recoverables=[
                Recoverable(
                    trigger="session_expired", max_retries=0,
                    strategy="escalate",
                    trigger_pattern="Your session has expired",
                    recovery_action="escalate",
                ),
            ],
        )
        obs = _obs([_node("e1", "paragraph", "Your session has expired.")])
        hit = run_detectors(build_detectors(art), obs)

        assert hit is not None
        assert hit.kind == "recoverable"
        assert hit.message == "detected: session_expired"

    def test_engine_reports_condition_and_message_separately(self):
        """End to end: the two fields stop being copies of each other."""
        art = _make_artifact(
            known_outcomes=[
                KnownOutcome(
                    id="MEMBER_NOT_FOUND",
                    condition="Search returns zero results",
                    business_meaning="The member number does not exist",
                    caller_action="return",
                    page_text_pattern="No records found",
                ),
            ],
        )
        mock = MockSurface(
            observations=[_obs([_node("e1", "paragraph", "No records found")])]
        )
        result = replay(art, mock, {"key": "0000"}, wait_timeout=0.1)

        assert isinstance(result, ReplayBusinessOutcome)
        assert result.message == "The member number does not exist"
        assert result.condition == "Search returns zero results"
        assert result.message != result.condition


# --------------------------------------------------------------------------- #
# The guarded_write tier on the replay path
# --------------------------------------------------------------------------- #


def _write_artifact_and_obs():
    """A one-step artifact whose only action is a guarded write.

    "Apply" is what `control/policy.py` classifies as `guarded_write`, so
    this is the smallest thing that reaches the tier at all.
    """
    from control.approval import content_hash

    art = CapabilityArtifact(
        capability_id="test.write",
        version="1.0",
        description="Test write",
        surface_id="test",
        created_from=CreatedFrom(
            run_id="test", model="test",
            steps_in_transcript=1, wall_seconds=1.0,
        ),
        inputs=[],
        outputs=[],
        steps=[
            Step(
                step_id="click_apply", ordinal=1,
                intent='Click button "Apply"', action="click",
                risk="guarded_write",
                target=Locator(
                    strategy="ax",
                    frame=FrameRef(match="exact", value="main"),
                    role="button", name="Apply",
                ),
            ),
        ],
        known_outcomes=[],
        checkpoint=Checkpoint(
            description="applied", evidence_refs=[], proposed_by="compiler",
        ),
        escalation_policy=EscalationPolicy(
            on_stuck="x", on_unknown_state="x", on_blocked="x",
        ),
        approval=Approval(
            status="approved", approved_by="D. PARK",
            approved_at="2026-01-01T00:00:00+00:00",
            risk_class="guarded_write",
        ),
        meta=Meta(schema_version="1.0", verified_runs=2),
    )
    # An approved artifact needs a hash that matches, or the pre-replay
    # check refuses before any of this is exercised.
    art.approval.content_hash = content_hash(art)
    obs = _obs([_node("e1", "button", "Apply")])
    return art, obs


class TestGuardedWriteReachesThePolicyTier:
    """`policy_check(artifact_approved=...)` was never passed by the engine.

    The parameter defaulted to False on every call, so the `guarded_write`
    tier was unreachable in REPLAY for any artifact, however approved.  Five
    `safe` steps never touch that branch, which is why it survived until the
    first write capability existed.
    """

    def test_an_approved_artifact_may_complete_a_guarded_write(self):
        art, obs = _write_artifact_and_obs()
        mock = MockSurface(observations=[obs])

        result = replay(art, mock, {}, policy_mode=Mode.REPLAY,
                        wait_timeout=0.1)

        assert isinstance(result, ReplaySuccess), (
            f"approved artifact was refused: {getattr(result, 'error', result)}"
        )
        assert [s.step_id for s in result.steps] == ["click_apply"]

    def test_a_draft_is_refused_at_the_step_by_the_policy_tier(self):
        """Refused at `click_apply`, not at `(pre-replay)`.

        That is deliberate, and it is worth pinning because the obvious
        expectation is the other one.  `check_approval()` passes a draft —
        it enforces hash validity only on artifacts *claiming* approval, so
        it is a tamper check, not an authorisation.  What refuses a draft's
        write is the `guarded_write` tier in `control/policy.py`, one layer
        down.

        The security property holds either way: a draft cannot write.  Only
        the location of the refusal differs, and this test says which.
        """
        art, obs = _write_artifact_and_obs()
        art.approval.status = "draft"
        mock = MockSurface(observations=[obs])

        result = replay(art, mock, {}, policy_mode=Mode.REPLAY,
                        wait_timeout=0.1)

        assert isinstance(result, ReplayFailure)
        assert result.step_id == "click_apply"
        assert "guarded write" in result.error
        assert "requires an approved artifact" in result.error

    def test_a_draft_may_still_replay_its_safe_steps(self):
        """The layering, stated: approval gates *writes*, not execution.

        A draft under REPLAY runs safe steps and is refused only where it
        would change something. If that ever becomes undesirable, the change
        belongs in the pre-replay gate, not in the policy tier.
        """
        art = _make_artifact()          # five safe steps, no writes
        art.approval.status = "draft"
        mock = MockSurface(
            observations=[OBS_MENU, OBS_TABLE], read_values={"e22": "412.09"}
        )

        result = replay(art, mock, {"key": "0000"}, policy_mode=Mode.REPLAY,
                        wait_timeout=0.1)
        assert isinstance(result, ReplaySuccess)
        assert result.outputs["balance"] == "412.09"

    def test_a_revoked_artifact_cannot_write(self):
        """Revoked passes `check_approval` too, and is stopped by the same
        tier. Revocation is meaningful because `artifact_approved` reads the
        status, not the hash."""
        art, obs = _write_artifact_and_obs()
        art.approval.status = "revoked"
        art.approval.content_hash = None
        mock = MockSurface(observations=[obs])

        result = replay(art, mock, {}, policy_mode=Mode.REPLAY,
                        wait_timeout=0.1)
        assert isinstance(result, ReplayFailure)
        assert "guarded write" in result.error

    def test_discovery_mode_allows_the_write_regardless(self):
        """Verification runs under DISCOVERY and depends on this."""
        art, obs = _write_artifact_and_obs()
        art.approval.status = "draft"
        mock = MockSurface(observations=[obs])

        result = replay(art, mock, {}, policy_mode=Mode.DISCOVERY,
                        wait_timeout=0.1)
        assert isinstance(result, ReplaySuccess)

    def test_resume_still_permits_the_write(self):
        """`resume()` reuses the same `_ReplayState`, so the flag travels
        with it — an implementation detail today, a regression tomorrow."""
        from replay.engine import resume as engine_resume

        art, obs = _write_artifact_and_obs()
        art.recoverables = [
            Recoverable(
                trigger="session_expired", max_retries=0, strategy="escalate",
                trigger_pattern="Your session has expired",
                recovery_action="escalate",
            ),
        ]
        art.approval.content_hash = None
        art.approval.status = "approved"
        from control.approval import content_hash
        art.approval.content_hash = content_hash(art)

        expired = _obs([_node("e1", "paragraph", "Your session has expired.")])
        mock = MockSurface(observations=[expired])
        escalated = replay(art, mock, {}, policy_mode=Mode.REPLAY,
                           wait_timeout=0.1)
        assert isinstance(escalated, ReplayEscalated)

        mock._observations = [obs]
        mock._state = 0
        resumed = engine_resume(escalated, mock)
        assert isinstance(resumed, ReplaySuccess), (
            f"resume refused the write: {getattr(resumed, 'error', resumed)}"
        )


class TestVerificationProvesPortabilityNotJustDeterminism:
    """N runs with identical parameters is a determinism check wearing a
    portability check's clothes.

    Two artifacts passed the old gate while being valid only for the record
    they were recorded against — `card.lock` keyed a row by a card id
    containing the member number, `update_address` waited for a cell named
    after the city. Both replayed perfectly, twice, with the same inputs.
    """

    def _factory(self, read_values=None):
        rv = read_values or {"e22": "412.09"}
        def make():
            return MockSurface(
                observations=[OBS_MENU, OBS_TABLE], read_values=dict(rv),
            )
        return make

    def test_one_parameter_set_is_refused(self):
        art = _make_artifact("AVAILABLE")
        vr = verify_artifact(
            art, self._factory(), {"key": "0000"},
            {"balance": "412.09"}, n=2, wait_timeout=0.1,
        )
        assert not vr.passed
        assert "second parameter set" in (vr.failure_summary or "")
        assert art.meta.verified_runs is None, "nothing was stamped"

    def test_a_duplicate_second_set_is_refused(self):
        """The trap the fix could have walked into: `InputParam.example`
        holds the discovery run's own values, so using it as set two would
        verify the same values twice and report a check that never ran."""
        art = _make_artifact("AVAILABLE")
        vr = verify_artifact(
            art, self._factory(), {"key": "0000"},
            {"balance": "412.09"}, n=1, wait_timeout=0.1,
            also=[{"key": "0000"}],
        )
        assert not vr.passed
        assert "identical to the first" in (vr.failure_summary or "")

    def test_outputs_are_not_compared_across_sets(self):
        """Different records give different answers. That is the point —
        a capability returning the same balance for two members would be
        the bug, not the proof."""
        art = _make_artifact("AVAILABLE")
        vr = verify_artifact(
            art, self._factory(), {"key": "0000"},
            {"balance": "412.09"}, n=1, wait_timeout=0.1,
            also=[{"key": "0070"}],
        )
        assert vr.passed

    def test_a_second_set_that_does_not_replay_fails(self):
        """The whole point: a capability that works for one record only."""
        art = _make_artifact("AVAILABLE")
        calls = {"n": 0}

        def factory():
            calls["n"] += 1
            if calls["n"] <= 1:
                return MockSurface(observations=[OBS_MENU, OBS_TABLE],
                                   read_values={"e22": "412.09"})
            # The second record's page does not have the row.
            return MockSurface(observations=[OBS_MENU, OBS_MENU])

        vr = verify_artifact(
            art, factory, {"key": "0000"},
            {"balance": "412.09"}, n=1, wait_timeout=0.1,
            also=[{"key": "0070"}],
        )
        assert not vr.passed
        assert "did not replay" in (vr.failure_summary or "")
        assert "not for this one" in (vr.failure_summary or "")

    def test_a_capability_with_no_inputs_is_exempt(self):
        """One possible parameter set, so the requirement is vacuous."""
        art = _make_artifact("AVAILABLE")
        art.inputs = []
        vr = verify_artifact(
            art, self._factory(), {"key": "0000"},
            {"balance": "412.09"}, n=1, wait_timeout=0.1,
        )
        assert vr.passed

    def test_verified_runs_counts_every_run_across_sets(self):
        art = _make_artifact("AVAILABLE")
        vr = verify_artifact(
            art, self._factory(), {"key": "0000"},
            {"balance": "412.09"}, n=2, wait_timeout=0.1,
            also=[{"key": "0070"}, {"key": "0110"}],
        )
        assert vr.passed
        assert art.meta.verified_runs == 4      # 2 + 1 + 1
