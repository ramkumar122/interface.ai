"""The discovery loop: observe -> policy-check -> decide -> policy-check -> act -> record.

Does NOT import ``google.genai`` or ``playwright``. Speaks to the model
through ``GeminiBrain`` and to the browser through the ``Surface`` protocol.
Policy is enforced via ``control.policy.check()`` -- never duplicated.

History compaction: full observation for the current turn only; prior turns
are compacted to one line each. Failures keep their detail (self-correction
ladder rung 2); successes are terse. This keeps cumulative input linear.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from control.policy import Mode, Verdict, check as policy_check
from control.redact import redact
from surface.base import (
    Action,
    ActionResult,
    Click,
    Done,
    Escalate,
    GiveUp,
    Navigate,
    Node,
    Observation,
    Read,
    ResolutionTrace,
    Select,
    Surface,
    Type,
)

from agent.brain import BrainResponse, GeminiBrain  # noqa: F401 (re-export)

MAX_STEPS = 25
# Wall-clock guard: prevents runaway loops. Generous because it includes API
# latency which we don't control. The step budget (25) is the real guard
# against flailing.
WALL_CLOCK_LIMIT = 900

RULES = """You are operating a legacy web application through an accessibility
view. Each turn you receive the current page as roles + accessible names, each
line tagged with a [ref=eN] valid for THIS observation only. You must respond
with exactly one function call.

Rules:
- Act only on a ref present in the current observation.
- Use the declared inputs exactly; never invent a value (e.g. a member number).
- When typing a declared input, prefer from_input=<input name> over literal text.
- Read a value by the cell under its column header, not by position.
- You are NOT told the routes or where data lives; navigate by what you see.
- Before calling done, state in the rationale what proves the goal is met.
- If you cannot proceed or the app blocks you, call escalate or give_up."""


# --------------------------------------------------------------------------- #
# Step record -- one per turn, persisted to evidence
# --------------------------------------------------------------------------- #


@dataclass
class StepRecord:
    step: int
    obs_hash: str
    action_kind: str
    action_args: dict
    target_role: str | None
    target_name: str | None
    result_ok: bool
    result_reason: str | None
    result_detail: str | None
    trace: ResolutionTrace | None
    verdict_allowed: bool
    verdict_risk: str
    prompt_tokens: int
    output_tokens: int
    hash_changed: bool
    proposed_by: str = "model"  # "model" | "human"


@dataclass
class DiscoveryResult:
    status: str
    steps: int
    outputs_filled: dict[str, str]
    transcript: list[StepRecord]
    prompt_tokens: int
    output_tokens: int
    wall_seconds: float


# --------------------------------------------------------------------------- #
# History compaction
# --------------------------------------------------------------------------- #


def compact_step(rec: StepRecord) -> str:
    """One-line summary of a prior step for history compaction.

    Successes are terse; failures keep their detail -- because the detail is
    rung 2 of the self-correction ladder. If it falls out of history the
    model loses the reason it shouldn't retry.
    """
    target = f' "{rec.target_name}"' if rec.target_name else ""

    if rec.action_kind == "type" and rec.action_args.get("from_input"):
        desc = f'type from_input={rec.action_args["from_input"]} into{target}'
    elif rec.action_kind == "read" and rec.action_args.get("into_output"):
        detail_preview = ""
        if rec.result_detail:
            detail_preview = f' "{rec.result_detail}"'
        desc = f'read{detail_preview} into {rec.action_args["into_output"]}'
    elif rec.action_kind in ("done", "give_up", "escalate"):
        desc = rec.action_kind
    else:
        desc = f"{rec.action_kind}{target}"

    if not rec.verdict_allowed:
        who = "HUMAN " if rec.proposed_by == "human" else ""
        return f"step {rec.step}: {who}{desc} -> BLOCKED ({rec.verdict_risk})"

    if not rec.result_ok:
        who = "HUMAN " if rec.proposed_by == "human" else ""
        reason = rec.result_reason or "unknown"
        detail = f": {rec.result_detail}" if rec.result_detail else ""
        return f"step {rec.step}: {who}{desc} -> FAILED ({reason}){detail}"

    suffix = ", hash changed" if rec.hash_changed else ", but page did not change"
    who = "HUMAN " if rec.proposed_by == "human" else ""
    return f"step {rec.step}: {who}{desc} -> ok{suffix}"


# --------------------------------------------------------------------------- #
# Observation formatting
# --------------------------------------------------------------------------- #


def observation_text(obs: Observation) -> str:
    """Format an observation for the model. Canary-filtered."""
    parts = [f"URL: {obs.url}", f"TITLE: {obs.title}"]
    if obs.warnings:
        parts.append("WARNINGS: " + "; ".join(obs.warnings))
    for path, yaml_text in obs.frames.items():
        parts.append(f"\n[FRAME {path}]\n{yaml_text}")
    return redact("\n".join(parts))


# --------------------------------------------------------------------------- #
# Action building
# --------------------------------------------------------------------------- #


def build_action(
    name: str,
    args: dict,
    *,
    input_names: frozenset[str] | set[str] | None = None,
    output_names: frozenset[str] | set[str] | None = None,
) -> Action:
    """Build a typed Action from the model's function call.

    Raises ``ValueError`` on invalid input (caught by the loop, which feeds
    the message back to the model as an ``invalid_action`` turn).

    ``input_names`` and ``output_names`` are the declared names, checked
    here because **the enum in the function declaration is advice, not a
    constraint.**  `action_function_declarations` puts the declared names in
    `read.into_output` and `type.from_input` as a JSON-Schema enum, and the
    model is free to return something else — it did, naming an output
    `"Address"` that was never declared, and the run accepted it.

    Sending a declaration *to* an external system is not a guarantee about
    what comes *back*.  The enum makes a wrong name unlikely; this check is
    what makes it impossible.
    """
    if name == "navigate":
        return Navigate(url=args["url"])
    if name == "click":
        return Click(ref=args["ref"])
    if name == "type":
        from_input = args.get("from_input")
        if (
            from_input is not None
            and input_names is not None
            and from_input not in input_names
        ):
            # The worse of the two.  An undeclared name reaching the surface
            # would type an empty string into the field and carry on — a
            # member record silently blanked, which looks like success.
            raise ValueError(
                f"from_input={from_input!r} is not a declared input. "
                f"Declared: {sorted(input_names)}. Use one of those, or "
                f"pass text= for a value that is genuinely not an input."
            )
        return Type(
            ref=args["ref"],
            from_input=from_input,
            text=args.get("text"),
        )
    if name == "select":
        return Select(ref=args["ref"], option=args["option"])
    if name == "read":
        into_output = args["into_output"]
        if output_names is not None and into_output not in output_names:
            raise ValueError(
                f"into_output={into_output!r} is not a declared output. "
                f"Declared: {sorted(output_names)}. Read into one of those."
            )
        return Read(ref=args["ref"], into_output=into_output)
    if name == "done":
        return Done(rationale=args.get("rationale", ""))
    if name == "escalate":
        return Escalate(reason=args.get("reason", ""))
    if name == "give_up":
        return GiveUp(rationale=args.get("rationale", ""))
    raise ValueError(f"unknown action kind: {name}")


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


# Zero-token stand-in so human steps reuse `_make_record` without a model turn.
_HUMAN_TURN = BrainResponse(
    function_call_name=None,
    function_call_args=None,
    content=None,
    prompt_tokens=0,
    output_tokens=0,
)


class DiscoveryLoop:
    """The discovery loop. Observe -> decide -> act, with policy gates.

    ``run()`` is the CLI path and still terminates on stuck.  Pass
    ``pause_on_stuck=True`` (the console) to release the browser, keep the
    transcript open, and wait for ``human_act`` / ``resume_model``.  The
    human's clicks go through the same ``act()`` + policy gate so they
    compile; driving the raw Playwright window would leave a hole.
    """

    def __init__(
        self,
        surface: Surface,
        brain: GeminiBrain,
        goal: str,
        inputs: dict[str, str],
        outputs: dict[str, str],
        evidence_dir: Path,
        entry_url: str,
    ) -> None:
        self._surface = surface
        self._brain = brain
        self._goal = goal
        self._inputs = dict(inputs)
        self._outputs = dict(outputs)
        self._evidence_dir = Path(evidence_dir)
        self._evidence_dir.mkdir(parents=True, exist_ok=True)
        self._entry_url = entry_url

        self._brain.configure(
            input_names=list(inputs),
            output_names=list(outputs),
            rules=RULES,
        )

        self._begun = False
        self._pause_on_stuck = False
        self._paused = False
        self._pause_reason = ""
        self._transcript_file: Any = None
        self._filled: dict[str, str] = {}
        self._records: list[StepRecord] = []
        self._total_prompt = 0
        self._total_output = 0
        self._started = 0.0
        self._obs: Observation | None = None
        self._last_hash: str | None = None
        self._want_screenshot = False
        self._consecutive_off_allowlist = 0
        self._action_hash_counts: dict[tuple[str, str], int] = {}
        self._goal_msg = ""
        self._model_contents: list[Any] = []
        self._step = 0
        self._status = "max_steps"

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def pause_reason(self) -> str:
        return self._pause_reason

    @property
    def current_observation(self) -> Observation | None:
        return self._obs

    @property
    def inputs(self) -> dict[str, str]:
        return dict(self._inputs)

    @property
    def outputs(self) -> dict[str, str]:
        return dict(self._outputs)

    def run(self, *, pause_on_stuck: bool = False) -> DiscoveryResult:
        """Drive model turns until done, a terminal failure, or a pause."""
        self._pause_on_stuck = pause_on_stuck
        if not self._begun:
            self._begin()
        return self._model_turns()

    def human_act(self, action: Action) -> StepRecord:
        """One operator step while paused. Same policy + ``act()`` as the model.

        ``as_human=True`` is what lets the click through while ownership is
        HUMAN — the default ``act()`` still rejects, so the model cannot
        race the operator.
        """
        if not self._paused or self._obs is None or self._transcript_file is None:
            raise RuntimeError("discovery is not paused for a human step")

        self._step += 1
        obs = self._obs
        node: Node | None = None
        ref = getattr(action, "ref", None)
        if ref is not None:
            node = next((n for n in obs.nodes if n.ref == ref), None)

        raw = {
            k: v for k, v in action.__dict__.items()
            if k != "kind" and v is not None
        }

        verdict = policy_check(action, node, obs, mode=Mode.DISCOVERY)
        if not verdict.allowed:
            rec = self._make_record(
                self._step, obs, action.kind, raw, node,
                ok=False, reason="policy_blocked", detail=verdict.reason,
                trace=None, verdict_allowed=False,
                verdict_risk=verdict.risk.value,
                brain_resp=_HUMAN_TURN, hash_changed=False,
                proposed_by="human",
            )
            self._records.append(rec)
            self._write_record(self._transcript_file, rec)
            return rec

        result = self._surface.act(action, as_human=True)
        if isinstance(action, Read) and result.ok and result.detail:
            self._filled[action.into_output] = result.detail

        if isinstance(action, (Done, Escalate, GiveUp)):
            rec = self._make_record(
                self._step, obs, action.kind, raw, None,
                ok=result.ok, reason=result.reason, detail=result.detail,
                trace=None, verdict_allowed=True,
                verdict_risk=verdict.risk.value,
                brain_resp=_HUMAN_TURN, hash_changed=False,
                proposed_by="human",
            )
            self._records.append(rec)
            self._write_record(self._transcript_file, rec)
            if isinstance(action, Done):
                self._paused = False
                self._finish(action.kind)
            return rec

        self._obs = self._surface.observe()
        hash_changed = self._obs.hash != self._last_hash
        self._last_hash = self._obs.hash
        rec = self._make_record(
            self._step, self._obs, action.kind, raw, node,
            ok=result.ok, reason=result.reason, detail=result.detail,
            trace=result.trace, verdict_allowed=True,
            verdict_risk=verdict.risk.value,
            brain_resp=_HUMAN_TURN, hash_changed=hash_changed,
            proposed_by="human",
        )
        self._records.append(rec)
        self._write_record(self._transcript_file, rec)
        return rec

    def resume_model(self) -> DiscoveryResult:
        """Hand the page back to the model after one or more human steps."""
        if not self._paused:
            raise RuntimeError("discovery is not paused")
        self._surface.reacquire()
        self._paused = False
        self._pause_reason = ""
        self._action_hash_counts.clear()
        self._consecutive_off_allowlist = 0
        self._obs = self._surface.observe()
        self._last_hash = self._obs.hash
        return self._model_turns()

    def _begin(self) -> None:
        self._transcript_file = (self._evidence_dir / "transcript.jsonl").open("w")
        self._filled = {}
        self._records = []
        self._total_prompt = 0
        self._total_output = 0
        self._started = time.time()
        self._obs = self._surface.observe()
        self._last_hash = None
        self._want_screenshot = False
        self._consecutive_off_allowlist = 0
        self._action_hash_counts = {}
        self._goal_msg = (
            f"GOAL: {self._goal}\n"
            f"INPUTS: {json.dumps(self._inputs)}\n"
            f"OUTPUTS TO FILL: {list(self._outputs)}\n"
        )
        self._model_contents = []
        self._step = 0
        self._status = "max_steps"
        self._begun = True
        self._paused = False

    def _model_turns(self) -> DiscoveryResult:
        assert self._obs is not None and self._transcript_file is not None
        while self._step < MAX_STEPS:
            self._step += 1
            stop = self._one_model_turn()
            if stop == "paused":
                return self._snapshot()
            if stop is not None:
                return self._finish(stop)
            if time.time() - self._started > WALL_CLOCK_LIMIT:
                return self._finish("timeout")
        return self._finish("max_steps")

    def _one_model_turn(self) -> str | None:
        """One observe→decide→act. None = continue; a string is a stop status."""
        assert self._obs is not None and self._transcript_file is not None
        obs = self._obs
        last_turn = self._step == MAX_STEPS
        deadline_warning = (
            "\n** You have ONE turn remaining. If the goal is met, "
            "call done now. **"
            if last_turn else ""
        )
        contents = self._build_contents(
            self._goal_msg, obs, self._records, self._model_contents,
            extra_warning=deadline_warning,
        )

        brain_resp = self._brain.decide(contents)
        self._total_prompt += brain_resp.prompt_tokens
        self._total_output += brain_resp.output_tokens
        self._model_contents.append(brain_resp.content)

        if brain_resp.function_call_name is None:
            return "no_function_call"

        fc_name = brain_resp.function_call_name
        fc_args = brain_resp.function_call_args or {}

        try:
            action = build_action(
                fc_name, fc_args,
                input_names=frozenset(self._inputs),
                output_names=frozenset(self._outputs),
            )
        except ValueError as exc:
            rec = self._make_record(
                self._step, obs, fc_name, fc_args, None,
                ok=False, reason="invalid_action", detail=str(exc),
                trace=None, verdict_allowed=True, verdict_risk="safe",
                brain_resp=brain_resp, hash_changed=False,
            )
            self._records.append(rec)
            self._write_record(self._transcript_file, rec)
            return None

        node: Node | None = None
        ref = getattr(action, "ref", None)
        if ref is not None:
            node = next((n for n in obs.nodes if n.ref == ref), None)

        verdict = policy_check(action, node, obs, mode=Mode.DISCOVERY)

        if not verdict.allowed:
            if verdict.reason and "allowlist" in verdict.reason.lower():
                self._consecutive_off_allowlist += 1
            else:
                self._consecutive_off_allowlist = 0

            rec = self._make_record(
                self._step, obs, fc_name, fc_args, node,
                ok=False, reason="policy_blocked", detail=verdict.reason,
                trace=None, verdict_allowed=False,
                verdict_risk=verdict.risk.value,
                brain_resp=brain_resp, hash_changed=False,
            )
            self._records.append(rec)
            self._write_record(self._transcript_file, rec)

            if self._consecutive_off_allowlist >= 2:
                return "off_allowlist"
            return None
        self._consecutive_off_allowlist = 0

        if isinstance(action, Escalate) and self._pause_on_stuck:
            rec = self._make_record(
                self._step, obs, fc_name, fc_args, None,
                ok=True, reason="escalate",
                detail=fc_args.get("reason") or "model called escalate",
                trace=None, verdict_allowed=True,
                verdict_risk=verdict.risk.value,
                brain_resp=brain_resp, hash_changed=False,
            )
            self._records.append(rec)
            self._write_record(self._transcript_file, rec)
            return self._enter_pause(
                fc_args.get("reason") or "the model asked for a human"
            )

        if isinstance(action, (Done, Escalate, GiveUp)):
            result = self._surface.act(action)
            rec = self._make_record(
                self._step, obs, fc_name, fc_args, None,
                ok=result.ok, reason=result.reason, detail=result.detail,
                trace=None, verdict_allowed=True,
                verdict_risk=verdict.risk.value,
                brain_resp=brain_resp, hash_changed=False,
            )
            self._records.append(rec)
            self._write_record(self._transcript_file, rec)
            return fc_name

        result = self._surface.act(action)
        if isinstance(action, Read) and result.ok and result.detail:
            self._filled[action.into_output] = result.detail

        self._obs = self._surface.observe(screenshot=self._want_screenshot)
        hash_changed = self._obs.hash != self._last_hash
        self._want_screenshot = not hash_changed and self._last_hash is not None
        self._last_hash = self._obs.hash

        rec = self._make_record(
            self._step, self._obs, fc_name, fc_args, node,
            ok=result.ok, reason=result.reason, detail=result.detail,
            trace=result.trace, verdict_allowed=True,
            verdict_risk=verdict.risk.value,
            brain_resp=brain_resp, hash_changed=hash_changed,
        )
        self._records.append(rec)
        self._write_record(self._transcript_file, rec)

        key = (fc_name, self._obs.hash)
        self._action_hash_counts[key] = self._action_hash_counts.get(key, 0) + 1
        if self._action_hash_counts[key] >= 3:
            if self._pause_on_stuck:
                return self._enter_pause(
                    "the model repeated the same action on the same page "
                    "three times"
                )
            return "stuck"

        return None

    def _enter_pause(self, reason: str) -> str:
        self._paused = True
        self._pause_reason = reason
        self._status = "paused"
        self._surface.release()
        return "paused"

    def _snapshot(self) -> DiscoveryResult:
        return DiscoveryResult(
            status=self._status,
            steps=self._step,
            outputs_filled=dict(self._filled),
            transcript=list(self._records),
            prompt_tokens=self._total_prompt,
            output_tokens=self._total_output,
            wall_seconds=round(time.time() - self._started, 1),
        )

    def _finish(self, status: str) -> DiscoveryResult:
        self._status = status
        self._paused = False
        if self._transcript_file is not None:
            self._transcript_file.close()
            self._transcript_file = None
        return DiscoveryResult(
            status=status,
            steps=self._step,
            outputs_filled=dict(self._filled),
            transcript=list(self._records),
            prompt_tokens=self._total_prompt,
            output_tokens=self._total_output,
            wall_seconds=round(time.time() - self._started, 1),
        )

    # -- internals --------------------------------------------------------- #

    def _build_contents(
        self,
        goal_msg: str,
        current_obs: Observation,
        records: list[StepRecord],
        model_contents: list[Any],
        extra_warning: str = "",
    ) -> list:
        """Build the contents list with compacted history.

        Model-role Content objects are echoed verbatim (preserving thought
        signatures). User-role messages are compacted to one line per prior
        turn, with the full observation only for the current turn.

        All SDK types are built through ``GeminiBrain.make_user_content`` so
        this module never imports ``google.genai``.

        Structure (Gemini requires strict user/model alternation):
        - First turn: [user: goal + obs]
        - Later turns: [user: goal] [model: prev-1 response] [user: bridge]
          [model: prev response] [user: compacted history + current obs]

        We keep the two most recent model responses for thought-signature
        continuity. Older signatures are dropped, which is acceptable for
        Gemini 3 (the previous turn's signature is the load-bearing one).
        """
        make = self._brain.make_user_content

        parts: list[str] = [goal_msg]
        if records:
            parts.append("\nPRIOR STEPS:")
            for rec in records:
                parts.append(compact_step(rec))
        if extra_warning:
            parts.append(extra_warning)
        parts.append("\nCURRENT OBSERVATION:")
        parts.append(observation_text(current_obs))
        user_text = "\n".join(parts)
        user_content = make(user_text)

        if not model_contents:
            return [user_content]

        contents: list = []
        if len(model_contents) == 1:
            contents.append(make(goal_msg))
            contents.append(model_contents[0])
            contents.append(user_content)
        else:
            # Keep last two model responses for signature continuity.
            contents.append(make(goal_msg))
            contents.append(
                model_contents[-2] if len(model_contents) >= 2
                else model_contents[0]
            )
            contents.append(make("(prior context compacted into current observation)"))
            contents.append(model_contents[-1])
            contents.append(user_content)

        return contents

    @staticmethod
    def _make_record(
        step: int,
        obs: Observation,
        action_kind: str,
        action_args: dict,
        node: Node | None,
        *,
        ok: bool,
        reason: str | None,
        detail: str | None,
        trace: ResolutionTrace | None,
        verdict_allowed: bool,
        verdict_risk: str,
        brain_resp: BrainResponse,
        hash_changed: bool,
        proposed_by: str = "model",
    ) -> StepRecord:
        return StepRecord(
            step=step,
            obs_hash=obs.hash,
            action_kind=action_kind,
            action_args=action_args,
            target_role=node.role if node else None,
            target_name=node.name if node else None,
            result_ok=ok,
            result_reason=reason,
            result_detail=detail,
            trace=trace,
            verdict_allowed=verdict_allowed,
            verdict_risk=verdict_risk,
            prompt_tokens=brain_resp.prompt_tokens,
            output_tokens=brain_resp.output_tokens,
            hash_changed=hash_changed,
            proposed_by=proposed_by,
        )

    @staticmethod
    def _write_record(f, rec: StepRecord) -> None:
        trace_dict = None
        if rec.trace is not None:
            enclosing = [
                {"role": r.role, "name": r.name}
                for r in (rec.trace.enclosing_named_regions or [])
            ]
            tc = rec.trace.table_context
            table_ctx = None
            if tc is not None:
                table_ctx = {
                    "table_name": tc.table_name,
                    "column_header": tc.column_header,
                    "row_key": tc.row_key,
                    "col_index": tc.col_index,
                    "row_index": tc.row_index,
                }
            trace_dict = {
                "frame": rec.trace.frame,
                "role": rec.trace.role,
                "name": rec.trace.name,
                "name_unique_in_frame": rec.trace.name_unique_in_frame,
                "enclosing_named_regions": enclosing,
                "table_context": table_ctx,
                "dom_id_observed": rec.trace.dom_id_observed,
                "elapsed_ms": rec.trace.elapsed_ms,
            }
        line = redact(json.dumps({
            "step": rec.step,
            "obs_hash": rec.obs_hash,
            "action": rec.action_kind,
            "args": rec.action_args,
            "target_role": rec.target_role,
            "target_name": rec.target_name,
            "ok": rec.result_ok,
            "reason": rec.result_reason,
            "detail": rec.result_detail,
            "verdict": {
                "allowed": rec.verdict_allowed,
                "risk": rec.verdict_risk,
            },
            "trace": trace_dict,
            "prompt_tokens": rec.prompt_tokens,
            "output_tokens": rec.output_tokens,
            "hash_changed": rec.hash_changed,
            "proposed_by": rec.proposed_by,
        }))
        f.write(line + "\n")
        f.flush()
