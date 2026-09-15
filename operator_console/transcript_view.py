"""Rendering a discovery transcript, live or recorded.

`agent/loop.py::_write_record` writes one JSON line per turn and **flushes**,
so `evidence/<run_id>/transcript.jsonl` is a live feed while a run is in
flight and a recording afterwards.  Same format either way, so this module
serves the live status page and rehearsal mode without knowing which it is.

That is why Part 4 of the plan needed no change to `agent/`: the progress
display is a rendering of a file the loop already writes, in the same sense
the replay step log is a rendering of `StepTrace`.

Nothing here interprets.  A refused action is shown as a refusal with the
policy's own reason, because a model trying something, being told no, and
adapting is the self-correction ladder working — rendering it as an error
would be a lie about what happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from control.redact import redact


# --------------------------------------------------------------------------- #
# One turn
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Turn:
    """One model turn, as the page shows it."""

    ordinal: int
    action: str
    target: str
    actor: str  # "model" | "human"
    from_input: str | None
    outcome: str
    kind: str  # "ok" | "refused" | "failed" | "terminal"
    detail: str
    hash_changed: bool
    prompt_tokens: int
    output_tokens: int

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.output_tokens


_TERMINAL = {"done", "escalate", "give_up"}


def _target_of(rec: dict) -> str:
    """What the turn acted on, in the vocabulary the model was given."""
    name = rec.get("target_name")
    if name:
        return f'"{name}"'
    args = rec.get("args") or {}
    if rec.get("action") == "navigate" and args.get("url"):
        return args["url"]
    for key in ("rationale", "reason"):
        if args.get(key):
            return str(args[key])
    return "—"


def _outcome_of(rec: dict) -> tuple[str, str, str]:
    """(kind, one-line outcome, detail).

    A policy refusal and a failed action are both feedback to the model,
    not errors in the run.  They get their own kind so the page can style
    them as information and say what the model was told.
    """
    action = rec.get("action") or ""
    verdict = rec.get("verdict") or {}
    detail = str(rec.get("detail") or "")

    if not verdict.get("allowed", True):
        return "refused", f"refused — {detail}", detail

    if action in _TERMINAL:
        return "terminal", action, detail

    if not rec.get("ok", True):
        reason = rec.get("reason") or "failed"
        return "failed", f"{reason} — {detail}" if detail else str(reason), detail

    if rec.get("hash_changed"):
        return "ok", "ok, page changed", detail
    return "ok", "ok", detail


def turn_from_record(rec: dict) -> Turn:
    args = rec.get("args") or {}
    kind, outcome, detail = _outcome_of(rec)
    return Turn(
        ordinal=int(rec.get("step") or 0),
        action=str(rec.get("action") or "?"),
        target=redact(_target_of(rec)),
        # The model naming a declared input rather than inventing a value is
        # the perception rule holding; worth showing on the row.
        from_input=args.get("from_input"),
        actor=str(rec.get("proposed_by") or "model"),
        outcome=redact(outcome),
        kind=kind,
        detail=redact(detail),
        hash_changed=bool(rec.get("hash_changed")),
        prompt_tokens=int(rec.get("prompt_tokens") or 0),
        output_tokens=int(rec.get("output_tokens") or 0),
    )


# --------------------------------------------------------------------------- #
# A whole transcript
# --------------------------------------------------------------------------- #


@dataclass
class TranscriptView:
    run_id: str
    turns: list[Turn]
    recorded: bool = False  # True for rehearsal — never let it read as live
    truncated_to: int | None = None

    @property
    def total_tokens(self) -> int:
        return sum(t.tokens for t in self.turns)

    @property
    def last_ordinal(self) -> int:
        return self.turns[-1].ordinal if self.turns else 0

    @property
    def finished(self) -> bool:
        """The loop called a terminal action, so there are no more turns."""
        return bool(self.turns) and self.turns[-1].kind == "terminal"

    @property
    def refusals(self) -> int:
        return sum(1 for t in self.turns if t.kind == "refused")


def read_transcript(
    path: Path, *, limit: int | None = None, recorded: bool = False,
    run_id: str = "",
) -> TranscriptView:
    """Parse a transcript file, tolerating a partial last line.

    The loop flushes after each record, but a reader can still arrive
    mid-write.  A half-written line is skipped rather than crashing the
    page — the next refresh will have it whole.
    """
    turns: list[Turn] = []
    if path.is_file():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # partial final line; it will be complete next poll
            turns.append(turn_from_record(rec))

    truncated = None
    if limit is not None and limit < len(turns):
        turns = turns[:limit]
        truncated = limit

    return TranscriptView(
        run_id=run_id or path.parent.name,
        turns=turns,
        recorded=recorded,
        truncated_to=truncated,
    )


def available_recordings(evidence_root: Path) -> list[tuple[str, int, str]]:
    """Saved discovery runs that rehearsal mode can play.

    Returns (run_id, turn count, one-line outcome) so the picker can say
    what each recording shows without opening it.
    """
    out: list[tuple[str, int, str]] = []
    if not evidence_root.is_dir():
        return out
    for d in sorted(evidence_root.glob("discover-*")):
        tpath = d / "transcript.jsonl"
        if not tpath.is_file():
            continue
        view = read_transcript(tpath, recorded=True, run_id=d.name)
        if not view.turns:
            continue
        summary_path = d / "summary.json"
        note = "no summary — the run died before writing one"
        if summary_path.is_file():
            try:
                s = json.loads(summary_path.read_text())
                bits = [f"status {s.get('status')}"]
                if s.get("compiled") is not None:
                    bits.append("compiled" if s["compiled"] else "not compiled")
                if s.get("verified") is not None:
                    bits.append("verified" if s["verified"] else "not verified")
                note = ", ".join(bits)
            except (OSError, json.JSONDecodeError):
                pass
        out.append((d.name, len(view.turns), note))
    return out
