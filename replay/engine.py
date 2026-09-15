"""Step executor: artifact + Surface -> ReplayResult.

The core replay loop.  Same code runs in verification (during discovery) and in
production.  Never imports ``google.genai`` or ``playwright``.

Two detection points per step:
  1. Pre-locate — catches conditions present when the step begins.
  2. Post-wait (on timeout only) — catches conditions that appeared after the
     action, turning "wait timed out" into "app_error at step 3."

Failures are data (the three-way result contract), not exceptions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from artifacts.schema import CapabilityArtifact, Step
from control.policy import Mode, check as policy_check
from replay.detect import (
    DetectorHit,
    Detector,
    OutcomeReclassification,
    build_detectors,
    build_reclassifications,
    run_detectors,
    try_reclassify,
)
from replay.locate import LocateError, locate, match_frame
from control.approval import check_approval
from control.escalate import InterventionRequest, build_intervention_request
from replay.result import (
    DriftSignal,
    RecoveryTrace,
    ReplayBusinessOutcome,
    ReplayEscalated,
    ReplayFailure,
    ReplayResult,
    ReplaySuccess,
    StepTrace,
)
from surface.base import (
    ActionResult,
    Click,
    Navigate,
    Node,
    Read,
    Select,
    Surface,
    Type,
)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


@dataclass
class _ReplayState:
    """Mutable state shared across steps in a single replay run."""

    artifact: CapabilityArtifact
    surface: Surface
    inputs: dict[str, str]
    outputs: dict[str, str]
    output_types: dict[str, str]
    policy_mode: Mode
    artifact_approved: bool
    wait_timeout: float
    detectors: list[Detector]
    reclassifications: list[OutcomeReclassification]
    recovery_counts: dict[str, int]
    step_traces: list[StepTrace]
    drift_signals: list[DriftSignal]
    run_id: str
    step_index: int = 0


def replay(
    artifact: CapabilityArtifact,
    surface: Surface,
    inputs: dict[str, str],
    *,
    policy_mode: Mode = Mode.DISCOVERY,
    wait_timeout: float = 10.0,
    run_id: str = "",
    tenant: str = "",
) -> ReplayResult:
    """Execute an artifact's steps against a live Surface.

    ``policy_mode`` is explicit: verification passes ``DISCOVERY`` (attended,
    draft artifact); production passes ``REPLAY``.

    ``tenant`` applies per-tenant overrides *after* the approval check.
    The stored hash covers the base steps plus the ``overrides`` block;
    applying patches mutates step names, so the check must run on the
    unpatched artifact.
    """
    # Pre-replay approval check: refuse if an approved artifact's hash is stale
    if policy_mode == Mode.REPLAY:
        ok, reason = check_approval(artifact)
        if not ok:
            return ReplayFailure(
                step_id="(pre-replay)",
                step_intent="validate artifact approval",
                expected="approved artifact with valid content hash",
                observed=reason,
                locator_rung="",
                error=f"approval check failed: {reason}",
            )

    # Whether policy may allow a `guarded_write` on this run.
    #
    # Deliberately the *status*, not `check_approval()`'s boolean.  That
    # returns True for a draft — it only enforces hash validity on artifacts
    # claiming to be approved — so threading it here would authorise every
    # draft's writes while looking like a fix.  Do not "simplify" this to
    # reuse `ok` above.
    artifact_approved = artifact.approval.status == "approved"

    effective = artifact.apply_overrides(tenant) if tenant else artifact

    state = _ReplayState(
        artifact=effective,
        surface=surface,
        inputs=inputs,
        outputs={},
        output_types={o.name: o.type for o in artifact.outputs},
        policy_mode=policy_mode,
        artifact_approved=artifact_approved,
        wait_timeout=wait_timeout,
        detectors=build_detectors(artifact),
        reclassifications=build_reclassifications(artifact),
        recovery_counts={},
        step_traces=[],
        drift_signals=[],
        run_id=run_id,
    )
    return _run_from(state, start_index=0)


def resume(
    escalated: ReplayEscalated,
    surface: Surface,
) -> ReplayResult:
    """Resume a replay after human intervention.

    Called by the orchestrator after the human finishes.  Reacquires
    ownership, re-validates the current step, then continues.

    The ``escalated`` result carries the engine state internally —
    the caller passes the ``ReplayEscalated`` back without inspecting it.
    The resume token is opaque.
    """
    state: _ReplayState = escalated._engine_state  # type: ignore[assignment]
    if state is None:
        raise ValueError("Cannot resume: no engine state attached")
    surface.reacquire()
    state.surface = surface

    step = state.artifact.steps[state.step_index]

    # Re-validate: can we locate the step's target?
    # The step hasn't been executed yet — the engine escalated before
    # acting — so we check the target (what we need to act on), not
    # the wait_for (what appears after the action).
    obs = surface.observe()
    try:
        locate(step.target, obs, step.step_id)
    except LocateError as e:
        return ReplayFailure(
            step_id=step.step_id,
            step_intent=step.intent,
            expected=f'{step.target.strategy} {step.target.role} "{step.target.name}"',
            observed=f"page at {obs.url} after resume",
            locator_rung=e.strategy,
            error=(
                f"Resume validation failed at step '{step.step_id}': "
                f"{e}. The operator should navigate to the screen for: "
                f"{step.intent}"
            ),
            steps=list(state.step_traces),
        )

    return _run_from(state, start_index=state.step_index)


def _run_from(state: _ReplayState, *, start_index: int) -> ReplayResult:
    """Execute steps from *start_index* onward."""
    for i in range(start_index, len(state.artifact.steps)):
        step = state.artifact.steps[i]
        state.step_index = i

        t0 = time.monotonic()
        result = _execute_step(
            step, state.surface, state.inputs, state.outputs,
            state.output_types, state.policy_mode, state.artifact_approved,
            state.wait_timeout,
            state.detectors, state.reclassifications, state.recovery_counts,
            state.step_traces, state.drift_signals,
            state,
        )
        elapsed = (time.monotonic() - t0) * 1000

        if isinstance(result, StepTrace):
            result.elapsed_ms = elapsed
            state.step_traces.append(result)
            continue

        if isinstance(result, ReplayEscalated):
            result.steps = list(state.step_traces)
            return result
        if isinstance(result, ReplayBusinessOutcome):
            result.steps = list(state.step_traces)
            return result
        if isinstance(result, ReplayFailure):
            result.steps = list(state.step_traces)
            return result

    # Every declared output must be filled
    for out in state.artifact.outputs:
        if out.name not in state.outputs:
            return ReplayFailure(
                step_id="(post-replay)",
                step_intent="collect all declared outputs",
                expected=f"output {out.name!r} filled",
                observed=f"missing from outputs: {list(state.outputs.keys())}",
                locator_rung="",
                error=f"output {out.name!r} was never filled",
                steps=state.step_traces,
            )

    return ReplaySuccess(
        outputs=state.outputs,
        drift_signals=state.drift_signals,
        steps=state.step_traces,
    )


# --------------------------------------------------------------------------- #
# Per-step execution
# --------------------------------------------------------------------------- #


_StepResult = StepTrace | ReplayEscalated | ReplayBusinessOutcome | ReplayFailure


def _execute_step(
    step: Step,
    surface: Surface,
    inputs: dict[str, str],
    outputs: dict[str, str],
    output_types: dict[str, str],
    policy_mode: Mode,
    artifact_approved: bool,
    wait_timeout: float,
    detectors: list[Detector],
    reclassifications: list[OutcomeReclassification],
    recovery_counts: dict[str, int],
    completed_traces: list[StepTrace],
    drift_signals: list[DriftSignal],
    replay_state: _ReplayState | None = None,
) -> _StepResult:
    """Run one step. Returns a StepTrace on success, or an early-exit result."""
    try:
        return _execute_step_inner(
            step, surface, inputs, outputs, output_types,
            policy_mode, artifact_approved, wait_timeout,
            detectors, reclassifications, recovery_counts,
            completed_traces, drift_signals,
            replay_state,
        )
    except Exception as exc:
        return ReplayFailure(
            step_id=step.step_id,
            step_intent=step.intent,
            expected="step completes without exception",
            observed=f"{type(exc).__name__}: {exc}",
            locator_rung="",
            error=f"unexpected: {type(exc).__name__}: {exc}",
        )


def _execute_step_inner(
    step: Step,
    surface: Surface,
    inputs: dict[str, str],
    outputs: dict[str, str],
    output_types: dict[str, str],
    policy_mode: Mode,
    artifact_approved: bool,
    wait_timeout: float,
    detectors: list[Detector],
    reclassifications: list[OutcomeReclassification],
    recovery_counts: dict[str, int],
    completed_traces: list[StepTrace],
    drift_signals: list[DriftSignal],
    replay_state: _ReplayState | None = None,
) -> _StepResult:
    # 1. Observe
    obs = surface.observe()
    step_recoveries: list[RecoveryTrace] = []

    # 2. Pre-locate detection
    hit = run_detectors(detectors, obs)
    if hit is not None:
        result = _handle_detection(
            hit, step, surface, obs, detectors, recovery_counts,
            step_recoveries, replay_state,
        )
        if result is not None:
            return result
        # Recovery succeeded — re-observe
        obs = surface.observe()

    # 3. Locate
    ref: str
    try:
        ref = locate(step.target, obs, step.step_id)
    except LocateError as e:
        # Try reclassification with positive evidence
        rc_hit = try_reclassify(e, step.step_id, obs, reclassifications)
        if rc_hit is not None:
            return ReplayBusinessOutcome(
                outcome_id=rc_hit.outcome_id or "",
                condition=rc_hit.condition or rc_hit.message,
                message=rc_hit.message,
                caller_action=rc_hit.caller_action or "return",
                at_step=step.step_id,
            )
        return ReplayFailure(
            step_id=step.step_id,
            step_intent=step.intent,
            expected=f'{step.target.strategy} {step.target.role} "{step.target.name}"',
            observed=str(e),
            locator_rung=e.strategy,
            error=str(e),
        )

    # 4. Build action — engine resolves inputs, passes text directly
    action = _build_action(step, ref, inputs)

    # 5. Policy check
    node = next((n for n in obs.nodes if n.ref == ref), None)
    # `artifact_approved` is what makes the `guarded_write` tier reachable.
    # Omitting it left the parameter at its `False` default on every call, so
    # no approved artifact could ever complete a write on the replay path.
    verdict = policy_check(
        action, node, obs,
        mode=policy_mode, artifact_approved=artifact_approved,
    )
    if not verdict.allowed:
        return ReplayFailure(
            step_id=step.step_id,
            step_intent=step.intent,
            expected="policy allows action",
            observed=f"blocked: {verdict.reason}",
            locator_rung=step.target.strategy,
            error=f"policy blocked: {verdict.reason}",
        )

    # 6. Act
    result = surface.act(action)
    if not result.ok:
        return ReplayFailure(
            step_id=step.step_id,
            step_intent=step.intent,
            expected="action succeeds",
            observed=f"{result.reason}: {result.detail}",
            locator_rung=step.target.strategy,
            error=f"act failed: {result.reason}: {result.detail}",
        )

    # 7. Collect output + type coercion
    if step.action == "read" and result.detail is not None:
        out_name = step.value.output_name if step.value else None
        if out_name:
            raw_value = result.detail
            out_type = output_types.get(out_name, "text")
            coercion_err = _coerce_output(raw_value, out_type)
            if coercion_err:
                return ReplayFailure(
                    step_id=step.step_id,
                    step_intent=step.intent,
                    expected=f"value coerces to type '{out_type}'",
                    observed=f"raw value: {raw_value!r}",
                    locator_rung=step.target.strategy,
                    error=f"{step.step_id}: {raw_value!r} is not valid {out_type} ({coercion_err})",
                )
            outputs[out_name] = raw_value

    # 8. Wait for condition (no time.sleep — condition-based polling)
    if step.wait_for is not None:
        deadline = time.monotonic() + wait_timeout
        found = False
        while time.monotonic() < deadline:
            wait_obs = surface.observe()
            for n in wait_obs.nodes:
                if (match_frame(step.wait_for.frame, n.frame)
                        and n.role == step.wait_for.role
                        and n.name == step.wait_for.name):
                    found = True
                    break
            if found:
                break
        if not found:
            # Post-wait detection (correction B): check for conditions that
            # appeared AFTER the action — turns "timed out" into "app_error".
            post_obs = surface.observe()
            post_hit = run_detectors(detectors, post_obs)
            if post_hit is not None:
                post_result = _handle_detection(
                    post_hit, step, surface, post_obs, detectors, recovery_counts,
                    step_recoveries, replay_state,
                )
                if post_result is not None:
                    return post_result
                # Recovery succeeded — retry wait? No, just report timeout.
                # The recovery cleared the condition but we still missed the
                # expected element. Fall through to timeout error.

            return ReplayFailure(
                step_id=step.step_id,
                step_intent=step.intent,
                expected=(
                    f'{step.wait_for.role} "{step.wait_for.name}"'
                ),
                observed=f"not found after {wait_timeout}s",
                locator_rung=step.target.strategy,
                error=(
                    f'wait timed out: expected {step.wait_for.role} '
                    f'"{step.wait_for.name}" after {wait_timeout}s'
                ),
            )

    return StepTrace(
        step_id=step.step_id,
        ref_used=ref,
        locator_strategy=step.target.strategy,
        recoveries=step_recoveries,
    )


# --------------------------------------------------------------------------- #
# Detection handling
# --------------------------------------------------------------------------- #


def _handle_detection(
    hit: DetectorHit,
    step: Step,
    surface: Surface,
    obs: Observation,
    detectors: list[Detector],
    recovery_counts: dict[str, int],
    step_recoveries: list[RecoveryTrace] | None = None,
    replay_state: _ReplayState | None = None,
) -> ReplayEscalated | ReplayBusinessOutcome | ReplayFailure | None:
    """Act on a detector hit. Returns an early-exit result, or None if recovered."""
    if hit.kind == "business_outcome":
        # `condition` is what was detected, `message` is what it means to the
        # caller.  They came from the same string until the console showed
        # that "detected: MEMBER_NOT_FOUND" is not an answer to anything.
        return ReplayBusinessOutcome(
            outcome_id=hit.outcome_id or hit.detector_id,
            condition=hit.condition or hit.message,
            message=hit.message,
            caller_action=hit.caller_action or "return",
            at_step=step.step_id,
        )

    if hit.kind == "hard_failure":
        return ReplayFailure(
            step_id=step.step_id,
            step_intent=step.intent,
            expected="no hard failure condition",
            observed=hit.message,
            locator_rung="",
            error=f"hard failure detected: {hit.detector_id}",
        )

    if hit.kind == "recoverable":
        action = hit.recovery_action or "escalate"

        if action == "escalate":
            # Capture evidence and build a real intervention request
            screenshot_path: str | None = None
            try:
                ev = surface.evidence("escalation")
                screenshot_path = str(ev.path)
            except Exception:
                pass

            cap_id = ""
            run_id = ""
            inputs: dict[str, str] = {}
            if replay_state is not None:
                cap_id = replay_state.artifact.capability_id
                run_id = replay_state.run_id
                inputs = replay_state.inputs

            request = build_intervention_request(
                run_id=run_id,
                capability_id=cap_id,
                step_id=step.step_id,
                step_intent=step.intent,
                reason=hit.message,
                inputs=inputs,
                detector_id=hit.detector_id,
                screenshot_path=screenshot_path,
                current_url=obs.url,
            )

            surface.release()

            token = f"{run_id}:{step.step_id}:{step.ordinal}"
            return ReplayEscalated(
                request=request,
                resume_token=token,
                _engine_state=replay_state,
            )

        count = recovery_counts.get(hit.detector_id, 0)
        if count >= hit.max_attempts:
            return ReplayFailure(
                step_id=step.step_id,
                step_intent=step.intent,
                expected="recovery succeeds",
                observed=f"{hit.detector_id} after {count} recovery attempts",
                locator_rung="",
                error=f"recoverable exhausted: {hit.detector_id} ({count} attempts)",
            )

        recovery_counts[hit.detector_id] = count + 1
        trace = RecoveryTrace(
            detector_id=hit.detector_id,
            recovery_action=action,
            attempt=count + 1,
        )

        if action == "dismiss":
            # Click the declared recovery target from the artifact
            target_role = hit.recovery_target_role
            target_name = hit.recovery_target_name
            if target_role and target_name:
                for n in obs.nodes:
                    if n.role == target_role and n.name == target_name:
                        surface.act(Click(ref=n.ref))
                        if step_recoveries is not None:
                            step_recoveries.append(trace)
                        return None  # recovered, caller re-observes

        if action == "retry":
            # Reload the current page to clear transient conditions.
            # Known limit: for a POST that returned a transient error,
            # Navigate re-requests the result URL (a GET) — it does not
            # re-submit the form.  Not a problem for read capabilities;
            # write capabilities need a POST-aware retry (task 8+).
            surface.act(Navigate(url=obs.url))
            if step_recoveries is not None:
                step_recoveries.append(trace)
            return None

    return None


# --------------------------------------------------------------------------- #
# Type coercion
# --------------------------------------------------------------------------- #


def _coerce_output(value: str, output_type: str) -> str | None:
    """Validate that *value* coerces to *output_type*. Returns error or None."""
    if output_type == "money":
        from db.money import display_to_cents
        try:
            display_to_cents(value)
            return None
        except (ValueError, KeyError):
            return f"cannot parse as money: {value!r}"
    # "text" and everything else: pass through
    return None


# --------------------------------------------------------------------------- #
# Action builder
# --------------------------------------------------------------------------- #


def _build_action(step: Step, ref: str, inputs: dict[str, str]):
    """Build a typed Action from a Step + resolved ref."""
    if step.action == "click":
        return Click(ref=ref)
    if step.action == "type":
        if step.value and step.value.source == "input":
            return Type(ref=ref, text=inputs.get(step.value.input_name or "", ""))
        if step.value and step.value.source == "literal":
            return Type(ref=ref, text=step.value.literal or "")
        return Type(ref=ref, text="")
    if step.action == "select":
        if step.value and step.value.source == "input":
            return Select(ref=ref, option=inputs.get(step.value.input_name or "", ""))
        if step.value and step.value.source == "literal":
            return Select(ref=ref, option=step.value.literal or "")
        return Select(ref=ref, option="")
    if step.action == "read":
        out_name = step.value.output_name if step.value else "unknown"
        return Read(ref=ref, into_output=out_name or "unknown")
    raise ValueError(f"unknown action: {step.action}")
