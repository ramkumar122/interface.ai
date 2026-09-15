"""Running a capability from the console: one worker thread, one browser.

Two constraints shape this, and neither is negotiable:

1. Playwright's sync API cannot run inside an asyncio event loop, and
   uvicorn is one.  FastAPI hands `def` handlers to a threadpool, but a
   Playwright object is bound to the thread that created it, so a browser
   built in one request's worker is unusable from the next.

2. A run outlives a request.  Escalation returns `ReplayEscalated`, the
   human acts in the browser window, and `resume()` continues on the *same*
   page.  A browser torn down with the response cannot support that.

So the console owns one long-lived worker thread holding the Playwright
instance, browser, page and surface.  Routes post a job and return; the
status page polls.  One run at a time — the same single-concurrency limit
task 8 already documented for escalation, made explicit here rather than
discovered when two runs fight over one page.

Nothing here decides anything about replay.  The engine is called exactly
as `scripts/replay_evidence.py` calls it.
"""

from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from artifacts.schema import CapabilityArtifact
from control.policy import Mode
from surface.base import (
    Action,
    ActionResult,
    EvidenceRef,
    Observation,
    Surface,
)

TARGET_ORIGIN = os.environ.get("OPERATOR_TARGET", "http://127.0.0.1:8001")
COREDESK_USER = os.environ.get("COREDESK_USER", "mreyes")
COREDESK_PASS = os.environ.get("COREDESK_PASS", "demo1234")
SURFACE_CONFIG = Path("surfaces/coredesk.yaml")
EVIDENCE_ROOT = Path(os.environ.get("OPERATOR_EVIDENCE_DIR", "evidence"))


def evidence_root() -> Path:
    """Where run evidence is written, read at call time.

    `evidence/` is a graded location and the tests drive real runs through
    this module, so the destination has to be redirectable. A constant
    captured at import is not.
    """
    return EVIDENCE_ROOT


# --------------------------------------------------------------------------- #
# Progress — a recorder on a seam that already exists
# --------------------------------------------------------------------------- #


class ProgressSurface:
    """Forwards every `Surface` call unchanged and records what went by.

    A decorator, not a participant: it adds no behaviour, makes no
    decisions, and the engine cannot tell it is there.  It exists because
    `replay()` returns only at the end, and a status page that shows
    nothing until then shows nothing at all.

    It deliberately does **not** publish a step number.  Each step issues
    one `act()`, but so does each recovery — a dismiss click, a retry
    navigation — so "step 3 of 5" would be right most of the time and
    wrong exactly when something interesting happened.  Actions dispatched
    is a fact.  The exact per-step record arrives at the end, from
    `StepTrace`.
    """

    def __init__(self, inner: Surface) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self.actions = 0
        self.observations = 0
        self.current_url = ""

    # -- Surface protocol, all pass-through ---------------------------------- #

    def observe(self, screenshot: bool = False) -> Observation:
        obs = self._inner.observe(screenshot=screenshot)
        with self._lock:
            self.observations += 1
            self.current_url = obs.url
        return obs

    def act(self, action: Action, *, as_human: bool = False) -> ActionResult:
        with self._lock:
            self.actions += 1
        return self._inner.act(action, as_human=as_human)

    def evidence(self, label: str) -> EvidenceRef:
        return self._inner.evidence(label)

    def release(self) -> None:
        self._inner.release()

    def reacquire(self) -> None:
        self._inner.reacquire()

    # -- what the status page reads ------------------------------------------ #

    @property
    def owner(self) -> str:
        """Ownership as the surface itself holds it.

        `WebSurface` keeps this as a plain string rather than using
        `control.session_owner.SessionOwner` (REPORT §7).  The console reads
        the value `act()` actually checks; a second `SessionOwner` here
        could disagree with the one enforcing the lock, which is worse than
        reaching for one attribute.
        """
        return getattr(self._inner, "_owner", "NONE")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "actions": self.actions,
                "observations": self.observations,
                "current_url": self.current_url,
                "owner": self.owner,
            }


# --------------------------------------------------------------------------- #
# Run state
# --------------------------------------------------------------------------- #


@dataclass
class RunState:
    """Everything the status page needs, and the handle `resume()` needs.

    In memory only.  Restarting the console loses an in-flight escalation
    and the ability to resume it — stated in the plan's known limits rather
    than papered over with a pickle.
    """

    run_id: str
    capability_key: str
    capability_id: str
    inputs: dict[str, str]
    policy_mode: str
    artifact: CapabilityArtifact | None
    watch: bool = False
    # "replay" invokes an existing capability; "discovery" makes one.  Both
    # hold the same browser through the same worker, so they are one job
    # type with a discriminator rather than two runners.
    kind: str = "replay"
    discovery: Any = None  # DiscoveryState, when kind == "discovery"

    phase: str = "starting"  # starting | running | escalated | finished | error
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None
    result: Any = None  # ReplayResult
    error: str | None = None
    progress: ProgressSurface | None = None
    escalated_at_step: str | None = None
    pending_human: bool = False

    @property
    def elapsed_s(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return round(end - self.started_at, 1)

    @property
    def in_flight(self) -> bool:
        """Is the engine working right now?  Drives the status page's poll.

        An escalated run is deliberately not in flight: it is waiting for a
        person, and re-rendering the instructions every second while they
        read them is hostile.
        """
        return self.phase in ("starting", "running")

    @property
    def holds_browser(self) -> bool:
        """Does this run still own the window?

        Not the same question as `in_flight`.  An escalated run is idle and
        still holds the browser — a human is typing in it.  Starting a
        second run then would open a page beside the one they are working
        in, and the resume would validate against the wrong window.
        """
        return self.phase in ("starting", "running", "escalated", "paused")

    @property
    def step_count(self) -> int:
        return len(self.artifact.steps) if self.artifact else 0

    @property
    def actions_dispatched(self) -> int:
        return self.progress.actions if self.progress else 0

    @property
    def current_url(self) -> str:
        return self.progress.current_url if self.progress else ""

    @property
    def owner(self) -> str:
        """`NONE` once the run is over: the engine has let the browser go."""
        if self.progress is None:
            return "NONE"
        if self.phase in ("escalated", "paused"):
            return self.progress.owner
        if self.phase in ("finished", "error"):
            return "NONE"
        return self.progress.owner

    def step_traces(self) -> list:
        """Completed steps, whatever the outcome.  Every result type carries
        them, so the log renders the same way on success and failure."""
        if self.result is None:
            return []
        return list(getattr(self.result, "steps", []))

    @property
    def is_discovery(self) -> bool:
        return self.kind == "discovery"


# --------------------------------------------------------------------------- #
# The worker
# --------------------------------------------------------------------------- #


class RunnerBusy(RuntimeError):
    """A run is already in flight.  One browser, one page, one run."""


class Runner:
    """Owns the worker thread and the single run slot.

    Jobs are closures posted to a queue; the worker runs them in the thread
    that created the browser.  The public methods return immediately — a
    route must never block on a browser.
    """

    def __init__(self, browser_factory: Callable[[], Any] | None = None) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._runs: dict[str, RunState] = {}
        self._active: str | None = None
        self._browser_factory = browser_factory
        self._browser_ctx: Any = None

    # -- lifecycle ----------------------------------------------------------- #

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._work, name="operator-runner", daemon=True
            )
            self._thread.start()

    def _work(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                job()
            except Exception as exc:  # a worker that dies takes every run with it
                run_id = getattr(job, "run_id", None)
                state = self._runs.get(run_id) if run_id else None
                if state is not None:
                    state.phase = "error"
                    state.error = f"{type(exc).__name__}: {exc}"
                    state.finished_at = time.monotonic()
                    # Mirror onto the discovery's own state.  The status
                    # page reads `DiscoveryState`, not this one, so leaving
                    # it unset left two objects disagreeing about a single
                    # failure: the real exception sat here while the page
                    # fell back to "the run ended before the pipeline
                    # reported" — vaguer than what the console already knew.
                    ds = state.discovery
                    if ds is not None and not ds.error:
                        ds.error = state.error
                with self._lock:
                    self._active = None

    # -- queries -------------------------------------------------------------- #

    def get(self, run_id: str) -> RunState | None:
        return self._runs.get(run_id)

    def active(self) -> RunState | None:
        with self._lock:
            return self._runs.get(self._active) if self._active else None

    def busy(self) -> bool:
        state = self.active()
        return state is not None and state.holds_browser

    # -- starting a run -------------------------------------------------------- #

    def start(
        self,
        artifact: CapabilityArtifact,
        capability_key: str,
        inputs: dict[str, str],
        *,
        policy_mode: Mode,
        watch: bool = False,
        tenant: str = "",
    ) -> RunState:
        """Queue a replay.  Returns immediately with the run's state."""
        with self._lock:
            active = self._runs.get(self._active) if self._active else None
            if active is not None and active.holds_browser:
                detail = (
                    "is waiting for a person to finish in the browser window"
                    if active.phase == "escalated"
                    else f"is still in flight ({active.phase})"
                )
                raise RunnerBusy(
                    f"Run {active.run_id} {detail}. The console drives one "
                    f"browser window, so it runs one capability at a time."
                )

            run_id = f"operator-{uuid.uuid4().hex[:8]}"
            state = RunState(
                run_id=run_id,
                capability_key=capability_key,
                capability_id=artifact.capability_id,
                inputs=dict(inputs),
                policy_mode=policy_mode.value,
                artifact=artifact,
                watch=watch,
            )
            self._runs[run_id] = state
            self._active = run_id

        self._ensure_thread()
        job = lambda: self._do_run(state, policy_mode, tenant)  # noqa: E731
        job.run_id = run_id  # type: ignore[attr-defined]
        self._jobs.put(job)
        return state

    def start_discovery(self, discovery_state: Any, *, entry_url: str) -> RunState:
        """Queue a discovery.  Same slot, same browser, same busy rules.

        Discovery holds the browser exactly as a replay does, so
        `holds_browser` already covers it and a replay started meanwhile is
        refused by the message that exists.
        """
        with self._lock:
            active = self._runs.get(self._active) if self._active else None
            if active is not None and active.holds_browser:
                raise RunnerBusy(
                    f"Run {active.run_id} is still in flight "
                    f"({active.phase}). The console drives one browser "
                    f"window, so it runs one job at a time."
                )

            run_id = discovery_state.evidence_dir.name
            state = RunState(
                run_id=run_id,
                capability_key=discovery_state.capability_id,
                capability_id=discovery_state.capability_id,
                inputs=dict(discovery_state.inputs),
                policy_mode=Mode.DISCOVERY.value,
                artifact=None,
                kind="discovery",
                discovery=discovery_state,
            )
            self._runs[run_id] = state
            self._active = run_id

        self._ensure_thread()
        job = lambda: self._do_discovery(state, entry_url)  # noqa: E731
        job.run_id = run_id  # type: ignore[attr-defined]
        self._jobs.put(job)
        return state

    def _do_discovery(self, state: RunState, entry_url: str) -> None:
        from operator_console import discovery as discovery_mod

        state.phase = "running"
        ds = state.discovery
        ds.evidence_dir.mkdir(parents=True, exist_ok=True)

        surface = self._build_surface(state, ds.evidence_dir, entry_url=entry_url)
        state.progress = surface

        def factory():
            return self._build_surface(state, ds.evidence_dir, entry_url=entry_url)

        discovery_mod.run_discovery(
            ds, surface=surface, surface_factory=factory,
            run_id=state.run_id, entry_url=entry_url,
        )

        if ds.loop is not None and ds.loop.paused:
            state.phase = "paused"
            return

        state.finished_at = time.monotonic()
        state.phase = "error" if ds.error else "finished"
        with self._lock:
            if self._active == state.run_id:
                self._active = None

    def human_step(self, state: RunState, action: Any) -> RunState:
        """Queue one recorded operator action on a paused discovery."""
        if state.phase != "paused" or not state.is_discovery:
            raise RunnerBusy(
                f"Run {state.run_id} is {state.phase}, not a paused "
                f"discovery — there is no human step to run."
            )
        state.pending_human = True
        self._ensure_thread()
        job = lambda: self._do_human_step(state, action)  # noqa: E731
        job.run_id = state.run_id  # type: ignore[attr-defined]
        self._jobs.put(job)
        return state

    def handoff_model(self, state: RunState) -> RunState:
        """Queue handing a paused discovery back to the model."""
        if state.phase != "paused" or not state.is_discovery:
            raise RunnerBusy(
                f"Run {state.run_id} is {state.phase}, not a paused "
                f"discovery — there is nothing to hand off."
            )
        state.phase = "running"
        state.finished_at = None
        self._ensure_thread()
        job = lambda: self._do_handoff(state)  # noqa: E731
        job.run_id = state.run_id  # type: ignore[attr-defined]
        self._jobs.put(job)
        return state

    def _do_human_step(self, state: RunState, action: Any) -> None:
        from operator_console import discovery as discovery_mod

        ds = state.discovery
        ds.human_error = None
        try:
            rec = ds.loop.human_act(action)
        except Exception as exc:
            ds.human_error = f"{type(exc).__name__}: {exc}"
            state.pending_human = False
            return
        state.pending_human = False
        if rec.action_kind == "done" and not ds.loop.paused:
            discovery_mod.complete_discovery(ds, ds.loop._snapshot())
            state.finished_at = time.monotonic()
            state.phase = "error" if ds.error else "finished"
            with self._lock:
                if self._active == state.run_id:
                    self._active = None

    def _do_handoff(self, state: RunState) -> None:
        from operator_console import discovery as discovery_mod

        ds = state.discovery
        ds.human_error = None
        ds.pause_reason = None
        try:
            result = ds.loop.resume_model()
        except Exception as exc:
            ds.error = f"{type(exc).__name__}: {exc}"
            state.phase = "error"
            state.finished_at = time.monotonic()
            with self._lock:
                if self._active == state.run_id:
                    self._active = None
            return
        if result.status == "paused":
            ds.pause_reason = ds.loop.pause_reason
            state.phase = "paused"
            return
        discovery_mod.complete_discovery(ds, result)
        state.finished_at = time.monotonic()
        state.phase = "error" if ds.error else "finished"
        with self._lock:
            if self._active == state.run_id:
                self._active = None

    def resume(self, state: RunState) -> RunState:
        """Queue a resume from an escalated result."""
        if state.phase != "escalated":
            raise RunnerBusy(
                f"Run {state.run_id} is {state.phase}, not escalated — "
                f"there is nothing to resume."
            )
        with self._lock:
            self._active = state.run_id
        state.phase = "running"
        state.finished_at = None

        self._ensure_thread()
        job = lambda: self._do_resume(state)  # noqa: E731
        job.run_id = state.run_id  # type: ignore[attr-defined]
        self._jobs.put(job)
        return state

    # -- the work itself -------------------------------------------------------- #

    def _do_run(self, state: RunState, policy_mode: Mode, tenant: str) -> None:
        from replay.engine import replay

        state.phase = "running"
        evidence_dir = evidence_root() / state.run_id
        evidence_dir.mkdir(parents=True, exist_ok=True)

        surface = self._build_surface(state, evidence_dir)
        state.progress = surface

        result = replay(
            state.artifact, surface, state.inputs,
            policy_mode=policy_mode,
            wait_timeout=10.0,
            run_id=state.run_id,
            tenant=tenant,
        )
        self._settle(state, result)

    def _do_resume(self, state: RunState) -> None:
        from replay.engine import resume as engine_resume

        # The escalated result carries the engine state.  The console passes
        # it back whole and never reads `resume_token` — the token is opaque
        # by contract, and parsing it would couple the console to a format
        # the engine is free to change.
        result = engine_resume(state.result, state.progress)
        self._settle(state, result)

    def _settle(self, state: RunState, result: Any) -> None:
        state.result = result
        state.finished_at = time.monotonic()
        if result.status == "escalated":
            state.phase = "escalated"
            state.escalated_at_step = result.request.step_id
            # Stay the active run: the browser is held open for the human,
            # and a second run would steal the page they are working in.
        else:
            state.phase = "finished"
            with self._lock:
                if self._active == state.run_id:
                    self._active = None

    # -- browser ------------------------------------------------------------------ #

    def _build_surface(
        self, state: RunState, evidence_dir: Path, *, entry_url: str = ""
    ) -> ProgressSurface:
        """Sign in and hand back an instrumented surface.

        Sign-in is the harness's job, exactly as in
        `scripts/replay_evidence.py`: credentials are filled by DOM id and
        never enter an artifact, an observation, or this console's screens.
        """
        if self._browser_factory is not None:
            inner = self._browser_factory()
            return ProgressSurface(inner)

        from playwright.sync_api import sync_playwright
        from surface.web import WebSurface

        if self._browser_ctx is None:
            pw = sync_playwright().start()
            headless = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes")
            browser = pw.chromium.launch(headless=headless)
            self._browser_ctx = (pw, browser)
        _pw, browser = self._browser_ctx

        page = browser.new_page()
        page.goto(f"{TARGET_ORIGIN}/")
        page.fill("#ctl00_MainContent_txtUserId", COREDESK_USER)
        page.fill("#ctl00_MainContent_txtPassword", COREDESK_PASS)
        page.click("#ctl00_MainContent_btnSignOn")
        page.wait_for_load_state("networkidle")

        inner = WebSurface(
            page, evidence_dir=evidence_dir,
            config_path=SURFACE_CONFIG, inputs=state.inputs,
        )
        page.goto(entry_url or f"{TARGET_ORIGIN}/menu")
        page.wait_for_load_state("networkidle")
        return ProgressSurface(inner)


# One runner per process, because there is one browser window.
RUNNER = Runner()
