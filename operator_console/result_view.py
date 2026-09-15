"""Turning a `ReplayResult` into what a screen shows.

The console's apparent "conversational" behaviour is entirely this table:

    status            caller_action                rendering
    success           —                            outputs
    business_outcome  return                       the answer, terminal
    business_outcome  retry_with_different_input   a follow-up field
    business_outcome  escalate                     the intervention panel
    failure           —                            the diagnostic
    escalated         —                            the intervention panel

There is no intent parsing anywhere, and nothing here inspects prose.  The
follow-up question is a `KnownOutcome.business_meaning` looked up by
`outcome_id` — the artifact's own words, rendered as a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from artifacts.schema import CapabilityArtifact
from control.redact import redact


# --------------------------------------------------------------------------- #
# Step log
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StepRow:
    """One completed step, joined to the artifact for its intent."""

    ordinal: int
    step_id: str
    intent: str
    rung: str
    elapsed_ms: float
    recoveries: list[tuple[str, str, int]]  # (detector_id, action, attempt)


def step_rows(result: Any, artifact: CapabilityArtifact) -> list[StepRow]:
    """The step log.  Every result type carries `steps`, so this renders the
    same on a success and on a failure four steps in."""
    by_id = {s.step_id: s for s in artifact.steps}
    rows: list[StepRow] = []
    for trace in getattr(result, "steps", []) or []:
        step = by_id.get(trace.step_id)
        rows.append(
            StepRow(
                ordinal=step.ordinal if step else len(rows) + 1,
                step_id=trace.step_id,
                intent=step.intent if step else "(not in artifact)",
                rung=trace.locator_strategy or "—",
                elapsed_ms=round(trace.elapsed_ms, 1),
                recoveries=[
                    (r.detector_id, r.recovery_action, r.attempt)
                    for r in trace.recoveries
                ],
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# Drift
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DriftPanel:
    signals: list[Any]

    @property
    def note(self) -> str:
        """Say what the empty list means, because it is always empty.

        `DriftSignal` is defined and plumbed and nothing constructs one:
        `locate()` dispatches on the declared strategy and raises if it
        misses, so there is no fallback to report (REPORT §7).  Rendering
        "no drift" without that context would read as a clean bill of
        health from a detector that does not exist.
        """
        if self.signals:
            return (
                "A fallback rung resolved where the primary missed. The run "
                "worked and the application moved."
            )
        return (
            "No drift signals — and none are possible today. `locate()` "
            "tries the strategy the artifact declares and fails if it "
            "misses; there is no fallback rung to report having used. The "
            "Rung column below is the real per-step record."
        )


# --------------------------------------------------------------------------- #
# The rendered result
# --------------------------------------------------------------------------- #


@dataclass
class ResultView:
    """What the template branches on.  One `panel` value per row of the table."""

    panel: str
    status: str
    steps: list[StepRow] = field(default_factory=list)
    outputs: list[tuple[str, str, str]] = field(default_factory=list)
    drift: DriftPanel | None = None

    # business_outcome
    outcome_id: str = ""
    message: str = ""
    detected_as: str = ""
    caller_action: str = ""
    at_step: str = ""
    follow_up_prompt: str = ""
    retry_input: str = ""
    retry_value: str = ""

    # failure
    failure: Any = None
    evidence_links: list[str] = field(default_factory=list)

    # escalated
    request: Any = None
    resume_token: str = ""


def build(
    result: Any,
    artifact: CapabilityArtifact,
    submitted_inputs: dict[str, str],
    evidence_root: str = "evidence",
) -> ResultView:
    """`ReplayResult` -> what the screen shows.  No branch invents anything."""
    rows = step_rows(result, artifact)
    status = result.status

    if status == "success":
        declared = {o.name: o.type for o in artifact.outputs}
        return ResultView(
            panel="success",
            status=status,
            steps=rows,
            outputs=[
                (name, redact(value), declared.get(name, "?"))
                for name, value in result.outputs.items()
            ],
            drift=DriftPanel(signals=list(result.drift_signals)),
        )

    if status == "business_outcome":
        return _business_outcome(result, artifact, submitted_inputs, rows)

    if status == "failure":
        return ResultView(
            panel="failure",
            status=status,
            steps=rows,
            failure=result,
            evidence_links=[
                _relative(p, evidence_root) for p in result.evidence_paths
            ],
        )

    # escalated
    return ResultView(
        panel="intervention",
        status=status,
        steps=rows,
        request=result.request,
        resume_token=result.resume_token,
    )


def _business_outcome(
    result: Any,
    artifact: CapabilityArtifact,
    submitted_inputs: dict[str, str],
    rows: list[StepRow],
) -> ResultView:
    """Three renderings, chosen by `caller_action` and nothing else."""
    declared = next(
        (ko for ko in artifact.known_outcomes if ko.id == result.outcome_id),
        None,
    )

    view = ResultView(
        panel="outcome_return",
        status=result.status,
        steps=rows,
        outcome_id=result.outcome_id,
        # The artifact's `business_meaning` is the answer.  `detect.py` now
        # reports the same string on both detection paths, so `result.message`
        # would usually do — but an artifact is the console's source of truth
        # and it is the only one that still works when a result arrives from
        # an engine version that disagrees.  The fallback covers an outcome
        # the artifact does not declare.
        message=redact(
            declared.business_meaning if declared else result.message
        ),
        # What was detected, as distinct from what it means.
        detected_as=redact(result.condition or result.message),
        caller_action=result.caller_action,
        at_step=result.at_step,
    )

    if result.caller_action == "escalate":
        view.panel = "outcome_escalate"
        return view

    if result.caller_action == "retry_with_different_input":
        view.panel = "outcome_retry"
        # The prompt is the artifact's own `business_meaning`.  Nothing here
        # parses the message or infers what to ask — if the artifact does
        # not say what the outcome means, the console says the outcome id.
        view.follow_up_prompt = (
            declared.business_meaning if declared else result.message
        )
        # Pre-fill the field the caller would change.  A single-input
        # capability makes that unambiguous; with several, the first is a
        # guess, so the form re-offers all of them instead.
        if len(artifact.inputs) == 1:
            name = artifact.inputs[0].name
            view.retry_input = name
            view.retry_value = submitted_inputs.get(name, "")
        return view

    return view


def _relative(path: str, evidence_root: str) -> str:
    """Evidence paths are written relative to CWD; the route serves them
    relative to `evidence/`."""
    p = str(path)
    marker = f"{evidence_root}/"
    idx = p.find(marker)
    return p[idx + len(marker):] if idx >= 0 else p
