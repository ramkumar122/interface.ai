"""Detectors: declarative condition matchers run before every step.

Built from the artifact's ``known_outcomes`` (page-level business signals) and
``recoverables`` (runtime conditions with bounded recovery).  Detection order:
business outcomes first (terminal), then hard failures, then recoverables.
Getting it backwards means retrying a "member not found" three times.

Two detection points per step:
  1. Pre-locate — catches conditions present when the step begins.
  2. Post-wait (on timeout only) — catches conditions that appeared after the
     action, turning "wait timed out" into "app_error at step 3."
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from artifacts.schema import CapabilityArtifact
from replay.locate import LocateError, locate, match_frame, _matching_frames
from surface.base import Observation


# --------------------------------------------------------------------------- #
# Detector types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DetectorHit:
    """A detector fired. Carries enough context for the engine to act.

    ``message`` is what the caller is told.  For a business outcome that is
    the artifact's ``business_meaning`` — the same string on both detection
    paths, so a caller never has to know which one fired.  For a hard
    failure or a recoverable there is no business meaning to carry (a
    ``Recoverable`` has no such field, and "the app 500'd" is not a business
    result), so it stays the detector id and the human sentence lives in
    ``InterventionRequest.instructions``.
    """

    detector_id: str
    kind: Literal["business_outcome", "hard_failure", "recoverable"]
    message: str
    condition: str | None = None
    outcome_id: str | None = None
    caller_action: str | None = None
    recovery_action: str | None = None
    recovery_target_role: str | None = None
    recovery_target_name: str | None = None
    max_attempts: int = 0


@dataclass
class Detector:
    """One condition the engine checks against an observation."""

    id: str
    kind: Literal["business_outcome", "hard_failure", "recoverable"]
    page_text_pattern: re.Pattern | None = None
    page_role_match: tuple[str, str] | None = None
    # The artifact's own words for this outcome, carried so a page-text
    # match reports the same thing a reclassification does.
    business_meaning: str | None = None
    condition: str | None = None
    outcome_id: str | None = None
    caller_action: str | None = None
    recovery_action: str | None = None
    recovery_target_role: str | None = None
    recovery_target_name: str | None = None
    max_attempts: int = 0


# --------------------------------------------------------------------------- #
# LocateError reclassification — positive evidence, not substring matching
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OutcomeReclassification:
    """Reclassify a LocateError when positive evidence confirms a business outcome.

    ``requires_present`` must ALL be found in the observation.
    ``requires_absent`` must ALL be NOT found.
    Only when both hold is the LocateError reclassified — if the table itself
    is missing (iframe didn't load), requires_present fails and the error
    stays a failure.
    """

    outcome_id: str
    at_step: str | None
    caller_action: str
    condition: str
    business_meaning: str
    requires_present: list[dict]
    requires_absent: list[dict]


def _check_presence(check: dict, obs: Observation) -> bool:
    """True if the element described by *check* exists in the observation."""
    role = check.get("role", "")
    name = check.get("name", "")
    frame_spec = check.get("frame")
    for node in obs.nodes:
        if node.role == role and node.name == name:
            if frame_spec is None:
                return True
            from artifacts.schema import FrameRef
            fr = FrameRef.model_validate(frame_spec)
            if match_frame(fr, node.frame):
                return True
    return False


def _check_absence(check: dict, obs: Observation) -> bool:
    """True if the described element is NOT found (confirming its absence)."""
    strategy = check.get("strategy")
    if strategy == "ax_relative":
        table_name = check.get("table_name", "")
        row_key = check.get("row_key", "")
        for node in obs.nodes:
            if node.role == "table" and node.name == table_name:
                frame_spec = check.get("frame")
                frames = list(obs.frames.keys()) if not frame_spec else (
                    _matching_frames(
                        __import__("artifacts.schema", fromlist=["FrameRef"]).FrameRef.model_validate(frame_spec),
                        obs,
                    )
                )
                from replay.locate import _parse_annotated, _find_named, _collect
                for fp in frames:
                    tree = _parse_annotated(obs.frames.get(fp, ""))
                    tbl = _find_named(tree, "table", table_name)
                    if tbl is None:
                        continue
                    rows = _collect(tbl.children, "row")
                    for row in rows:
                        cells = [c for c in row.children if c.role == "cell"]
                        if cells:
                            first = cells[0].name or cells[0].value or ""
                            if first == row_key:
                                return False  # row IS present → absence not confirmed
                return True  # row not found → absence confirmed
    # Default: check by role+name not present
    role = check.get("role", "")
    name = check.get("name", "")
    for node in obs.nodes:
        if node.role == role and node.name == name:
            return False
    return True


def try_reclassify(
    error: LocateError,
    step_id: str,
    obs: Observation,
    reclassifications: list[OutcomeReclassification],
) -> DetectorHit | None:
    """Attempt to reclassify a LocateError as a known business outcome.

    Returns a ``DetectorHit`` if positive evidence confirms the outcome,
    ``None`` if the error should stay a failure.
    """
    for rc in reclassifications:
        # Check step match
        if rc.at_step is not None:
            if rc.at_step != step_id and not (
                rc.at_step.endswith("*") and step_id.startswith(rc.at_step[:-1])
            ):
                continue

        # Positive evidence: all requires_present must exist
        if not all(_check_presence(p, obs) for p in rc.requires_present):
            continue

        # Negative evidence: all requires_absent must be absent
        if not all(_check_absence(a, obs) for a in rc.requires_absent):
            continue

        return DetectorHit(
            detector_id=rc.outcome_id,
            kind="business_outcome",
            message=rc.business_meaning,
            condition=rc.condition,
            outcome_id=rc.outcome_id,
            caller_action=rc.caller_action,
        )

    return None


# --------------------------------------------------------------------------- #
# Build detectors from an artifact
# --------------------------------------------------------------------------- #


def build_detectors(artifact: CapabilityArtifact) -> list[Detector]:
    """Construct detector list from artifact's known_outcomes and recoverables.

    Ordering: business outcomes first, then hard failures, then recoverables.
    """
    detectors: list[Detector] = []

    # Business outcomes with page_text_pattern
    for ko in artifact.known_outcomes:
        pat = getattr(ko, "page_text_pattern", None)
        if pat:
            detectors.append(Detector(
                id=ko.id,
                kind="business_outcome",
                page_text_pattern=re.compile(pat),
                business_meaning=ko.business_meaning,
                condition=ko.condition,
                outcome_id=ko.id,
                caller_action=ko.caller_action,
            ))

    # Hard failures (built-in — these are always checked)
    detectors.append(Detector(
        id="APP_ERROR",
        kind="hard_failure",
        page_text_pattern=re.compile(r"CoreDesk Application Error"),
    ))

    # Recoverables from artifact
    for rec in artifact.recoverables:
        pat = getattr(rec, "trigger_pattern", None)
        role = getattr(rec, "trigger_role", None)
        name = getattr(rec, "trigger_name", None)
        action = getattr(rec, "recovery_action", None) or rec.strategy
        t_role = getattr(rec, "recovery_target_role", None)
        t_name = getattr(rec, "recovery_target_name", None)
        d = Detector(
            id=rec.trigger,
            kind="recoverable",
            page_text_pattern=re.compile(pat) if pat else None,
            page_role_match=(role, name) if role and name else None,
            recovery_action=action,
            recovery_target_role=t_role,
            recovery_target_name=t_name,
            max_attempts=rec.max_retries,
        )
        detectors.append(d)

    return detectors


def build_reclassifications(artifact: CapabilityArtifact) -> list[OutcomeReclassification]:
    """Build reclassification rules from known_outcomes that have requires_present/absent."""
    rcs: list[OutcomeReclassification] = []
    for ko in artifact.known_outcomes:
        rp = getattr(ko, "requires_present", None)
        ra = getattr(ko, "requires_absent", None)
        if rp is not None or ra is not None:
            # Serialize Pydantic models to dicts for the checker functions
            rp_dicts = [p.model_dump() if hasattr(p, "model_dump") else p for p in (rp or [])]
            ra_dicts = [a.model_dump() if hasattr(a, "model_dump") else a for a in (ra or [])]
            rcs.append(OutcomeReclassification(
                outcome_id=ko.id,
                at_step=getattr(ko, "at_step", None),
                caller_action=ko.caller_action,
                condition=ko.condition,
                business_meaning=ko.business_meaning,
                requires_present=rp_dicts,
                requires_absent=ra_dicts,
            ))
    return rcs


# --------------------------------------------------------------------------- #
# Run detectors against an observation
# --------------------------------------------------------------------------- #


def _obs_text(obs: Observation) -> str:
    """Concatenate all node names and values for text-pattern matching."""
    parts: list[str] = []
    for n in obs.nodes:
        if n.name:
            parts.append(n.name)
        if n.value:
            parts.append(n.value)
    # Also include the raw YAML text for each frame
    for text in obs.frames.values():
        parts.append(text)
    return "\n".join(parts)


def run_detectors(
    detectors: list[Detector],
    obs: Observation,
) -> DetectorHit | None:
    """Run all detectors against an observation. Returns the first hit or None.

    Order matters: business outcomes checked first, then hard failures, then
    recoverables.  The detector list is pre-sorted by ``build_detectors``.
    """
    text = _obs_text(obs)

    for d in detectors:
        matched = False

        if d.page_text_pattern and d.page_text_pattern.search(text):
            matched = True
        if d.page_role_match:
            role, name = d.page_role_match
            for n in obs.nodes:
                if n.role == role and n.name == name:
                    matched = True
                    break

        if matched:
            return DetectorHit(
                detector_id=d.id,
                kind=d.kind,
                # A business outcome reports the artifact's words; anything
                # else has none, so it reports what fired.
                message=d.business_meaning or f"detected: {d.id}",
                condition=d.condition,
                outcome_id=d.outcome_id,
                caller_action=d.caller_action,
                recovery_action=d.recovery_action,
                recovery_target_role=d.recovery_target_role,
                recovery_target_name=d.recovery_target_name,
                max_attempts=d.max_attempts,
            )

    return None
