"""Approval model: content hash, approve/revoke, review checklist.

Approval binds to a content hash of the execution-affecting fields.
The replay engine recomputes the hash before executing and refuses on
mismatch — regardless of the approval status field.

The hash uses the full SHA-256 digest, prefixed ``sha256:`` so the
algorithm is legible in the JSON file.  No truncation: approval is a
security property, and there is no cost to the full 64 characters in
a string nobody types by hand.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from artifacts.schema import CapabilityArtifact


# --------------------------------------------------------------------------- #
# Content hash
# --------------------------------------------------------------------------- #

# Fields that affect execution.  Everything else is metadata/provenance.
_HASHED_FIELDS = frozenset({
    "steps", "known_outcomes", "recoverables",
    "overrides", "inputs", "outputs",
})


def content_hash(artifact: CapabilityArtifact) -> str:
    """SHA-256 of the execution-affecting fields.

    Canonical JSON (sorted keys, no whitespace) ensures the hash is
    stable across serialisation order.  Prefixed ``sha256:`` so the
    algorithm is legible in the file.
    """
    payload = artifact.model_dump(include=_HASHED_FIELDS)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"sha256:{digest}"


# --------------------------------------------------------------------------- #
# Approve / revoke
# --------------------------------------------------------------------------- #


class ApprovalPreconditionError(Exception):
    """Raised when an artifact is not ready for approval."""


def approve(
    artifact: CapabilityArtifact,
    approver: str,
) -> CapabilityArtifact:
    """Set approval status, compute and store the content hash.

    Refuses to approve an artifact with no verification record —
    the machine gate must pass before the human gate.
    """
    if artifact.meta.verified_runs is None:
        raise ApprovalPreconditionError(
            "Cannot approve: verified_runs is not set. "
            "Run verification first to prove the artifact replays "
            "deterministically (e.g. `python scripts/verify_gate.py --variant clean`)."
        )
    h = content_hash(artifact)
    data = artifact.model_dump()
    data["approval"]["status"] = "approved"
    data["approval"]["approved_by"] = approver
    data["approval"]["approved_at"] = datetime.now(timezone.utc).isoformat()
    data["approval"]["content_hash"] = h
    return CapabilityArtifact.model_validate(data)


def revoke(artifact: CapabilityArtifact) -> CapabilityArtifact:
    """Revoke approval, clear the hash."""
    data = artifact.model_dump()
    data["approval"]["status"] = "revoked"
    data["approval"]["content_hash"] = None
    return CapabilityArtifact.model_validate(data)


# --------------------------------------------------------------------------- #
# Check (called by the engine before REPLAY execution)
# --------------------------------------------------------------------------- #


def check_approval(artifact: CapabilityArtifact) -> tuple[bool, str]:
    """Validate that an approved artifact's hash matches its content.

    Returns ``(ok, reason)``.  Draft/revoked artifacts pass — approval
    enforcement applies only to artifacts that claim to be approved.
    """
    if artifact.approval.status != "approved":
        return True, "not approved — no hash check required"

    stored = artifact.approval.content_hash
    if stored is None:
        return False, (
            "Artifact status is 'approved' but no content_hash is stored. "
            "Run `cli approve` to set it."
        )

    current = content_hash(artifact)
    if current != stored:
        return False, (
            f"Content hash mismatch: approved hash {stored!r}, "
            f"current {current!r}. The artifact has been modified "
            f"since approval. Run `cli revoke` then `cli approve`."
        )

    return True, "approved, hash valid"


# --------------------------------------------------------------------------- #
# Write with hash guard
# --------------------------------------------------------------------------- #


class ApprovalIntegrityError(Exception):
    """Raised when writing an approved artifact whose hash doesn't match."""


def artifact_filename(artifact: CapabilityArtifact) -> str:
    """The one filename an artifact is written under.

    Major version only: ``coredesk.member.read_savings_balance@1.json``.
    ``structure.mdc`` permits either reading — it says ``@<version>`` and
    gives ``@1`` as its example — and two writers took different ones, so
    the same capability at the same version could exist as two files.
    `catalog.resolve()` then had two matches for one key.  One helper, both
    writers, no ambiguity to resolve.
    """
    major = artifact.version.split(".")[0]
    return f"{artifact.capability_id}@{major}.json"


def write_artifact(path: Path, artifact: CapabilityArtifact) -> None:
    """Write an artifact to disk, refusing if an approved hash is stale.

    An approved artifact whose content has changed since approval is an
    error, not a silent demotion.  Call ``cli revoke`` first or
    ``cli approve`` again.
    """
    if artifact.approval.status == "approved" and artifact.approval.content_hash:
        current = content_hash(artifact)
        if current != artifact.approval.content_hash:
            raise ApprovalIntegrityError(
                f"Cannot write approved artifact with stale hash. "
                f"Approved: {artifact.approval.content_hash!r}, "
                f"current: {current!r}. "
                f"Run `cli revoke` first, or `cli approve` to re-approve."
            )
    path.write_text(artifact.model_dump_json(indent=2))


# --------------------------------------------------------------------------- #
# Detection fields check (deferred from task 6)
# --------------------------------------------------------------------------- #

_DETECTION_FIELDS = {"page_text_pattern", "at_step", "requires_present", "requires_absent"}


def _has_detection_fields(outcome_data: dict) -> bool:
    """True if at least one detection field is non-null/non-empty."""
    return any(outcome_data.get(f) for f in _DETECTION_FIELDS)


# --------------------------------------------------------------------------- #
# Review checklist
# --------------------------------------------------------------------------- #


def review_checklist(artifact: CapabilityArtifact) -> str:
    """Human-readable review checklist for an artifact."""
    lines: list[str] = []
    cap = f"{artifact.capability_id}@{artifact.version}"
    lines.append(f"═══ Artifact Review: {cap} ═══")
    lines.append("")
    lines.append(f"Status: {artifact.approval.status}")
    lines.append(f"Risk class: {artifact.approval.risk_class} "
                 f"(derived: {artifact.derived_risk_class()})")
    if artifact.meta.verified_runs is not None:
        lines.append(f"Verified runs: {artifact.meta.verified_runs}")
    else:
        lines.append("Verified runs: none recorded")
    lines.append("")

    # Steps
    total_rejected = 0
    steps_with_rejected = 0
    lines.append(f"── Steps ({len(artifact.steps)}) "
                 + "─" * 50)
    for s in artifact.steps:
        loc = s.target
        loc_desc = f'{loc.strategy}  {loc.role} "{loc.name}"'
        if loc.strategy == "ax_relative":
            loc_desc = (f"ax_relative  {loc.role} "
                        f"{loc.table_name}/{loc.column_header}/{loc.row_key}")
        extra = ""
        if s.value and s.value.source == "literal":
            # A literal is a value frozen at discovery time.  The compiler
            # flags it; printing the flag is what makes the flag useful.
            extra = f'  value: "{s.value.literal}"'
            if s.value.review_required:
                extra += "   ⚠ literal — review this"
        if s.value and s.value.source == "input":
            extra = f"  ← input: {s.value.input_name}"
        if s.value and s.value.source == "output":
            out = s.value.output_name
            out_type = next(
                (o.type for o in artifact.outputs if o.name == out), "?"
            )
            extra = f"  → output: {out} ({out_type})"
        lines.append(f"  {s.ordinal}. {s.step_id:<18s} {s.action:<6s} {loc_desc}{extra}")
        if s.rejected:
            total_rejected += len(s.rejected)
            steps_with_rejected += 1
    total_steps = len(artifact.steps)
    steps_without = total_steps - steps_with_rejected
    if total_rejected:
        lines.append(
            f"  ({steps_with_rejected} of {total_steps} steps recorded "
            f"a rejected locator ({steps_without} had no alternative to reject))"
        )
    else:
        lines.append(
            f"  (0 of {total_steps} steps recorded a rejected locator)"
        )
    lines.append("")

    literals = [
        s for s in artifact.steps
        if s.value and s.value.source == "literal" and s.value.review_required
    ]

    # Known outcomes
    warnings: list[str] = []
    lines.append(f"── Known Outcomes ({len(artifact.known_outcomes)}) "
                 + "─" * 40)
    for ko in artifact.known_outcomes:
        d = ko.model_dump()
        has_det = _has_detection_fields(d)
        marker = "" if has_det else "  ⚠ NO DETECTION FIELDS"
        lines.append(f"  {ko.id:<24s} {ko.caller_action:<10s}"
                     f"{marker}")
        if ko.page_text_pattern:
            lines.append(f"    page_text: \"{ko.page_text_pattern}\"")
        if ko.at_step:
            lines.append(f"    at_step: {ko.at_step}")
        if ko.requires_present:
            for rp in ko.requires_present:
                lines.append(f"    requires_present: {rp.role} \"{rp.name}\"")
        if ko.requires_absent:
            for ra in ko.requires_absent:
                lines.append(f"    requires_absent: "
                             f"{ra.strategy} row \"{ra.row_key}\"")
        if not has_det:
            warnings.append(
                f"⚠ {ko.id} has no detection fields "
                f"({', '.join(sorted(_DETECTION_FIELDS))}). "
                f"The engine cannot detect this outcome at runtime."
            )
    lines.append("")

    # Recoverables
    lines.append(f"── Recoverables ({len(artifact.recoverables)}) "
                 + "─" * 40)
    for rec in artifact.recoverables:
        action = rec.recovery_action or rec.strategy
        trigger = ""
        if rec.trigger_pattern:
            trigger = f'  trigger: "{rec.trigger_pattern}"'
        elif rec.trigger_role and rec.trigger_name:
            trigger = f'  trigger: {rec.trigger_role} "{rec.trigger_name}"'
        lines.append(f"  {rec.trigger:<24s} {action:<10s}{trigger}")
        if rec.recovery_target_role and rec.recovery_target_name:
            lines.append(f"    target: {rec.recovery_target_role} "
                         f'"{rec.recovery_target_name}"')
    lines.append("")

    # Overrides
    if artifact.overrides:
        tenants = list(artifact.overrides.keys())
        lines.append(f"── Overrides ({len(tenants)} tenant(s)) "
                     + "─" * 40)
        for t in tenants:
            ov = artifact.overrides[t]
            n = len(ov.steps)
            step_ids = ", ".join(sorted(ov.steps.keys()))
            lines.append(f"  {t}: {n} step overrides ({step_ids})")
        lines.append("")

    # Warnings
    for s in literals:
        warnings.append(
            f'⚠ {s.step_id} uses the literal "{s.value.literal}". It was '
            f"frozen at discovery time and every invocation will use it. "
            f"If it should vary per call, it belongs in `inputs`."
        )

    if warnings:
        lines.append("── Warnings " + "─" * 50)
        for w in warnings:
            lines.append(f"  {w}")
        lines.append("")

    return "\n".join(lines)
