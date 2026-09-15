"""Intervention requests and instruction templates.

An intervention request carries enough context for an operator to understand
what happened and what to do — without knowing anything about artifact schemas
or locator strategies.

Instructions are concrete per-step, derived from the step's ``intent`` and the
artifact's inputs. "Navigate to the Member Inquiry search screen for member
100101" is actionable. "Navigate back to the screen where the task was running"
is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class InterventionRequest:
    """What the engine sends when it needs a human.

    All fields are JSON-serialisable (``screenshot_path`` is stored as
    a string, not a ``Path``) so the console can serialise and transmit
    the request without a custom encoder.
    """

    run_id: str
    capability_id: str
    step_id: str
    step_intent: str
    reason: str
    instructions: str
    screenshot_path: str | None  # string, not Path — JSON-safe
    current_url: str
    created_at: str


@dataclass
class InterventionRecord:
    """Full lifecycle of one intervention, written to evidence."""

    request: InterventionRequest
    resumed_at: str | None = None
    resume_validated: bool = False
    total_human_seconds: float = 0.0
    outcome: str = "pending"  # "resumed" | "abandoned" | "timed_out"


# ---------------------------------------------------------------------------
# Instruction templates — keyed by detector_id
# ---------------------------------------------------------------------------

_ESCALATION_TEMPLATES: dict[str, str] = {
    "session_expired": (
        "The session has expired. Please sign in again as the current operator."
    ),
    "unknown_state": (
        "The page is in an unexpected state. Please review and navigate to "
        "the expected screen."
    ),
}

_DEFAULT_TEMPLATE = (
    "The automation cannot proceed. Please review the current state "
    "and take the appropriate action."
)


def build_instructions(
    step_intent: str,
    inputs: dict[str, str],
    detector_id: str,
) -> str:
    """Build concrete, per-step instructions for the human operator."""
    base = _ESCALATION_TEMPLATES.get(detector_id, _DEFAULT_TEMPLATE)
    # Describe what the step was trying to do
    context = f" Then navigate to the correct screen (step: '{step_intent}')."
    # Add input context if available
    if inputs:
        params = ", ".join(f"{k}={v}" for k, v in inputs.items())
        context += f" Parameters: {params}."
    return base + context


def build_intervention_request(
    *,
    run_id: str,
    capability_id: str,
    step_id: str,
    step_intent: str,
    reason: str,
    inputs: dict[str, str],
    detector_id: str,
    screenshot_path: Path | None,
    current_url: str,
) -> InterventionRequest:
    """Construct a fully-populated intervention request."""
    return InterventionRequest(
        run_id=run_id,
        capability_id=capability_id,
        step_id=step_id,
        step_intent=step_intent,
        reason=reason,
        instructions=build_instructions(step_intent, inputs, detector_id),
        screenshot_path=screenshot_path,
        current_url=current_url,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
