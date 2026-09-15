"""Pure perception/action types and the ``Surface`` protocol.

This module is deliberately dependency-free: it imports neither ``playwright``
nor ``google.genai``. Everything the agent and the compiler exchange with a
browser crosses this seam as one of these dataclasses, so keeping it pure is
what lets ``agent/`` and ``replay/`` depend on the vocabulary without dragging
in a browser or a model SDK (agent-rules boundaries 3 and 4).

The tool schema the model sees is *derived from* the ``Action`` union here, by
``action_function_declarations`` — plain JSON-Schema dicts, no SDK import. The
spike wraps them in ``types.Tool``. Building the schema from the types (rather
than hand-writing it beside them) makes drift structurally impossible.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal, Protocol, Union, runtime_checkable

# --------------------------------------------------------------------------- #
# Observation types
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Node:
    """One accessibility node the model may act on.

    ``ref`` (``e1``, ``e2`` …) is valid for the observation it came from and
    nothing else; it must never be recorded into an artifact. What gets
    recorded upward is ``(frame, role, name)`` plus, for act-time
    disambiguation only, ``occurrence``.
    """

    ref: str
    frame: str
    role: str
    name: str
    value: str | None
    disabled: bool
    occurrence: int


@dataclass(frozen=True)
class NamedRegion:
    """A landmark/region/table ancestor, carrying BOTH role and name.

    A bare name loses the role, so the compiler could not emit
    ``"within": {"role": "table", "name": "Share accounts"}``.
    """

    role: str
    name: str


@dataclass(frozen=True)
class TableContext:
    """Where a cell sits, so a locator can say 'the cell under AVAILABLE'.

    Column association is positional *now* (Nth cell under the Nth
    columnheader) but what is recorded is the header *name*, which is what
    survives a tenant column reorder. ``None`` when a row's cell count does not
    match the header (e.g. a full-width colspan row) — honest beats wrong.
    """

    table_name: str
    column_header: str | None
    row_key: str | None
    col_index: int
    row_index: int


@dataclass(frozen=True)
class Observation:
    """A whole-page snapshot: ref-annotated YAML per frame plus a state hash."""

    url: str
    title: str
    frames: dict[str, str]  # frame_path -> ref-annotated ARIA YAML (what the model sees)
    nodes: list[Node]
    screenshot: Path | None
    warnings: list[str]
    hash: str


@dataclass(frozen=True)
class ResolutionTrace:
    """Evidence of how a ref resolved to a live element, for the compiler.

    ``dom_id_observed`` is recorded for evidence only and never used to locate.
    """

    frame: str
    role: str
    name: str
    name_unique_in_frame: bool
    enclosing_named_regions: list[NamedRegion]
    table_context: TableContext | None
    dom_id_observed: str | None
    elapsed_ms: float


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    trace: ResolutionTrace | None = None
    reason: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class EvidenceRef:
    label: str
    path: Path


# --------------------------------------------------------------------------- #
# Action tagged union
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Navigate:
    url: str
    kind: Literal["navigate"] = "navigate"


@dataclass(frozen=True)
class Click:
    ref: str
    kind: Literal["click"] = "click"


@dataclass(frozen=True)
class Type:
    """Type into ``ref``. Exactly one of ``from_input`` or ``text``.

    ``from_input`` names a declared input whose value the harness substitutes,
    so the model never sees or invents the value. It is preferred; free ``text``
    exists only for values that genuinely aren't a declared input.
    """

    ref: str
    from_input: str | None = None
    text: str | None = None
    kind: Literal["type"] = "type"

    def __post_init__(self) -> None:
        if (self.from_input is None) == (self.text is None):
            raise ValueError("Type requires exactly one of from_input or text")


@dataclass(frozen=True)
class Select:
    ref: str
    option: str
    kind: Literal["select"] = "select"


@dataclass(frozen=True)
class Read:
    ref: str
    into_output: str
    kind: Literal["read"] = "read"


@dataclass(frozen=True)
class Done:
    rationale: str
    kind: Literal["done"] = "done"


@dataclass(frozen=True)
class Escalate:
    reason: str
    kind: Literal["escalate"] = "escalate"


@dataclass(frozen=True)
class GiveUp:
    rationale: str
    kind: Literal["give_up"] = "give_up"


Action = Union[Navigate, Click, Type, Select, Read, Done, Escalate, GiveUp]

# Ordered so schema output is stable across runs.
ACTION_CLASSES: tuple[type, ...] = (
    Navigate,
    Click,
    Type,
    Select,
    Read,
    Done,
    Escalate,
    GiveUp,
)
ACTION_KINDS: tuple[str, ...] = tuple(
    next(f.default for f in fields(cls) if f.name == "kind") for cls in ACTION_CLASSES
)


# --------------------------------------------------------------------------- #
# Tool schema derived from the Action types (SDK-free)
# --------------------------------------------------------------------------- #

# Every Action field is a string in the wire schema; the enum-valued ones are
# constrained at runtime from the CLI-declared input/output names.
_ENUM_FROM = {"from_input": "inputs", "into_output": "outputs"}

_DESCRIPTIONS = {
    "navigate": "Load a URL as a full-page navigation.",
    "click": "Click the element identified by ref in the current observation.",
    "type": (
        "Type into the element at ref. Provide exactly one of from_input "
        "(preferred: the name of a declared input whose value is substituted "
        "for you) or text (a literal). Prefer from_input whenever the value "
        "comes from a declared input; never invent an input value as text."
    ),
    "select": "Choose option (by its visible label) in the select element at ref.",
    "read": "Read the visible text at ref into the declared output named into_output.",
    "done": "Signal the goal is complete; rationale states what proves it met.",
    "escalate": "Hand off to a human operator; reason explains why.",
    "give_up": "Abandon the goal; rationale explains why it cannot be completed.",
}


def _kind_of(cls: type) -> str:
    return next(f.default for f in fields(cls) if f.name == "kind")


def action_function_declarations(
    input_names: list[str], output_names: list[str]
) -> list[dict]:
    """Return one JSON-Schema function declaration per Action kind.

    Plain dicts, no ``google.genai`` import — the spike wraps them in
    ``types.Tool``. The parameter set of each declaration is derived from the
    dataclass fields (minus ``kind``), so it cannot drift from the types.
    ``read.into_output`` becomes an enum of the declared output names and
    ``type.from_input`` an enum of the declared input names.
    """

    enums = {"from_input": input_names, "into_output": output_names}
    decls: list[dict] = []
    for cls in ACTION_CLASSES:
        kind = _kind_of(cls)
        properties: dict[str, dict] = {}
        required: list[str] = []
        for f in fields(cls):
            if f.name == "kind":
                continue
            prop: dict = {"type": "string"}
            if f.name in enums:
                prop["enum"] = list(enums[f.name])
            properties[f.name] = prop
            has_default = (
                f.default is not dataclasses.MISSING
                or f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
            )
            if not has_default:
                required.append(f.name)
        decls.append(
            {
                "name": kind,
                "description": _DESCRIPTIONS[kind],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            }
        )
    return decls


# --------------------------------------------------------------------------- #
# Surface protocol
# --------------------------------------------------------------------------- #


@runtime_checkable
class Surface(Protocol):
    """The only seam through which anything reaches a browser."""

    # `screenshot` is declared here because `agent/loop.py` passes it —
    # a screenshot is rung 3 of the self-correction ladder, requested when
    # an observation is ambiguous.  The protocol omitted it, so it described
    # something no implementation had and no caller used; a decorating
    # surface written against the declaration broke the moment discovery
    # asked for a screenshot.
    def observe(self, screenshot: bool = False) -> Observation: ...
    def act(self, action: Action, *, as_human: bool = False) -> ActionResult: ...
    def evidence(self, label: str) -> EvidenceRef: ...
    def release(self) -> None: ...
    def reacquire(self) -> None: ...
