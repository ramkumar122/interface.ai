"""Plain-English rendering of a capability's steps.

The single most important rendering in the console.  A risk reviewer who
will never read `artifacts/schema.py` has to be able to judge, from this
text alone, whether a locator depends on something that changes between
records.  `{"strategy": "ax_relative", "row_key": "0000"}` does not let
them do that; "the row where the first cell is exactly 0000" does.

Nothing here interprets.  `intent` and every rejected locator's `reason`
print verbatim — the renderer describes locators, it never rewrites the
author's prose.  A sentence that reads well but says something the
artifact does not is the failure mode this module exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass

from artifacts.schema import (
    CapabilityArtifact,
    FrameRef,
    Locator,
    RejectedLocator,
    Step,
)


# --------------------------------------------------------------------------- #
# Frame
# --------------------------------------------------------------------------- #


def describe_frame(frame: FrameRef) -> str | None:
    """Where the element lives, or ``None`` for the main frame.

    The main frame is the default and naming it adds noise to every step.
    A named iframe is load-bearing — the shares table lives in one — so it
    is said out loud, with the ellipsis marking that the recorded prefix is
    deliberately shorter than the runtime name (which carries the member
    number).
    """
    if frame.match == "exact":
        if frame.value == "main":
            return None
        return f'in the frame "{frame.value}"'
    # role_name
    return f'in the frame titled "{frame.name_prefix}…"'


# --------------------------------------------------------------------------- #
# Locator — one renderer per strategy
# --------------------------------------------------------------------------- #


def _describe_ax(loc: Locator) -> list[str]:
    return [f'{loc.role} named "{loc.name}"']


def _describe_ax_scoped(loc: Locator) -> list[str]:
    assert loc.within is not None  # schema validator guarantees this
    return [
        f'{loc.role} named "{loc.name}", '
        f'inside the {loc.within.role} "{loc.within.name}"'
    ]


def _describe_ax_relative(loc: Locator) -> list[str]:
    """Three clauses, because a reviewer checks three separate things.

    Split across lines so "the row where the first cell is exactly 0000"
    can be read on its own — that clause is where a record-dependent
    locator would hide.
    """
    match = "prefixed by" if loc.row_match == "prefix" else "exactly"
    return [
        f'in table "{loc.table_name}",',
        f'the row where the first cell is {match} "{loc.row_key}",',
        f'the cell under the column header "{loc.column_header}"',
    ]


_STRATEGY_RENDERERS = {
    "ax": _describe_ax,
    "ax_scoped": _describe_ax_scoped,
    "ax_relative": _describe_ax_relative,
}


def describe_locator(loc: Locator) -> list[str]:
    """How to find the element, as clauses meant to be read one per line.

    Returns a list rather than a string so the template controls line
    breaks and the tests can pin each clause independently.
    """
    renderer = _STRATEGY_RENDERERS.get(loc.strategy)
    if renderer is None:
        # Unknown strategy: say so rather than rendering a confident lie.
        return [f"by an unrecognised strategy ({loc.strategy})"]
    clauses = renderer(loc)
    frame = describe_frame(loc.frame)
    if frame is not None:
        return [f"{frame},"] + clauses
    return clauses


def describe_locator_sentence(loc: Locator) -> str:
    """The clauses joined into one line, for compact contexts."""
    return " ".join(describe_locator(loc))


# --------------------------------------------------------------------------- #
# Value
# --------------------------------------------------------------------------- #


def describe_value(step: Step, artifact: CapabilityArtifact) -> str | None:
    """What the step operates on, or ``None`` when it operates on nothing.

    An output's declared type is shown because a reviewer checking
    "does this return money" should not have to cross-reference the
    outputs table.
    """
    v = step.value
    if v is None:
        return None
    if v.source == "input":
        return f"value from input: {v.input_name}"
    if v.source == "literal":
        return f'value: "{v.literal}"'
    # output — only valid for action="read", per the schema validator
    out_type = next(
        (o.type for o in artifact.outputs if o.name == v.output_name), "?"
    )
    return f"into output: {v.output_name} ({out_type})"


# --------------------------------------------------------------------------- #
# Rejected locators
# --------------------------------------------------------------------------- #


def describe_rejected(rej: RejectedLocator) -> str:
    """The headline of a rejected locator.  The reason prints separately,
    verbatim, because it is the compiler's justification and not ours."""
    return f'rejected: {rej.strategy} "{rej.value}"'


@dataclass(frozen=True)
class RejectedCount:
    """How many steps recorded a rejected locator, and how many could not.

    A reviewer needs to tell "nothing to reject" from "something was
    missed".  An empty `rejected` list across every step is the suspicious
    case; an empty one on a step whose target had no DOM id is not.
    """

    with_rejected: int
    without: int
    total: int

    @property
    def sentence(self) -> str:
        return (
            f"{self.with_rejected} of {self.total} steps recorded a "
            f"rejected locator ({self.without} had no DOM id to reject)"
        )


def count_rejected(artifact: CapabilityArtifact) -> RejectedCount:
    """Same counts `control.approval.review_checklist()` prints, phrased for
    this surface.  Both read `step.rejected`; only the sentence differs."""
    with_rejected = sum(1 for s in artifact.steps if s.rejected)
    total = len(artifact.steps)
    return RejectedCount(
        with_rejected=with_rejected,
        without=total - with_rejected,
        total=total,
    )


# --------------------------------------------------------------------------- #
# Step
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class DescribedStep:
    """One step, ready to render.  No template logic beyond iteration."""

    ordinal: int
    step_id: str
    intent: str
    action: str
    risk: str
    locator_clauses: list[str]
    value_clause: str | None
    # The compiler sets `review_required` on every literal — the schema's
    # marker for "a human should look at this value".  It was set and never
    # rendered, so `card.lock` shipped with "Suspected fraud" hardcoded and
    # the flag that said so went nowhere.
    value_needs_review: bool
    rejected: list[tuple[str, str]]  # (headline, verbatim reason)


def describe_step(step: Step, artifact: CapabilityArtifact) -> DescribedStep:
    return DescribedStep(
        ordinal=step.ordinal,
        step_id=step.step_id,
        intent=step.intent,
        action=step.action,
        risk=step.risk,
        locator_clauses=describe_locator(step.target),
        value_clause=describe_value(step, artifact),
        value_needs_review=bool(step.value and step.value.review_required),
        rejected=[(describe_rejected(r), r.reason) for r in step.rejected],
    )


def describe_steps(artifact: CapabilityArtifact) -> list[DescribedStep]:
    return [describe_step(s, artifact) for s in artifact.steps]


# --------------------------------------------------------------------------- #
# Plain-text rendering — what the tests pin, and what a terminal would show
# --------------------------------------------------------------------------- #


def render_step_text(step: Step, artifact: CapabilityArtifact) -> str:
    """The step as plain text, matching the console's visual layout.

    Kept beside the structured form so the sentence a test asserts is the
    same sentence the screen shows.  A renderer whose tests exercise a
    different code path than the UI is a renderer with no tests.
    """
    d = describe_step(step, artifact)
    lines = [f"{d.ordinal}. {d.intent}"]
    for i, clause in enumerate(d.locator_clauses):
        lines.append(f"   {'→ ' if i == 0 else '  '}{clause}")
    if d.value_clause:
        flag = "   ⚠ literal — review this" if d.value_needs_review else ""
        lines.append(f"   → {d.value_clause}{flag}")
    for headline, reason in d.rejected:
        lines.append(f"   ✕ {headline}")
        lines.append(f"     ({reason})")
    return "\n".join(lines)
