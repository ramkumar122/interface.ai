"""Transcript + ResolutionTraces -> CapabilityArtifact.

Five passes: prune, parameterize, locator ladder, infer waits, checkpoint.
Never touches the browser — consumes traces only.
"""

from __future__ import annotations

import re
import sys as _sys
from dataclasses import dataclass

from agent.loop import StepRecord, DiscoveryResult
from agent.prune import prune
from artifacts.schema import (
    CapabilityArtifact,
    Approval,
    Checkpoint,
    CreatedFrom,
    EscalationPolicy,
    FrameRef,
    InputParam,
    Locator,
    Meta,
    NamedRegionRef,
    OutputParam,
    RejectedLocator,
    Step,
    ValueSpec,
    WaitCondition,
)


class CompilationError(Exception):
    """A diagnostic explaining why compilation failed."""

    def __init__(self, reason: str, step: int | None = None) -> None:
        self.step = step
        self.reason = reason
        msg = f"step {step}: {reason}" if step is not None else reason
        super().__init__(msg)


# --------------------------------------------------------------------------- #
# Frame reference construction
# --------------------------------------------------------------------------- #

_FRAME_RE = re.compile(r"^frame\[(.+)\]$")


def _build_frame_ref(
    frame_path: str, input_values: dict[str, str]
) -> FrameRef:
    """Build a FrameRef from a frame path string.

    Replaces declared input values to derive a name_prefix rather than
    guessing where to truncate.  When no input value appears, falls back to
    exact match with the full frame string (safe but inflexible).
    """
    if frame_path == "main":
        return FrameRef(match="exact", value="main")

    m = _FRAME_RE.match(frame_path)
    if not m:
        return FrameRef(match="exact", value=frame_path)

    full_name = m.group(1)

    for input_val in input_values.values():
        if input_val and input_val in full_name:
            idx = full_name.index(input_val)
            prefix = full_name[:idx].rstrip()
            if prefix:
                return FrameRef(
                    match="role_name",
                    role="iframe",
                    name_prefix=prefix,
                )

    # No input value found — exact match, flagged for review
    return FrameRef(match="exact", value=frame_path)


# --------------------------------------------------------------------------- #
# Step ID generation
# --------------------------------------------------------------------------- #


def _make_step_id(action: str, locator: Locator, args: dict, ordinal: int,
                  seen: set[str]) -> str:
    """Generate a stable step_id from the action and the **locator**.

    Not from the observed element name.  A read step's target is a cell
    whose accessible name is the value it holds, so the old version emitted
    ``read_12_845_50`` — the member's balance, slugified, as an identifier.

    That is the third field the same value leaked into, after ``intent``
    and ``target.name``, and it is not an internal detail: ``step_id``
    appears in the review screen's step list, in tenant override keys, and
    in every failure diagnostic.  A reviewer reading ``read_12_845_50`` on
    an approval screen learns something false about what the capability
    does.

    A read step is named for the output it fills, which is what the
    hand-written artifact always called it (``read_savings_balance``).
    """
    if action == "read" and args.get("into_output"):
        base = f"read_{args['into_output']}"
    elif locator.strategy == "ax_relative" and locator.column_header:
        # No stable name to use; the column is the durable part.
        base = f"{action}_{locator.column_header}"
    elif locator.name:
        base = f"{action}_{locator.name}"
    else:
        base = f"{action}_{ordinal}"

    slug = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")

    if slug in seen:
        slug = f"{slug}_{ordinal}"
    seen.add(slug)
    return slug


# --------------------------------------------------------------------------- #
# Intent generation (known weakness: mechanical, not model's own words)
# --------------------------------------------------------------------------- #


def _describe_target(locator: Locator) -> str | None:
    """Where the step acts, described the way the locator finds it.

    ``None`` when the locator carries nothing nameable, so the caller can
    fall back to a bare phrasing rather than quoting an empty string.
    """
    role_label = locator.role or "element"
    if locator.strategy == "ax_relative":
        # The cell's accessible name IS the runtime value, so it is not in
        # the locator and must not be in the sentence.  Describe the
        # structure that finds it.
        parts = [f'the {role_label} under column "{locator.column_header}"']
        if locator.table_name:
            parts.append(f'in table "{locator.table_name}"')
        return " ".join(parts)
    if locator.name:
        if locator.strategy == "ax_scoped" and locator.within:
            return (
                f'{role_label} "{locator.name}" in '
                f'{locator.within.role} "{locator.within.name}"'
            )
        return f'{role_label} "{locator.name}"'
    return None


def _make_intent(action: str, locator: Locator, args: dict) -> str:
    """Synthesize intent from the action and the **locator**.

    Never from the resolved element name.  For ``ax_relative`` the resolved
    name is the cell's text — which is the value the step reads — and using
    it produced intents like ``Read cell "12,845.50" into savings_balance``:
    true of the member discovery happened to run on, false of every other,
    and never wrong in a way that fails.  A reviewer answers "what does this
    do?" from ``intent`` (REPORT §2), so a misleading one misinforms the
    review screen built to make that judgement possible.

    The locator is the durable description.  The observed name is an
    accident of the discovery run.
    """
    where = _describe_target(locator)
    if action == "read":
        out = args.get("into_output", "")
        if where:
            return f"Read {where} into {out}"
        return f"Read value into {out}"
    if action == "type":
        fi = args.get("from_input")
        if fi and where:
            return f"Type {fi} into {where}"
        if where:
            return f"Type into {where}"
        return "Type value"
    if where:
        return f"{action.capitalize()} {where}"
    return f"{action.capitalize()} {locator.role or 'element'}"


_RISK_ORDER = {"safe": 0, "guarded_write": 1, "irreversible": 2}


def _derived_risk(steps: list[Step]) -> str:
    """The capability's risk: the highest of its steps'.

    Mirrors `CapabilityArtifact.derived_risk_class()`, which cannot be
    called here — the artifact does not exist until the constructor this
    feeds returns.
    """
    highest = max((_RISK_ORDER.get(s.risk, 0) for s in steps), default=0)
    for name, order in _RISK_ORDER.items():
        if order == highest:
            return name
    return "safe"


def description_warnings(description: str, inputs: dict[str, str]) -> list[str]:
    """Warn when a description names a value the capability takes as input.

    Narrower than the rules on ``intent``, ``target.name`` and ``step_id``,
    and deliberately so.  Those three are *generated* here, so a rule about
    them is enforceable where they are made.  ``description`` is ``--goal``
    copied verbatim — a human sentence the compiler has no licence to
    rewrite.

    What is still checkable: a description that says "member 100101" for a
    capability taking ``member_no`` as a parameter describes one record
    rather than the capability.  A warning, not an error: bad wording is
    misleading, not incorrect execution, and failing the compile would
    throw away a working artifact over prose.
    """
    warnings: list[str] = []
    for name, value in inputs.items():
        if value and str(value) in description:
            warnings.append(
                f"description contains {value!r}, the value of declared "
                f"input {name!r} — it describes one record rather than the "
                f"capability. Reword the goal to name the parameter."
            )
    return warnings


# Every field the compiler generates that must describe structure rather
# than a record.  Named once, because five separate patches to five of them
# is how this class of bug kept coming back.
_GENERATED_FIELDS = (
    "intent", "step_id", "target.name", "target.row_key",
    "target.column_header", "wait_for.name",
)


def _slug(value: str) -> str:
    """How a value looks by the time it reaches a `step_id`."""
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def record_data_violations(
    steps: list[Step],
    *,
    input_values: dict[str, str],
    output_values: dict[str, str],
) -> list[str]:
    """Fields that contain a value belonging to one record.

    **No compiler-generated field may contain a value the capability reads
    into an output or takes as an input.**

    One rule over six fields, because it was four separate patches before it
    was a rule:

    ======================  ==============================  ===================
    field                   held                            found by
    ======================  ==============================  ===================
    steps[].intent          the balance being read          the review screen
    steps[].step_id         the balance being read          a capture script
    target.row_key          a member number in a card id    reading a locator
    wait_for.name           the city being read             a replay failing
    ======================  ==============================  ===================

    The root cause is single: the compiler builds these from the element as
    it resolved on the recorded run, and that element carries that record's
    data.  Checking one field at a time only ever finds the field somebody
    happened to look at.
    """
    values = {v for v in list(input_values.values()) + list(output_values.values()) if v}
    if not values:
        return []

    slugged = {v: _slug(v) for v in values}
    violations: list[str] = []

    for step in steps:
        fields: list[tuple[str, str | None]] = [
            ("intent", step.intent),
            ("step_id", step.step_id),
            ("target.name", step.target.name),
            ("target.row_key", step.target.row_key),
            ("target.column_header", step.target.column_header),
            ("wait_for.name", step.wait_for.name if step.wait_for else None),
        ]
        for field, content in fields:
            if not content:
                continue
            for value in values:
                needle = slugged[value] if field == "step_id" else value
                if needle and needle in str(content):
                    violations.append(
                        f"step {step.ordinal} ({step.step_id}): {field} is "
                        f"{content!r}, which contains {value!r} — a value "
                        f"this capability {'reads' if value in output_values.values() else 'takes as an input'}. "
                        f"The field describes one record, not the capability."
                    )
                    break

    return violations


def _created_from(
    records: list[StepRecord],
    result: DiscoveryResult,
    run_id: str | None,
    model: str | None,
) -> CreatedFrom:
    """Provenance, from the caller when it knows it.

    ``DiscoveryResult`` carries neither the run id nor the model name, so
    without them this falls back to the first observation hash and
    ``"unknown"``.  That is a provenance block naming a run nobody can open
    and a model that does not exist — on an artifact whose whole purpose is
    to be auditable.  The caller (``scripts/run_discover.py``) knows both;
    passing them is the fix, and the fallback stays honest rather than
    inventing something plausible.
    """
    return CreatedFrom(
        run_id=run_id or (
            result.transcript[0].obs_hash[:16]
            if result.transcript else "unknown"
        ),
        model=model or "unknown",
        steps_in_transcript=len(records),
        wall_seconds=result.wall_seconds,
    )


# --------------------------------------------------------------------------- #
# Pass 2: Parameterize
# --------------------------------------------------------------------------- #


def _parameterize(
    rec: StepRecord, input_values: dict[str, str]
) -> ValueSpec | None:
    """Determine the ValueSpec for a step.

    Both paths produce parameterized output:
    - Model used from_input → trivially parameterized.
    - Model typed a literal matching a declared input value → parameterized
      by matching. The compiler must not depend on the model using from_input.
    """
    args = rec.action_args

    if rec.action_kind == "read":
        out_name = args.get("into_output")
        if out_name:
            return ValueSpec(source="output", output_name=out_name)
        return None

    if rec.action_kind == "type":
        fi = args.get("from_input")
        if fi:
            return ValueSpec(source="input", input_name=fi)

        text = args.get("text", "")
        # Check if the literal matches any declared input value
        for name, val in input_values.items():
            if text == val:
                return ValueSpec(source="input", input_name=name)

        if text:
            return ValueSpec(source="literal", literal=text, review_required=True)
        return None

    if rec.action_kind == "select":
        option = args.get("option", "")
        for name, val in input_values.items():
            if option == val:
                return ValueSpec(source="input", input_name=name)
        if option:
            return ValueSpec(source="literal", literal=option, review_required=True)
        return None

    return None


# --------------------------------------------------------------------------- #
# Pass 3: Locator ladder
# --------------------------------------------------------------------------- #


def _build_locator(
    rec: StepRecord, input_values: dict[str, str]
) -> tuple[Locator, list[RejectedLocator]]:
    """Build a Locator from the ResolutionTrace, or raise CompilationError.

    Ladder order:
      1. ax — name unique in frame (most common)
      2. ax_relative — cell with table context (structural > name-based
         for tables, because a cell's accessible name IS its runtime value)
      3. ax_scoped — unique inside a named region
      4. FAIL

    Two conditions disqualify rung 1 before uniqueness is even considered,
    and both were found by compiling a second capability:

    * **An empty name.**  A ``<p>`` banner has no accessible name — its text
      is content, not a name — and an empty name is trivially "unique in
      frame".  Rung 1 would build ``ax`` with ``name=""``, which the schema
      rejects with a pydantic error three frames deep instead of the
      compilation failure this ladder is supposed to raise.

    * **A name equal to the value being read.**  Rung 2's docstring says a
      cell's accessible name is its content — but rung 1 runs first and
      takes it whenever that content happens to be unique on the page.
      Reading ``cell "LOCKED"`` into ``card_status`` compiles a locator that
      only resolves for members whose card is already locked.  That is the
      failure in REPORT §3 recurring: ordering plus one transcript-specific
      test was not a structural guarantee.  This is.
    """
    trace = rec.trace
    if trace is None:
        raise CompilationError(
            "no ResolutionTrace — cannot build locator", step=rec.step
        )

    frame_ref = _build_frame_ref(trace.frame, input_values)
    rejected: list[RejectedLocator] = []

    # Record rejected dom_id if present
    if trace.dom_id_observed:
        rejected.append(RejectedLocator(
            strategy="dom_id",
            value=trace.dom_id_observed,
            reason="ASP.NET WebForms control IDs are unstable across "
                   "versions and tenant configurations",
        ))

    # A read step's own result: never usable as the name that finds it.
    read_value = (
        rec.result_detail if rec.action_kind == "read" else None
    )
    name_is_the_answer = bool(
        read_value is not None and trace.name and trace.name == read_value
    )
    if name_is_the_answer:
        rejected.append(RejectedLocator(
            strategy="ax",
            value=trace.name,
            reason=(
                "the accessible name is the value this step reads, so the "
                "locator would only resolve for the record it was recorded "
                "against"
            ),
        ))

    # Strategy 1: unique name in frame — and a name worth matching on
    if trace.name_unique_in_frame and trace.name and not name_is_the_answer:
        return Locator(
            strategy="ax",
            frame=frame_ref,
            role=trace.role,
            name=trace.name,
        ), rejected

    # Strategy 2: table cell — prefer structural locator over name-based
    # because a cell's accessible name is its content (a runtime value like
    # "12,845.50" that changes per member)
    tc = trace.table_context
    if tc is not None and tc.column_header and tc.row_key:
        return Locator(
            strategy="ax_relative",
            frame=frame_ref,
            role=trace.role,
            table_name=tc.table_name,
            column_header=tc.column_header,
            row_key=tc.row_key,
            row_match="exact",
        ), rejected

    # Strategy 3: unique within a named scope
    if trace.enclosing_named_regions:
        region = trace.enclosing_named_regions[0]
        return Locator(
            strategy="ax_scoped",
            frame=frame_ref,
            role=trace.role,
            name=trace.name,
            within=NamedRegionRef(role=region.role, name=region.name),
        ), rejected

    # Strategy 4: FAIL — with a diagnostic that says which rung refused
    # and why, because "cannot locate" alone does not tell an author what
    # to change.
    if name_is_the_answer:
        why = (
            f"its accessible name is the value it reads ({trace.name!r}), "
            f"and no table context or named region offers a structural "
            f"alternative"
        )
    elif not trace.name:
        why = (
            "it has no accessible name (a paragraph or banner carries text "
            "as content, not as a name), and no table context or named "
            "region offers a structural alternative"
        )
    else:
        why = "not unique, no table context, no named scope"

    raise CompilationError(
        f"cannot locate {trace.role} \"{trace.name}\" in frame "
        f"\"{trace.frame}\": {why}",
        step=rec.step,
    )


# --------------------------------------------------------------------------- #
# Pass 4: Infer waits
# --------------------------------------------------------------------------- #

# Only these action types cause page navigation / new content to load.
_NAVIGATING_ACTIONS = frozenset({"click", "navigate"})


def _infer_wait(
    rec: StepRecord,
    next_rec: StepRecord | None,
    input_values: dict[str, str],
    record_values: frozenset[str] = frozenset(),
) -> WaitCondition | None:
    """Infer what to wait for after a step.

    Only navigating actions (click, navigate) produce waits. Type, read, and
    select don't cause page changes in CoreDesk (no JS, no fetch).

    Waits prefer a landmark (table/region) over the specific target element,
    because the target may be a cell whose accessible name is a runtime
    value.  That reasoning was here from the start and applied only to the
    cross-frame branch; the same-frame branch took the target name
    unconditionally, so an address-update capability compiled a wait for
    `cell "Tempe"` and timed out for every other city.  Both branches now
    use the same ladder.

    ``record_values`` is what must not appear: the declared input values and
    everything read into an output.  When the target's name is one of them,
    fall through to structure, and to no wait at all rather than a wait that
    can never be satisfied.  A step with no wait is slower and correct.
    """
    if rec.action_kind not in _NAVIGATING_ACTIONS:
        return None

    if next_rec is None or next_rec.trace is None:
        return None

    next_trace = next_rec.trace
    next_frame_ref = _build_frame_ref(next_trace.frame, input_values)
    current_frame = rec.trace.frame if rec.trace else None
    cross_frame = bool(current_frame and current_frame != next_trace.frame)

    # A target whose name is a value cannot be waited on: the next record
    # will carry a different one.
    target_name = next_trace.name or ""
    name_is_record_data = bool(target_name and target_name in record_values)

    # Structure first, for a cross-frame hop or a value-named target.
    if cross_frame or name_is_record_data:
        tc = next_trace.table_context
        if tc and tc.table_name and tc.table_name not in record_values:
            return WaitCondition(
                frame=next_frame_ref, role="table", name=tc.table_name,
            )
        for region in next_trace.enclosing_named_regions or []:
            if region.name and region.name not in record_values:
                return WaitCondition(
                    frame=next_frame_ref, role=region.role, name=region.name,
                )

    if name_is_record_data:
        # Nothing structural to wait on.  No wait beats a wait for a value
        # that will never appear — that is a ten-second timeout and a failed
        # run, versus a step that proceeds without one.
        return None

    return WaitCondition(
        frame=next_frame_ref, role=next_trace.role, name=target_name,
    )


# --------------------------------------------------------------------------- #
# The compiler
# --------------------------------------------------------------------------- #


def compile_transcript(
    records: list[StepRecord],
    result: DiscoveryResult,
    *,
    capability_id: str,
    inputs: dict[str, str],
    outputs: dict[str, str],
    description: str = "",
    surface_id: str = "coredesk",
    run_id: str | None = None,
    model: str | None = None,
) -> CapabilityArtifact:
    """Compile a discovery transcript into a capability artifact.

    Raises ``CompilationError`` on:
    - No successful done step
    - A step that cannot be located
    - Missing declared outputs
    """
    # Validate: must end with done
    done_recs = [r for r in records if r.action_kind == "done" and r.result_ok]
    if not done_recs:
        raise CompilationError("transcript does not contain a successful done step")

    # Validate: all declared outputs must be filled
    for out_name in outputs:
        if out_name not in result.outputs_filled:
            raise CompilationError(f"declared output {out_name!r} was never read")

    for w in description_warnings(description or "", inputs):
        print(f"COMPILE WARNING: {w}", file=_sys.stderr)

    # Everything that belongs to the record this run happened to use.  No
    # generated field may contain any of it.
    record_values = frozenset(
        v for v in list(inputs.values()) + list(result.outputs_filled.values())
        if v
    )

    # --- Pass 1: Prune ---
    pruned = prune(records)
    action_recs = [r for r in pruned if r.action_kind not in ("done", "escalate", "give_up")]
    done_rec = done_recs[-1]

    # --- Passes 2-4 per step ---
    steps: list[Step] = []
    seen_ids: set[str] = set()

    for i, rec in enumerate(action_recs):
        ordinal = i + 1

        # Pass 2: Parameterize
        value = _parameterize(rec, inputs)

        # Pass 3: Locator
        locator, rejected = _build_locator(rec, inputs)

        # Pass 4: Wait inference
        # Next record is the next action step, or the done step for the last
        # one. The done step's observation is the page the model saw when it
        # decided the goal was met — the "next state" for the last action step.
        if i + 1 < len(action_recs):
            next_rec = action_recs[i + 1]
        else:
            next_rec = done_rec

        wait = _infer_wait(rec, next_rec, inputs, record_values)

        # Step ID
        step_id = _make_step_id(
            rec.action_kind, locator, rec.action_args, ordinal, seen_ids
        )

        # Intent — from the compiled locator, not the observed name
        intent = _make_intent(rec.action_kind, locator, rec.action_args)

        steps.append(Step(
            step_id=step_id,
            ordinal=ordinal,
            intent=intent,
            action=rec.action_kind,
            risk=rec.verdict_risk,
            target=locator,
            value=value,
            wait_for=wait,
            rejected=rejected,
        ))

    # --- The rule, as one check over every generated field ---
    # Prevention happens upstream: intents come from the locator, step ids
    # from the output name, waits from structure.  This is the backstop that
    # says so out loud when prevention misses, rather than shipping an
    # artifact that works for exactly one record.
    violations = record_data_violations(
        steps, input_values=inputs, output_values=result.outputs_filled,
    )
    if violations:
        raise CompilationError(
            "generated fields contain record data:\n  "
            + "\n  ".join(violations)
            + "\n\nA locator field that must name a record — a row key, "
              "typically — needs to reference a declared input, and "
              "`Locator.row_key` is a plain string. That is a schema change, "
              "not something to work around here."
        )

    # --- Pass 5: Checkpoint ---
    checkpoint = Checkpoint(
        description=done_rec.action_args.get("rationale", "Goal completed"),
        evidence_refs=list(result.outputs_filled.keys()),
        proposed_by="compiler",
    )

    # Build input/output param lists
    input_params = [
        InputParam(
            name=name,
            type="string",
            description=f"Declared input: {name}",
            example=val,
        )
        for name, val in inputs.items()
    ]
    output_params = [
        OutputParam(
            name=name,
            type=typ,
            shape=f"Value of type {typ}",
            description=f"Declared output: {name}",
        )
        for name, typ in outputs.items()
    ]

    return CapabilityArtifact(
        capability_id=capability_id,
        version="1.0",
        description=description or f"Compiled capability: {capability_id}",
        surface_id=surface_id,
        created_from=_created_from(records, result, run_id, model),
        inputs=input_params,
        outputs=output_params,
        steps=steps,
        known_outcomes=[],
        checkpoint=checkpoint,
        recoverables=[],
        overrides={},
        escalation_policy=EscalationPolicy(
            on_stuck="Escalate to human operator",
            on_unknown_state="Escalate immediately",
            on_blocked="Return policy_blocked outcome",
        ),
        approval=Approval(
            status="draft",
            approved_by=None,
            approved_at=None,
            # Derived from the steps just built, not hardcoded.  A
            # capability that locks a card compiled as `safe` while its
            # Apply step was `guarded_write`, and the console had to render
            # the disagreement.  A declared class that cannot disagree with
            # the derived one is better than one that reports it.
            risk_class=_derived_risk(steps),
        ),
        meta=Meta(schema_version="1.0", compiler_version="0.1"),
    )
