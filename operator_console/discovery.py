"""Discovery from the console: the one module here that touches the model.

Every other console module is on the replay path and imports no SDK — a
test enforces that, exempting this file by name the way
`test_only_web_imports_playwright` exempts `surface/web.py`.  The claim
that matters is unchanged: `replay/` never imports the SDK, and neither
does `runner.py`, which both job types share.

The work itself is ``DiscoveryLoop`` with ``pause_on_stuck=True``, then
the same ``compile_and_verify`` gates the CLI uses once the model
finishes.  What lives here is the console's half: the browser, the
harness sign-in, the pause form, and the decision to write.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.pipeline import (
    PHASE_DISCOVERING,
    Phase,
    PipelineResult,
    compile_and_verify,
)
from control.approval import artifact_filename, write_artifact

DEFAULT_MODEL = "gemini-3.6-flash"


def api_key() -> str | None:
    """The key, from the environment or the repo-root `.env`.

    `scripts/run_discover.py` loads `.env` itself; the console is a
    long-lived process started from a shell that may not have it exported,
    so it reads the same file rather than making the two disagree about
    where configuration lives.
    """
    key = os.environ.get("GOOGLE_API_KEY")
    if key:
        return key
    env = Path(".env")
    if not env.is_file():
        return None
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == "GOOGLE_API_KEY":
            return v.strip().strip('"').strip("'") or None
    return None


def model_name() -> str:
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def key_missing_reason() -> str | None:
    """Why the form is disabled, or None when it is usable."""
    if api_key():
        return None
    return (
        "GOOGLE_API_KEY is not set. Discovery needs a model; replay does "
        "not. Set it in the environment, or put GOOGLE_API_KEY=... in a "
        ".env file at the repository root."
    )


# --------------------------------------------------------------------------- #
# What the status page reads
# --------------------------------------------------------------------------- #


@dataclass
class DiscoveryState:
    """The console's view of a discovery job.

    The per-turn detail is not here — it is read from the transcript file
    the loop flushes (see `transcript_view`).  This holds what the file
    cannot say: which gate we are at, and what came out the end.
    """

    goal: str
    capability_id: str
    model: str
    inputs: dict[str, str]
    outputs: dict[str, str]
    verify_runs: int
    evidence_dir: Path

    # Further parameter sets, one dict per record. `verify_artifact` refuses
    # a capability with declared inputs unless it gets at least one, and the
    # console used not to pass any — so every discovery of a real capability
    # ran the model, compiled, and was then refused at verification. Defaulted
    # rather than required so the field can be added without breaking every
    # constructor.
    verify_also: list[dict[str, str]] = field(default_factory=list)

    phases: list[Phase] = field(default_factory=list)
    pipeline: PipelineResult | None = None
    artifact_path: str | None = None
    written: bool = False
    error: str | None = None
    loop: Any = None
    pause_reason: str | None = None
    human_error: str | None = None
    entry_url: str = ""
    run_id: str = ""
    surface_factory: Any = None

    @property
    def records_described(self) -> str:
        """The records verification covers, as a reader would say them.

        "member 100101 and member 100110" carries the portability claim;
        two values side by side do not. Built here rather than in the
        template so it can be asserted as an exact string.
        """
        def one(params: dict[str, str]) -> str:
            # `member_no` reads as "member" in a sentence; anything else is
            # named as declared rather than guessed at.
            # " / " binds tighter than the " and " between records, so a
            # multi-input capability does not read as one flat list.
            return " / ".join(
                f"member {v}" if k == "member_no" else f"{k} {v}"
                for k, v in params.items()
            )

        parts = [one(self.inputs)] + [one(p) for p in self.verify_also]
        if len(parts) == 1:
            return parts[0]
        return " and ".join([", ".join(parts[:-1]), parts[-1]])

    @property
    def transcript_path(self) -> Path:
        return self.evidence_dir / "transcript.jsonl"

    @property
    def artifact_key(self) -> str | None:
        """`id@version` for the review link, or None if nothing was written."""
        if self.pipeline is None or self.pipeline.artifact is None:
            return None
        a = self.pipeline.artifact
        return f"{a.capability_id}@{a.version}"

    @property
    def failure_reason(self) -> str | None:
        if self.error:
            return self.error
        if self.pipeline is not None:
            return self.pipeline.failure_reason
        # No pipeline result and no recorded error means the job died before
        # it could report.  Saying so beats rendering the word "None".
        return (
            "the run ended before the pipeline reported — see the console "
            "log and the transcript"
        )

    @property
    def gate_refused(self) -> bool:
        """Did a gate refuse, or did the run fall over before reaching one?

        The console tells the reader that a refusal is the system working
        as designed.  That is only true when the pipeline reached a gate
        and the gate said no — the model gave up, compilation refused,
        verification failed, or nothing proved the artifact replays.  A
        crash reaches no gate at all, and printing "this is a gate
        refusing, not a crash" over the top of one is this console
        asserting something false about itself with total confidence.

        `written` is not the test.  Nothing is written when a gate refuses
        *and* when the browser fails to launch; only one of those is the
        design holding.
        """
        if self.error:
            return False
        return (
            self.pipeline is not None
            and self.pipeline.failure_reason is not None
        )

    @property
    def succeeded(self) -> bool:
        return self.written


# --------------------------------------------------------------------------- #
# Running one
# --------------------------------------------------------------------------- #


def new_run_id(model: str) -> str:
    """Same shape as the CLI's, so evidence sorts together."""
    return f"discover-{model}-{uuid.uuid4().hex[:8]}"


# Roles a human can usefully pick. Landmarks and column headers are
# visible in the headed window; they are not things to click.
_PICKABLE = frozenset({
    "button", "link", "textbox", "searchbox", "combobox",
    "radio", "checkbox", "menuitem", "tab", "cell",
})


def picker_nodes(obs: Any) -> list:
    """Actionable nodes from the current observation, for the pause form."""
    if obs is None:
        return []
    return [n for n in obs.nodes if n.role in _PICKABLE and not n.disabled]


def run_discovery(
    state: DiscoveryState,
    *,
    surface: Any,
    surface_factory: Any,
    run_id: str,
    entry_url: str,
) -> None:
    """Run the loop, pausing for a human when the model is stuck.

    Compile/verify wait until the model (or a human ``done``) finishes.
    The browser and sign-in stay with ``runner.py``.
    """
    from agent.brain import GeminiBrain
    from agent.loop import DiscoveryLoop

    key = api_key()
    if not key:
        state.error = key_missing_reason()
        return

    state.entry_url = entry_url
    state.run_id = run_id
    state.surface_factory = surface_factory

    brain = GeminiBrain(model=state.model, api_key=key)
    loop = DiscoveryLoop(
        surface=surface,
        brain=brain,
        goal=state.goal,
        inputs=state.inputs,
        outputs=state.outputs,
        evidence_dir=state.evidence_dir,
        entry_url=entry_url,
    )
    state.loop = loop
    state.phases.append(Phase(PHASE_DISCOVERING, state.goal))
    result = loop.run(pause_on_stuck=True)
    if result.status == "paused":
        state.pause_reason = loop.pause_reason
        return
    complete_discovery(state, result)


def complete_discovery(state: DiscoveryState, result: Any) -> None:
    """Compile, verify, and maybe write — the same gates the CLI uses."""
    pipe = compile_and_verify(
        result,
        goal=state.goal,
        inputs=state.inputs,
        outputs=state.outputs,
        capability_id=state.capability_id,
        run_id=state.run_id,
        model=state.model,
        verify_runs=state.verify_runs,
        verify_also=state.verify_also,
        surface_factory=state.surface_factory,
        on_phase=state.phases.append,
    )
    state.pipeline = pipe

    if pipe.artifact is not None and pipe.verified:
        path = Path("artifacts") / artifact_filename(pipe.artifact)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_artifact(path, pipe.artifact)
        state.artifact_path = str(path)
        state.written = True

    summary = pipe.summary(
        run_id=state.run_id, model=state.model, goal=state.goal,
        artifact_path=state.artifact_path,
    )
    (state.evidence_dir / "summary.json").write_text(json.dumps(summary, indent=2))
