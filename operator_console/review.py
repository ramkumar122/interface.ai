"""The review screen's four sections, and the approval gate.

Section order follows the order a reviewer's questions actually arrive:
what does it claim to do, what did it really do, what can go wrong, and
show me the evidence.  Reordering them means answering questions nobody
has asked yet.

No approval logic lives here.  `control.approval` owns the hash, the
stamp, the precondition and the integrity guard; this module collects a
form and calls it.  Reimplementing any of that would create a second
approval path that could disagree with `python -m control approve`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from artifacts.schema import CapabilityArtifact, KnownOutcome, Recoverable
from control.approval import _DETECTION_FIELDS, _has_detection_fields

EVIDENCE_DIR = Path("evidence")

# The four assertions a reviewer makes.  Order matters: it walks the same
# path as the sections above it on the page.
CHECKLIST: list[tuple[str, str]] = [
    ("behaviour_matches", "The described behaviour matches the steps"),
    ("no_record_dependent_locator",
     "No locator depends on a value that changes between records"),
    ("risk_correct", "The risk classification is correct"),
    ("outcomes_complete", "The declared outcomes are complete"),
]

APPROVAL_INTRO = (
    "Approving makes this capability callable without a human present. "
    "Approval is bound to a hash of the artifact's execution fields — any "
    "later edit invalidates it and replay refuses."
)


def _why_behaviour_matches(artifact: CapabilityArtifact) -> str:
    return (
        f"Read the description above, then the {len(artifact.steps)} steps "
        f"in section 2. Does the sequence do what the description claims?"
    )


def _why_no_record_dependent_locator(artifact: CapabilityArtifact) -> str:
    """The concern is every locator, not only reads.

    A read step is the sharpest illustration — a cell's accessible name IS
    the value it holds — but a capability that reads nothing still clicks
    and types, and a click located by a name that varied per member would
    be the same failure.  So the general sentence leads, and the read
    example is appended only when the artifact has one.
    """
    general = (
        "Every step is found by role and name, or by position in a named "
        "table — never by a value that varies between records. Read each "
        "locator in section 2 and ask whether it would still find the "
        "right element for a different member."
    )

    read = next((s for s in artifact.steps if s.action == "read"), None)
    if read is None:
        return general

    loc = read.target
    if loc.strategy == "ax_relative":
        where = f'the cell under the column header "{loc.column_header}"'
        if loc.row_key:
            where += f', in the row whose first cell is "{loc.row_key}"'
        # Plain text: this string is autoescaped into the template, so
        # markdown emphasis would render as literal asterisks.  The
        # contrast carries in the wording instead.
        specific = (
            f"Step {read.ordinal} reads {where} — a description of where the "
            f"value sits, not of what it contains. A locator that named the "
            f"value itself would resolve only for the record this was "
            f"captured against. You will not find such a value anywhere in "
            f"this artifact: the compiler refuses to emit one, and a test "
            f"fails if it does."
        )
    else:
        specific = (
            f'Step {read.ordinal} reads {loc.role} "{loc.name}". Check that '
            f"the name describes the field rather than whatever happened to "
            f"be in it — the compiler refuses a read locator named after the "
            f"value it reads, but a name can still be record-specific in "
            f"ways only a reader would notice."
        )
    return f"{general} {specific}"


def _why_risk_correct(artifact: CapabilityArtifact) -> str:
    counts: dict[str, int] = {}
    for s in artifact.steps:
        counts[s.risk] = counts.get(s.risk, 0) + 1
    breakdown = ", ".join(f"{n} {risk}" for risk, n in sorted(counts.items()))
    return (
        f"Each step is safe, guarded_write or irreversible; this one has "
        f"{breakdown}. Irreversible steps are blocked on both paths and "
        f"escalate to a human. Is anything mislabelled?"
    )


def _why_outcomes_complete(artifact: CapabilityArtifact) -> str:
    n = len(artifact.known_outcomes)
    if n == 0:
        return (
            "No outcomes are declared. Can this capability produce any "
            "result other than success? Each one it can produce and has "
            "not declared reaches the caller as a failure instead of an "
            "answer."
        )
    named = ", ".join(ko.id for ko in artifact.known_outcomes)
    return (
        f"{n} outcome{'' if n == 1 else 's'} declared ({named}). Can this "
        f"capability produce a result that is not listed? An undeclared "
        f"outcome reaches the caller as a failure instead of an answer."
    )


_EXPLANATIONS = {
    "behaviour_matches": _why_behaviour_matches,
    "no_record_dependent_locator": _why_no_record_dependent_locator,
    "risk_correct": _why_risk_correct,
    "outcomes_complete": _why_outcomes_complete,
}


def checklist_for(artifact: CapabilityArtifact) -> list[tuple[str, str, str]]:
    """The four assertions with reasoning drawn from *this* artifact.

    A risk reviewer who is not an engineer cannot honestly tick "no locator
    depends on a value that changes between records" without already
    knowing the failure mode.  Generic help text would be boilerplate;
    counts and step numbers taken from the capability in front of them are
    the difference between a checklist and a form.
    """
    return [
        (key, label, _EXPLANATIONS[key](artifact))
        for key, label in CHECKLIST
    ]

ROLES: list[str] = ["Engineering", "Risk / Ops"]


# --------------------------------------------------------------------------- #
# Section 3 — what can go wrong
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OutcomeRow:
    outcome: KnownOutcome
    detection: list[str]
    undetectable: bool

    @property
    def warning(self) -> str | None:
        if not self.undetectable:
            return None
        return (
            f"{self.outcome.id} has no detection fields "
            f"({', '.join(sorted(_DETECTION_FIELDS))}). The engine cannot "
            f"detect this outcome at runtime, so the caller is promised a "
            f"result nothing will ever return."
        )


def describe_detection(outcome: KnownOutcome) -> list[str]:
    """How the engine would recognise this outcome, in plain clauses."""
    clauses: list[str] = []
    if outcome.page_text_pattern:
        clauses.append(f'page text matches "{outcome.page_text_pattern}"')
    if outcome.at_step:
        clauses.append(f"only at step {outcome.at_step}")
    for rp in outcome.requires_present or []:
        clauses.append(f'{rp.role} "{rp.name}" is present')
    for ra in outcome.requires_absent or []:
        if ra.table_name and ra.row_key:
            clauses.append(
                f'table "{ra.table_name}" has no row "{ra.row_key}"'
            )
        elif ra.role:
            clauses.append(f'{ra.role} "{ra.name}" is absent')
    return clauses


def outcome_rows(artifact: CapabilityArtifact) -> list[OutcomeRow]:
    rows: list[OutcomeRow] = []
    for ko in artifact.known_outcomes:
        has_detection = _has_detection_fields(ko.model_dump())
        rows.append(
            OutcomeRow(
                outcome=ko,
                detection=describe_detection(ko),
                undetectable=not has_detection,
            )
        )
    return rows


def describe_recoverable(rec: Recoverable) -> str:
    """What fires this recovery, from whichever trigger field is populated."""
    if rec.trigger_pattern:
        return f'page text matches "{rec.trigger_pattern}"'
    if rec.trigger_role and rec.trigger_name:
        return f'{rec.trigger_role} "{rec.trigger_name}" appears'
    return rec.trigger


# --------------------------------------------------------------------------- #
# Section 4 — evidence
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class EvidenceReport:
    """One evidence directory's `replay-result.json`, if it has one."""

    directory: str
    status: str | None
    outputs: dict[str, str]
    note: str | None = None


@dataclass(frozen=True)
class EvidenceSection:
    verified_runs: int | None
    reports: list[EvidenceReport]
    discovery_run_id: str
    discovery_files: list[str]
    screenshots: list[str] = field(default_factory=list)

    @property
    def verification_note(self) -> str:
        """Say what the artifact actually records, and what it does not."""
        if self.verified_runs is None:
            return (
                "No verification recorded. `approve()` refuses an artifact "
                "with `verified_runs` unset — the machine gate runs before "
                "the human one."
            )
        if not self.reports:
            return (
                f"`verified_runs: {self.verified_runs}` recorded; no per-run "
                f"report found in evidence/. The artifact stores a count, "
                f"not a record — what those runs returned is not kept."
            )
        return (
            f"`verified_runs: {self.verified_runs}` recorded. The artifact "
            f"stores only that count, so the runs below are replay evidence "
            f"reports on disk, not the verification record itself."
        )

    @property
    def association_note(self) -> str:
        """How these reports were matched to this capability.

        The reports do not name one.  Saying which weak link was used is
        the difference between evidence and decoration.
        """
        return (
            "An evidence report does not record which capability produced "
            "it, so these are matched by declared output name. A report "
            "whose outputs this artifact does not declare is not shown."
        )

    @property
    def discovery_note(self) -> str:
        return (
            "Discovery captured no screenshots. The model receives roles and "
            "accessible names; a screenshot is an escalation rung, used when "
            "an observation is ambiguous, not a default."
        )


# Evidence directories whose reports say something about this capability
# replaying cleanly.  Verification does not write its own report, so these
# are the closest thing on disk.
_VERIFICATION_EVIDENCE = ("verify-stamp", "replay-determinism", "replay-success")


def _read_report(
    directory: Path, declared_outputs: set[str]
) -> EvidenceReport | None:
    """One evidence report, if it exists and plausibly belongs here.

    Returns ``None`` when the report's outputs are not ones this artifact
    declares.  Every report in the repo today happens to be for the one
    shipped capability; showing them under a second capability's review
    would be attributing someone else's evidence to it.
    """
    report_path = directory / "replay-result.json"
    if not report_path.is_file():
        return None
    try:
        data = json.loads(report_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return EvidenceReport(
            directory=directory.name, status=None, outputs={},
            note=f"unreadable: {type(exc).__name__}",
        )

    # `replay-determinism` reports a list of runs; the others report one.
    if isinstance(data.get("runs"), list):
        merged: dict[str, str] = {}
        names: set[str] = set()
        for i, run in enumerate(data["runs"], 1):
            for name, value in (run.get("outputs") or {}).items():
                names.add(name)
                merged[f"{name} (run {i}, member {run.get('member_no')})"] = value
        if not names or not names <= declared_outputs:
            return None
        return EvidenceReport(
            directory=directory.name,
            status="success" if data.get("all_passed") else "mixed",
            outputs=merged,
            note=data.get("description"),
        )

    outputs = data.get("outputs") or {}
    if not outputs or not set(outputs) <= declared_outputs:
        return None

    status = data.get("status")
    variant = data.get("variant")
    return EvidenceReport(
        directory=directory.name,
        status=status,
        outputs=outputs,
        # The variant name repeats the status on the happy path; saying it
        # twice reads as two facts when it is one.
        note=None if variant == status else variant,
    )


def evidence_section(
    artifact: CapabilityArtifact, evidence_dir: Path | None = None
) -> EvidenceSection:
    root = Path(evidence_dir) if evidence_dir is not None else EVIDENCE_DIR
    declared = {o.name for o in artifact.outputs}
    reports: list[EvidenceReport] = []
    for name in _VERIFICATION_EVIDENCE:
        report = _read_report(root / name, declared)
        if report is not None:
            reports.append(report)

    run_id = artifact.created_from.run_id
    discovery_files: list[str] = []
    discovery_dir = root / run_id
    if discovery_dir.is_dir():
        discovery_files = sorted(
            f"{run_id}/{p.name}" for p in discovery_dir.iterdir() if p.is_file()
        )

    return EvidenceSection(
        verified_runs=artifact.meta.verified_runs,
        reports=reports,
        discovery_run_id=run_id,
        discovery_files=discovery_files,
    )


# --------------------------------------------------------------------------- #
# The approval gate
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ApprovalSubmission:
    """A validated approve form.  Invalid submissions never reach `approve()`."""

    reviewer: str
    role: str
    checked: set[str]

    @property
    def approver(self) -> str:
        """`approved_by` carries name and role — the one free-text field the
        `Approval` model has.  `python -m control approve --approver "D. PARK"`
        already uses it this way."""
        return f"{self.reviewer} — {self.role}"


def validate_approval(
    reviewer: str, role: str, checked: list[str]
) -> tuple[ApprovalSubmission | None, list[str]]:
    """Enforce the checklist server-side.

    The form disables the button, but a `disabled` attribute is a
    suggestion a curl request ignores.  `approved_by` next to a content
    hash is only meaningful if someone actually looked, so the assertion
    is checked where it cannot be skipped.
    """
    errors: list[str] = []
    name = (reviewer or "").strip()
    if not name:
        errors.append("A reviewer name is required.")

    if role not in ROLES:
        errors.append(f"Role must be one of: {', '.join(ROLES)}.")

    checked_set = {c for c in checked if c}
    missing = [label for key, label in CHECKLIST if key not in checked_set]
    if missing:
        errors.append(
            "Every assertion must be checked. Not checked: "
            + "; ".join(missing)
        )

    if errors:
        return None, errors
    return ApprovalSubmission(
        reviewer=name, role=role, checked=checked_set
    ), []
