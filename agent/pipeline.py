"""Discovery pipeline: loop → compile → verify.

Extracted from ``scripts/run_discover.py`` so the CLI and the operator
console run the same code rather than two implementations that drift.

**No browser and no writing.**  Sign-in, ``sync_playwright()`` and the
surface factory stay with the callers, because ``agent/`` must never import
Playwright (agent-rules boundary 3).  Writing the artifact stays with them
too: whether a compiled-but-unverified artifact is kept is a policy
decision, not a pipeline one, and the two callers may reasonably differ.

``on_phase`` exists because the console renders each gate as it is reached.
It is a reporting hook — it cannot change what the pipeline does, and the
CLI passes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent.compile import CompilationError, compile_transcript
from agent.loop import DiscoveryLoop, DiscoveryResult
from artifacts.schema import CapabilityArtifact
from replay.verify import VerifyResult, verify_artifact
from surface.base import Surface


# --------------------------------------------------------------------------- #
# Phases — what the console draws as the artifact crosses each gate
# --------------------------------------------------------------------------- #

PHASE_DISCOVERING = "discovering"
PHASE_COMPILING = "compiling"
PHASE_VERIFYING = "verifying"
PHASE_WRITING = "writing"
PHASE_DONE = "done"
PHASE_FAILED = "failed"


@dataclass
class Phase:
    """One line on the pipeline display."""

    name: str
    detail: str = ""
    ok: bool = True


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #


@dataclass
class PipelineResult:
    """Everything both callers need to report and decide on.

    ``artifact`` may be present while ``verified`` is False — compiled but
    not proven.  The caller decides what that is worth; the pipeline does
    not throw it away.
    """

    result: DiscoveryResult
    artifact: CapabilityArtifact | None = None
    compile_error: str | None = None
    verify_result: VerifyResult | None = None
    verified: bool = False
    verify_runs: int = 0
    phases: list[Phase] = field(default_factory=list)

    @property
    def loop_succeeded(self) -> bool:
        return self.result.status == "done"

    @property
    def failure_reason(self) -> str | None:
        """One sentence naming which gate refused, or None on full success.

        Each of these is a real outcome, not an error path — the gates
        exist to produce them.
        """
        if not self.loop_succeeded:
            return f"the model stopped with status {self.result.status!r}"
        if self.compile_error:
            return f"compilation failed: {self.compile_error}"
        if self.verify_result is not None and not self.verify_result.passed:
            return f"verification failed: {self.verify_result.failure_summary}"
        if self.artifact is not None and not self.verified:
            return (
                "verification was skipped (0 runs requested), so nothing "
                "proved the artifact replays"
            )
        return None

    def summary(self, *, run_id: str, model: str, goal: str,
                artifact_path: str | None) -> dict:
        """The evidence summary, in the shape ``evidence/*/summary.json``
        already uses.  Kept here so both callers write the same thing."""
        return {
            "run_id": run_id,
            "model": model,
            "goal": goal,
            "status": self.result.status,
            "steps": self.result.steps,
            "prompt_tokens": self.result.prompt_tokens,
            "output_tokens": self.result.output_tokens,
            "seconds": self.result.wall_seconds,
            "outputs_filled": self.result.outputs_filled,
            "compiled": self.artifact is not None,
            "compile_error": self.compile_error,
            "verified": self.verified,
            "verify_runs": self.verify_runs if self.artifact else 0,
            "artifact_path": artifact_path,
        }


# --------------------------------------------------------------------------- #
# The pipeline
# --------------------------------------------------------------------------- #


def discover_and_verify(
    surface: Surface,
    brain: Any,
    *,
    goal: str,
    inputs: dict[str, str],
    outputs: dict[str, str],
    capability_id: str,
    run_id: str,
    model: str,
    evidence_dir: Path,
    entry_url: str,
    verify_runs: int = 2,
    verify_also: list[dict[str, str]] | None = None,
    surface_factory: Callable[[], Surface] | None = None,
    wait_timeout: float = 10.0,
    on_phase: Callable[[Phase], None] | None = None,
    on_compiled: Callable[[CapabilityArtifact], None] | None = None,
) -> PipelineResult:
    """Run discovery, compile the transcript, verify the artifact.

    ``brain`` is typed ``Any`` deliberately: annotating it ``GeminiBrain``
    would make this module import the SDK, and a stub brain in a test would
    then be a lie about the signature.  What is required is ``configure()``
    and ``decide()``.

    ``on_compiled`` is a seam between the compile and verify gates, for the
    CLI's ``--inject-break``: the gate evidence needs a deliberately broken
    artifact to prove verification catches it.  It is the only way to
    mutate what verification sees, and it is named so that reading the
    signature tells you such a thing exists.
    """

    def phase(name: str, detail: str = "", ok: bool = True) -> Phase:
        p = Phase(name=name, detail=detail, ok=ok)
        if on_phase is not None:
            on_phase(p)
        return p

    phases: list[Phase] = []

    # ---- 1. Discover ----
    phases.append(phase(PHASE_DISCOVERING, goal))
    loop = DiscoveryLoop(
        surface=surface,
        brain=brain,
        goal=goal,
        inputs=inputs,
        outputs=outputs,
        evidence_dir=evidence_dir,
        entry_url=entry_url,
    )
    result = loop.run()

    return compile_and_verify(
        result,
        goal=goal,
        inputs=inputs,
        outputs=outputs,
        capability_id=capability_id,
        run_id=run_id,
        model=model,
        verify_runs=verify_runs,
        verify_also=verify_also,
        surface_factory=surface_factory,
        wait_timeout=wait_timeout,
        on_phase=on_phase,
        on_compiled=on_compiled,
        phases=phases,
    )


def compile_and_verify(
    result: DiscoveryResult,
    *,
    goal: str,
    inputs: dict[str, str],
    outputs: dict[str, str],
    capability_id: str,
    run_id: str,
    model: str,
    verify_runs: int = 2,
    verify_also: list[dict[str, str]] | None = None,
    surface_factory: Callable[[], Surface] | None = None,
    wait_timeout: float = 10.0,
    on_phase: Callable[[Phase], None] | None = None,
    on_compiled: Callable[[CapabilityArtifact], None] | None = None,
    phases: list[Phase] | None = None,
) -> PipelineResult:
    """Compile and verify a finished discovery transcript.

    Split from ``discover_and_verify`` so the console can pause the loop
    for a human, then run the same gates once the model calls ``done``.
    """

    def phase(name: str, detail: str = "", ok: bool = True) -> Phase:
        p = Phase(name=name, detail=detail, ok=ok)
        if on_phase is not None:
            on_phase(p)
        return p

    if phases is None:
        phases = []
    out = PipelineResult(result=result, verify_runs=verify_runs, phases=phases)

    if not out.loop_succeeded:
        phases.append(phase(
            PHASE_FAILED,
            f"the model stopped with status {result.status!r}",
            ok=False,
        ))
        return out

    # ---- 2. Compile (prune runs inside, as pass 1) ----
    phases.append(phase(PHASE_COMPILING, capability_id))
    try:
        out.artifact = compile_transcript(
            result.transcript, result,
            capability_id=capability_id,
            inputs=inputs,
            outputs=outputs,
            description=goal,
            run_id=run_id,
            model=model,
        )
    except CompilationError as exc:
        out.compile_error = str(exc)
        phases.append(phase(PHASE_FAILED, str(exc), ok=False))
        return out

    if on_compiled is not None:
        on_compiled(out.artifact)

    # ---- 3. Verify ----
    if verify_runs > 0 and surface_factory is not None:
        phases.append(phase(
            PHASE_VERIFYING, f"{verify_runs} run{'' if verify_runs == 1 else 's'}"
        ))
        vr = verify_artifact(
            out.artifact, surface_factory, inputs,
            expected_outputs=result.outputs_filled,
            n=verify_runs, wait_timeout=wait_timeout,
            also=verify_also,
        )
        out.verify_result = vr
        out.verified = vr.passed
        if not vr.passed:
            phases.append(phase(PHASE_FAILED, vr.failure_summary or "", ok=False))
            return out
        phases.append(phase(
            PHASE_DONE, f"verified {verify_runs}/{verify_runs} — outputs matched"
        ))
    else:
        phases.append(phase(
            PHASE_DONE, "verification skipped (0 runs requested)", ok=False
        ))

    return out
