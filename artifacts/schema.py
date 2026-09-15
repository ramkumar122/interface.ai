"""Capability artifact schema — Pydantic v2.

The artifact is the deliverable: a typed, versioned, reviewable description of
a single automated capability. It must be readable by a bank's risk reviewer
who will never see this code.

Round-trips to JSON, validates on load, and answers five questions from the
JSON alone: what does this do, what does it need, what does it return, what
can go wrong, and what happens if it can't finish.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


# --------------------------------------------------------------------------- #
# Frame reference — structured, never a bare string with magic wildcards
# --------------------------------------------------------------------------- #


class FrameRef(BaseModel):
    """How to find the frame a target lives in.

    ``match="exact"`` with ``value="main"`` for the main frame.
    ``match="role_name"`` with ``role="iframe"`` and ``name_prefix="Share
    accounts"`` for a named iframe whose accessible name starts with a known
    prefix (the suffix may contain a member number or other runtime value).
    """

    match: Literal["exact", "role_name"]
    value: str | None = None
    role: str | None = None
    name_prefix: str | None = None

    @model_validator(mode="after")
    def _validate_match(self) -> "FrameRef":
        if self.match == "exact" and not self.value:
            raise ValueError("match='exact' requires 'value'")
        if self.match == "role_name":
            if not self.role:
                raise ValueError("match='role_name' requires 'role'")
            if not self.name_prefix:
                raise ValueError("match='role_name' requires 'name_prefix'")
        return self


# --------------------------------------------------------------------------- #
# Locator — strategy-tagged, never a selector string
# --------------------------------------------------------------------------- #


class NamedRegionRef(BaseModel):
    """A landmark/region/table ancestor used to scope a locator."""

    role: str
    name: str


class Locator(BaseModel):
    """How to find an element. Strategy-tagged so a future ``field_grid``
    strategy for a terminal surface is additive, not a rewrite.

    ``ax`` — name is unique in frame.
    ``ax_scoped`` — unique inside a named region.
    ``ax_relative`` — table cell by column header and row.

    For ``ax_relative``, ``name`` is typically ``None`` because the cell's
    accessible name IS the runtime output value (e.g. ``"12,845.50"``).
    The cell is located by its position in the table structure, not by name.
    """

    strategy: Literal["ax", "ax_scoped", "ax_relative"]
    frame: FrameRef
    role: str
    name: str | None = None
    within: NamedRegionRef | None = None
    table_name: str | None = None
    column_header: str | None = None
    row_key: str | None = None
    row_match: Literal["exact", "prefix"] | None = None

    @model_validator(mode="after")
    def _validate_strategy(self) -> "Locator":
        if self.strategy == "ax" and not self.name:
            raise ValueError("strategy='ax' requires 'name'")
        if self.strategy == "ax_scoped":
            if not self.name:
                raise ValueError("strategy='ax_scoped' requires 'name'")
            if not self.within:
                raise ValueError("strategy='ax_scoped' requires 'within'")
        if self.strategy == "ax_relative":
            if not self.table_name:
                raise ValueError("strategy='ax_relative' requires 'table_name'")
            if not self.column_header:
                raise ValueError("strategy='ax_relative' requires 'column_header'")
            if not self.row_key:
                raise ValueError(
                    "strategy='ax_relative' requires 'row_key' — "
                    "'first row' is a positional assumption"
                )
        return self


# --------------------------------------------------------------------------- #
# Rejected locator — what was refused and why
# --------------------------------------------------------------------------- #


class RejectedLocator(BaseModel):
    """A locator strategy that was available but deliberately not used."""

    strategy: str
    value: str
    reason: str

    @field_validator("reason")
    @classmethod
    def _reason_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("reason must not be empty")
        return v


# --------------------------------------------------------------------------- #
# Value specification
# --------------------------------------------------------------------------- #


class ValueSpec(BaseModel):
    """What value a step operates on.

    Three sources: ``input`` (replay substitutes a runtime value), ``literal``
    (constant string), ``output`` (the step reads into a declared output —
    only valid for ``action="read"``).
    """

    source: Literal["input", "literal", "output"]
    input_name: str | None = None
    literal: str | None = None
    output_name: str | None = None
    review_required: bool = False

    @model_validator(mode="after")
    def _validate_source(self) -> "ValueSpec":
        if self.source == "input" and not self.input_name:
            raise ValueError("source='input' requires 'input_name'")
        if self.source == "literal" and self.literal is None:
            raise ValueError("source='literal' requires 'literal'")
        if self.source == "output" and not self.output_name:
            raise ValueError("source='output' requires 'output_name'")
        return self


# --------------------------------------------------------------------------- #
# Wait condition
# --------------------------------------------------------------------------- #


class WaitCondition(BaseModel):
    """What to wait for after a step completes, before observing."""

    frame: FrameRef
    role: str
    name: str


# --------------------------------------------------------------------------- #
# Step
# --------------------------------------------------------------------------- #


class Step(BaseModel):
    """One step in the capability. Ordered, with a stable ``step_id``."""

    step_id: str
    ordinal: int
    intent: str
    action: Literal["navigate", "click", "type", "select", "read"]
    risk: Literal["safe", "guarded_write", "irreversible"] = "safe"
    target: Locator
    value: ValueSpec | None = None
    wait_for: WaitCondition | None = None
    rejected: list[RejectedLocator] = []

    @field_validator("intent")
    @classmethod
    def _intent_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("intent must not be empty")
        return v

    @model_validator(mode="after")
    def _read_requires_output_value(self) -> "Step":
        """``read`` steps must have ``value.source == "output"``.

        This is the invariant: only ``read`` actions produce output values.
        """
        if self.action == "read":
            if not self.value or self.value.source != "output":
                raise ValueError(
                    "action='read' requires value with source='output'"
                )
        if self.value and self.value.source == "output" and self.action != "read":
            raise ValueError("source='output' is only valid for action='read'")
        return self


# --------------------------------------------------------------------------- #
# Known outcomes — non-success business results
# --------------------------------------------------------------------------- #


class PresenceCheck(BaseModel):
    """An element that MUST be present for a reclassification to hold."""

    role: str
    name: str
    frame: FrameRef | None = None


class AbsenceCheck(BaseModel):
    """An element whose absence confirms a business outcome.

    ``strategy="ax_relative"`` checks a specific table row.  The default
    checks by ``(role, name)`` not being present.
    """

    strategy: str | None = None
    role: str | None = None
    name: str | None = None
    table_name: str | None = None
    row_key: str | None = None


class KnownOutcome(BaseModel):
    """A non-success business outcome the caller must handle.

    Success is expressed by the result contract (``status="success"`` plus
    populated outputs), not here. ``caller_action`` tells the calling agent
    what to do, which a boolean ``recoverable`` cannot.

    Detection fields are optional: ``page_text_pattern`` for page-level text
    matching, ``requires_present``/``requires_absent`` for positive-evidence
    reclassification of LocateErrors.
    """

    id: str
    condition: str
    business_meaning: str
    caller_action: Literal["return", "escalate", "retry_with_different_input"]

    # Page-level text detection
    page_text_pattern: str | None = None

    # Positive-evidence reclassification (correction A)
    at_step: str | None = None
    requires_present: list[PresenceCheck] | None = None
    requires_absent: list[AbsenceCheck] | None = None

    @field_validator("id")
    @classmethod
    def _no_success(cls, v: str) -> str:
        if v.upper() == "SUCCESS":
            raise ValueError(
                "SUCCESS is not a known_outcome; success is expressed by the "
                "result contract (status='success' + populated outputs)"
            )
        return v


# --------------------------------------------------------------------------- #
# Checkpoint
# --------------------------------------------------------------------------- #


class Checkpoint(BaseModel):
    description: str
    evidence_refs: list[str]
    proposed_by: Literal["model", "compiler", "human"]


# --------------------------------------------------------------------------- #
# Recoverable conditions
# --------------------------------------------------------------------------- #


class Recoverable(BaseModel):
    """A runtime condition the engine handles without escalating.

    ``trigger`` and ``strategy`` are human-readable (backward-compatible).
    The ``trigger_*`` and ``recovery_action`` fields are machine-readable
    for the detector engine.
    """

    trigger: str
    max_retries: int
    strategy: str

    # Machine-readable detection (optional, new)
    trigger_pattern: str | None = None
    trigger_role: str | None = None
    trigger_name: str | None = None
    recovery_action: Literal["dismiss", "retry", "escalate"] | None = None
    # For dismiss: the element to click (role + name within the trigger scope)
    recovery_target_role: str | None = None
    recovery_target_name: str | None = None


# --------------------------------------------------------------------------- #
# Override — keyed by step_id, with from/to assertion
# --------------------------------------------------------------------------- #


class OverrideFieldPatch(BaseModel):
    """A single field patch: asserts what it expects to replace.

    If the base artifact doesn't contain ``from_value``, the override fails
    loudly instead of silently patching nothing. A silent no-op on Summit
    would mean reading the wrong column — the exact failure this project
    exists to prevent.
    """

    from_value: str
    to_value: str


class OverrideTargetPatch(BaseModel):
    """Patches to a step's target locator fields."""

    name: OverrideFieldPatch | None = None
    column_header: OverrideFieldPatch | None = None
    name_prefix: OverrideFieldPatch | None = None


class OverrideWaitPatch(BaseModel):
    """Patches to a step's wait_for fields (tenant-visible names)."""

    name: OverrideFieldPatch | None = None


class StepOverride(BaseModel):
    """Override for a single step, keyed by step_id in the parent dict."""

    target: OverrideTargetPatch | None = None
    wait_for: OverrideWaitPatch | None = None


class OverrideSet(BaseModel):
    """All overrides for one tenant."""

    steps: dict[str, StepOverride] = {}


# --------------------------------------------------------------------------- #
# Escalation policy — caller documentation, not engine-executable
# --------------------------------------------------------------------------- #


class EscalationPolicy(BaseModel):
    """What the calling agent should expect when the engine gives up.

    These are documentation for the caller, not executable by the engine.
    The engine's runtime behaviour lives in ``recoverables``.
    """

    on_stuck: str
    on_unknown_state: str
    on_blocked: str


# --------------------------------------------------------------------------- #
# Approval
# --------------------------------------------------------------------------- #


_RISK_ORDER = {"safe": 0, "guarded_write": 1, "irreversible": 2}


class Approval(BaseModel):
    status: Literal["draft", "approved", "revoked"]
    approved_by: str | None = None
    approved_at: str | None = None
    risk_class: Literal["safe", "guarded_write", "irreversible"]
    content_hash: str | None = None


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #


class CreatedFrom(BaseModel):
    run_id: str
    model: str
    steps_in_transcript: int
    wall_seconds: float


class Meta(BaseModel):
    schema_version: str
    compiler_version: str | None = None
    verified_runs: int | None = None


# --------------------------------------------------------------------------- #
# Input / Output parameters
# --------------------------------------------------------------------------- #


class InputParam(BaseModel):
    name: str
    type: str
    description: str
    example: str | None = None


class OutputParam(BaseModel):
    name: str
    type: str
    shape: str
    description: str


# --------------------------------------------------------------------------- #
# The artifact
# --------------------------------------------------------------------------- #


_VERSION_RE = re.compile(r"^\d+\.\d+$")


class CapabilityArtifact(BaseModel):
    """A complete capability artifact. The central deliverable."""

    capability_id: str
    version: str
    description: str
    surface_id: str
    created_from: CreatedFrom

    inputs: list[InputParam]
    outputs: list[OutputParam]

    steps: list[Step]

    known_outcomes: list[KnownOutcome]

    checkpoint: Checkpoint

    recoverables: list[Recoverable] = []

    overrides: dict[str, OverrideSet] = {}

    escalation_policy: EscalationPolicy

    approval: Approval

    meta: Meta

    @field_validator("version")
    @classmethod
    def _version_format(cls, v: str) -> str:
        if not _VERSION_RE.match(v):
            raise ValueError(f"version must be MAJOR.MINOR, got {v!r}")
        return v

    @model_validator(mode="after")
    def _step_ids_unique(self) -> "CapabilityArtifact":
        ids = [s.step_id for s in self.steps]
        if len(ids) != len(set(ids)):
            dupes = [sid for sid in ids if ids.count(sid) > 1]
            raise ValueError(f"duplicate step_id(s): {set(dupes)}")
        return self

    @model_validator(mode="after")
    def _override_step_ids_exist(self) -> "CapabilityArtifact":
        """Every step_id referenced in overrides must exist in steps."""
        step_ids = {s.step_id for s in self.steps}
        for tenant, oset in self.overrides.items():
            for sid in oset.steps:
                if sid not in step_ids:
                    raise ValueError(
                        f"override for tenant {tenant!r} references unknown "
                        f"step_id {sid!r}"
                    )
        return self

    def apply_overrides(self, tenant: str) -> "CapabilityArtifact":
        """Return a copy with the given tenant's overrides applied.

        Raises ``ValueError`` if a ``from_value`` assertion fails.
        """
        oset = self.overrides.get(tenant)
        if not oset:
            return self.model_copy(deep=True)

        data = self.model_dump()
        steps_by_id = {s["step_id"]: s for s in data["steps"]}

        for step_id, so in oset.steps.items():
            step = steps_by_id[step_id]
            if so.target:
                t = step["target"]
                if so.target.name:
                    _assert_and_patch(
                        t, "name", so.target.name, step_id, tenant
                    )
                if so.target.column_header:
                    _assert_and_patch(
                        t, "column_header", so.target.column_header,
                        step_id, tenant,
                    )
                if so.target.name_prefix:
                    f = t.get("frame", {})
                    _assert_and_patch(
                        f, "name_prefix", so.target.name_prefix,
                        step_id, tenant,
                    )
            if so.wait_for and so.wait_for.name:
                wf = step.get("wait_for")
                if wf is None:
                    raise ValueError(
                        f"override for tenant {tenant!r}, step {step_id!r}: "
                        f"wait_for patch but step has no wait_for"
                    )
                _assert_and_patch(
                    wf, "name", so.wait_for.name, step_id, tenant
                )

        return CapabilityArtifact.model_validate(data)

    def derived_risk_class(self) -> str:
        """The capability's risk class: max of its steps'.

        A capability containing one ``guarded_write`` step isn't ``safe``
        overall. The compiler sets each step's ``risk`` from the discovery
        run's ``verdict.risk``.
        """
        max_risk = 0
        for step in self.steps:
            max_risk = max(max_risk, _RISK_ORDER.get(step.risk, 0))
        for risk_name, order in _RISK_ORDER.items():
            if order == max_risk:
                return risk_name
        return "safe"


def _assert_and_patch(
    target: dict,
    field: str,
    patch: OverrideFieldPatch | dict,
    step_id: str,
    tenant: str,
) -> None:
    """Apply a from/to patch, failing loudly if from doesn't match."""
    if isinstance(patch, dict):
        from_val = patch.get("from_value") or patch.get("from")
        to_val = patch.get("to_value") or patch.get("to")
    else:
        from_val = patch.from_value
        to_val = patch.to_value

    actual = target.get(field)
    if actual != from_val:
        raise ValueError(
            f"override for tenant {tenant!r}, step {step_id!r}, field "
            f"{field!r}: expected base value {from_val!r} but found "
            f"{actual!r}"
        )
    target[field] = to_val


