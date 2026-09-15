"""Three-way result contract for replay.

Never an exception across the module boundary.  Callers pattern-match on
``status``: ``"success"`` | ``"business_outcome"`` | ``"failure"``.

``DriftSignal`` records which locator rung matched on every step.  A primary
that misses while a fallback succeeds is a run that worked and a system
that's degrading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Union


@dataclass
class RecoveryTrace:
    """One recovery attempt during a step."""

    detector_id: str
    recovery_action: str
    attempt: int


@dataclass
class StepTrace:
    """One successfully completed step — timing and resolution evidence."""

    step_id: str
    ref_used: str | None = None
    locator_strategy: str | None = None
    elapsed_ms: float = 0
    recoveries: list[RecoveryTrace] = field(default_factory=list)


@dataclass
class DriftSignal:
    """Primary locator missed; a fallback rung resolved the element."""

    step_id: str
    primary_strategy: str
    matched_strategy: str
    detail: str


@dataclass
class ReplaySuccess:
    """All steps completed, all outputs filled."""

    outputs: dict[str, str] = field(default_factory=dict)
    drift_signals: list[DriftSignal] = field(default_factory=list)
    steps: list[StepTrace] = field(default_factory=list)
    status: Literal["success"] = "success"


@dataclass
class ReplayBusinessOutcome:
    """A known, legitimate non-success result the caller must handle."""

    outcome_id: str = ""
    condition: str = ""
    message: str = ""
    caller_action: str = ""
    at_step: str = ""
    steps: list[StepTrace] = field(default_factory=list)
    status: Literal["business_outcome"] = "business_outcome"


@dataclass
class ReplayFailure:
    """Something broke — locator, wait, policy, or unexpected exception."""

    step_id: str = ""
    step_intent: str = ""
    expected: str = ""
    observed: str = ""
    locator_rung: str = ""
    error: str = ""
    evidence_paths: list[str] = field(default_factory=list)
    steps: list[StepTrace] = field(default_factory=list)
    status: Literal["failure"] = "failure"


@dataclass
class ReplayEscalated:
    """Engine cannot proceed; a human must intervene.

    The caller handles the intervention and calls ``engine.resume()``,
    passing this result back.  ``resume_token`` is for logging only —
    the engine holds the actual state internally in ``_engine_state``.
    The token is opaque to the caller.
    """

    request: object  # InterventionRequest (avoid circular import)
    resume_token: str = ""
    steps: list[StepTrace] = field(default_factory=list)
    _engine_state: object = field(default=None, repr=False)
    status: Literal["escalated"] = "escalated"


ReplayResult = Union[ReplaySuccess, ReplayBusinessOutcome, ReplayFailure, ReplayEscalated]
