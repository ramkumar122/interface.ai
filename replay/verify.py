"""Verification wrapper: N-run replay with output comparison.

String-exact comparison on outputs.  No money normalisation — if a formatting
difference exists between discovery and replay, it fails loudly.  That is
correct: a formatting difference IS nondeterminism.

N-run verification assumes an idempotent capability.  For non-idempotent ones
(e.g. ``card.lock``), either N=1, the factory resets state, or verification
accepts the expected business outcome.  Decided in task 7.

Business outcomes fail verification **unless the artifact declares an
expected-on-repeat outcome** (correction D).  That field is not yet in the
schema — the code is written so adding it is a one-line change, not a refactor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from control.policy import Mode
from replay.engine import replay
from replay.result import (
    ReplayBusinessOutcome,
    ReplayEscalated,
    ReplayFailure,
    ReplayResult,
    ReplaySuccess,
)
from surface.base import Surface

from artifacts.schema import CapabilityArtifact


@dataclass
class VerifyResult:
    """Aggregate of N replay runs."""

    passed: bool
    runs: list[ReplayResult] = field(default_factory=list)
    failure_summary: str | None = None


def verify_artifact(
    artifact: CapabilityArtifact,
    surface_factory: Callable[[], Surface],
    inputs: dict[str, str],
    expected_outputs: dict[str, str],
    *,
    n: int = 2,
    policy_mode: Mode = Mode.DISCOVERY,
    wait_timeout: float = 10.0,
    tenant: str = "",
    also: list[dict[str, str]] | None = None,
) -> VerifyResult:
    """Run the artifact and verify it replays — deterministically, and for
    more than one record.

    Two separate claims, and until now only the first was checked:

    * **Deterministic.**  *n* runs on the same parameters, outputs
      string-equal to *expected_outputs* and identical to each other.
    * **Portable.**  At least one further parameter set in ``also``, each
      run succeeding and filling every declared output.

    Outputs across sets are *not* compared — they differ, which is the
    point.  A capability returning the same balance for two members would
    be the bug, not the proof.

    Verification with identical parameters every time is a determinism
    check wearing a portability check's clothes.  Two artifacts passed this
    gate while being valid only for the record they were recorded against:
    `card.lock` keyed a row by a card id containing the member number, and
    `update_address` waited for a cell named after the city.  Both replayed
    perfectly, twice, with the same inputs.

    A capability declaring **no inputs** has one possible parameter set, so
    the requirement is vacuous and skipped — there is nothing to vary.

    On success, stamps ``artifact.meta.verified_runs = n``.  That field
    means "this replayed cleanly N times with matching outputs" and is
    only set here — never counted from mixed evidence scenarios.

    A ``business_outcome`` or ``failure`` result fails verification.
    Task 7 may add ``expected_on_repeat`` for non-idempotent capabilities —
    that would be checked here with one additional condition.
    """
    runs: list[ReplayResult] = []
    also = [s for s in (also or []) if s]

    # A capability with no declared inputs cannot be run two ways.
    if artifact.inputs and not also:
        return VerifyResult(
            passed=False, runs=[],
            failure_summary=(
                "verification needs a second parameter set: this capability "
                f"declares {[i.name for i in artifact.inputs]}, and running "
                "the same values twice proves determinism, not that the "
                "capability works for more than one record. Supply one with "
                "--verify-inputs-2."
            ),
        )

    for i in range(n):
        surface = surface_factory()
        result = replay(
            artifact, surface, inputs,
            policy_mode=policy_mode,
            wait_timeout=wait_timeout,
            tenant=tenant,
        )
        runs.append(result)

        # Must be a success
        if not isinstance(result, ReplaySuccess):
            if isinstance(result, ReplayBusinessOutcome):
                summary = (
                    f"run {i+1}/{n}: business_outcome "
                    f"{result.outcome_id!r} at step {result.at_step!r}"
                )
            elif isinstance(result, ReplayEscalated):
                summary = (
                    f"run {i+1}/{n}: escalated at step "
                    f"{result.request.step_id!r} — {result.request.reason}"
                )
            elif isinstance(result, ReplayFailure):
                summary = f"run {i+1}/{n} failed: {result.error}"
            else:
                summary = f"run {i+1}/{n}: unexpected result type"
            return VerifyResult(passed=False, runs=runs, failure_summary=summary)

        # Output comparison — string-exact
        for name, expected in expected_outputs.items():
            actual = result.outputs.get(name)
            if actual != expected:
                return VerifyResult(
                    passed=False, runs=runs,
                    failure_summary=(
                        f"run {i+1}/{n}: output {name!r} = {actual!r}, "
                        f"expected {expected!r}"
                    ),
                )

    # Cross-run stability
    if len(runs) > 1:
        assert all(isinstance(r, ReplaySuccess) for r in runs)
        baseline = runs[0].outputs  # type: ignore[union-attr]
        for j, run in enumerate(runs[1:], 2):
            assert isinstance(run, ReplaySuccess)
            if run.outputs != baseline:
                return VerifyResult(
                    passed=False, runs=runs,
                    failure_summary=(
                        f"stability: run 1 outputs {baseline} "
                        f"≠ run {j} outputs {run.outputs}"
                    ),
                )

    # ---- Portability: every further set must succeed and fill outputs ----
    # Not compared against each other or against `expected_outputs` — a
    # different member has a different balance.  What is asserted is that
    # the capability still works.
    for j, params in enumerate(also, 1):
        if params == inputs:
            return VerifyResult(
                passed=False, runs=runs,
                failure_summary=(
                    f"parameter set {j} is identical to the first; it would "
                    f"verify the same values twice and report a portability "
                    f"check that never happened"
                ),
            )
        surface = surface_factory()
        result = replay(
            artifact, surface, params,
            policy_mode=policy_mode, wait_timeout=wait_timeout, tenant=tenant,
        )
        runs.append(result)
        if not isinstance(result, ReplaySuccess):
            detail = getattr(result, "error", None) or getattr(
                result, "message", result.status)
            return VerifyResult(
                passed=False, runs=runs,
                failure_summary=(
                    f"parameter set {j} ({params}) did not replay: {detail}. "
                    f"The capability works for the record it was recorded "
                    f"against and not for this one."
                ),
            )
        for out in artifact.outputs:
            if not result.outputs.get(out.name):
                return VerifyResult(
                    passed=False, runs=runs,
                    failure_summary=(
                        f"parameter set {j}: output {out.name!r} was not "
                        f"filled"
                    ),
                )

    # The total clean runs, across every set.  How many *sets* that spans is
    # not recorded — that needs a schema field, and `meta` has only this one.
    artifact.meta.verified_runs = len(runs)
    return VerifyResult(passed=True, runs=runs)
