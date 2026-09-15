"""Locator resolution: Locator + Observation -> Node ref.

Three strategies (ax, ax_scoped, ax_relative), each producing a unique ref or
a diagnostic error.  Never imports ``playwright`` or ``google.genai``.

For ``ax``, resolution uses the flat node list on Observation.  For
``ax_relative`` and ``ax_scoped``, it parses the per-frame annotated YAML to
recover tree structure (table nesting, named regions).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from artifacts.schema import FrameRef, Locator
from surface.base import Observation


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class LocateError(Exception):
    """Cannot resolve a locator against the current observation."""

    def __init__(self, step_id: str, strategy: str, detail: str) -> None:
        self.step_id = step_id
        self.strategy = strategy
        self.detail = detail
        super().__init__(f"step {step_id} ({strategy}): {detail}")


# --------------------------------------------------------------------------- #
# Frame matching
# --------------------------------------------------------------------------- #

_FRAME_NAME_RE = re.compile(r"^frame\[(.+)\]$")


def match_frame(frame_ref: FrameRef, frame_path: str) -> bool:
    """Does *frame_path* satisfy *frame_ref*?"""
    if frame_ref.match == "exact":
        return frame_path == frame_ref.value
    if frame_ref.match == "role_name":
        m = _FRAME_NAME_RE.match(frame_path)
        if not m:
            return False
        return m.group(1).startswith(frame_ref.name_prefix or "")
    return False


def _matching_frames(frame_ref: FrameRef, obs: Observation) -> list[str]:
    return [fp for fp in obs.frames if match_frame(frame_ref, fp)]


# --------------------------------------------------------------------------- #
# Lightweight YAML parser for annotated observation text
# --------------------------------------------------------------------------- #

_LINE_RE = re.compile(
    r"^(?P<indent>\s*)-\s+"
    r"(?P<role>[^\s\":\[]+)"
    r'(?:\s+"(?P<name>[^"]*)")?'
    r"(?::\s+(?P<value>.+?))?"
    r"\s+\[ref=(?P<ref>e\d+)\]$"
)


@dataclass
class _YNode:
    role: str
    name: str
    ref: str
    value: str | None = None
    children: list["_YNode"] = field(default_factory=list)


def _parse_annotated(text: str) -> list[_YNode]:
    """Parse ref-annotated YAML into a tree, recovering nesting from indent."""
    root: list[_YNode] = []
    stack: list[tuple[int, list[_YNode]]] = [(-1, root)]

    for line in text.splitlines():
        m = _LINE_RE.match(line)
        if not m:
            continue
        depth = len(m.group("indent"))
        node = _YNode(
            role=m.group("role"),
            name=m.group("name") or "",
            ref=m.group("ref"),
            value=m.group("value"),
        )
        while len(stack) > 1 and stack[-1][0] >= depth:
            stack.pop()
        stack[-1][1].append(node)
        stack.append((depth, node.children))

    return root


def _find_named(tree: list[_YNode], role: str, name: str) -> _YNode | None:
    """DFS for first node matching role + name."""
    for n in tree:
        if n.role == role and n.name == name:
            return n
        hit = _find_named(n.children, role, name)
        if hit:
            return hit
    return None


def _collect(tree: list[_YNode], role: str) -> list[_YNode]:
    """Collect all descendants with *role*, stopping descent at nested tables."""
    out: list[_YNode] = []
    for n in tree:
        if n.role == "table":
            continue
        if n.role == role:
            out.append(n)
        out.extend(_collect(n.children, role))
    return out


# --------------------------------------------------------------------------- #
# The public entry point
# --------------------------------------------------------------------------- #


def locate(locator: Locator, obs: Observation, step_id: str) -> str:
    """Resolve a Locator against an Observation, returning a Node ref.

    Raises ``LocateError`` with a diagnostic if resolution fails.
    """
    frames = _matching_frames(locator.frame, obs)
    if not frames:
        raise LocateError(
            step_id, locator.strategy,
            f"no frame matches {locator.frame.model_dump()}",
        )

    if locator.strategy == "ax":
        return _locate_ax(locator, obs, frames, step_id)
    if locator.strategy == "ax_scoped":
        return _locate_ax_scoped(locator, obs, frames, step_id)
    if locator.strategy == "ax_relative":
        return _locate_ax_relative(locator, obs, frames, step_id)

    raise LocateError(step_id, locator.strategy, "unknown strategy")


# --------------------------------------------------------------------------- #
# Strategies
# --------------------------------------------------------------------------- #


def _locate_ax(
    loc: Locator, obs: Observation, frames: list[str], step_id: str
) -> str:
    """ax: unique by role+name in frame."""
    hits = [
        n for n in obs.nodes
        if n.frame in frames and n.role == loc.role and n.name == loc.name
    ]
    if len(hits) == 0:
        raise LocateError(
            step_id, "ax",
            f'no {loc.role} "{loc.name}" in {frames}',
        )
    if len(hits) > 1:
        raise LocateError(
            step_id, "ax",
            f'{len(hits)} nodes match {loc.role} "{loc.name}" in {frames}',
        )
    return hits[0].ref


def _locate_ax_scoped(
    loc: Locator, obs: Observation, frames: list[str], step_id: str
) -> str:
    """ax_scoped: unique within a named region."""
    assert loc.within is not None
    for fp in frames:
        tree = _parse_annotated(obs.frames.get(fp, ""))
        region = _find_named(tree, loc.within.role, loc.within.name)
        if region is None:
            continue
        target = _find_named(region.children, loc.role, loc.name or "")
        if target is not None:
            return target.ref
    raise LocateError(
        step_id, "ax_scoped",
        f'no {loc.role} "{loc.name}" within '
        f'{loc.within.role} "{loc.within.name}" in {frames}',
    )


def _locate_ax_relative(
    loc: Locator, obs: Observation, frames: list[str], step_id: str
) -> str:
    """ax_relative: table cell by column header and row key."""
    for fp in frames:
        tree = _parse_annotated(obs.frames.get(fp, ""))
        table = _find_named(tree, "table", loc.table_name or "")
        if table is None:
            continue

        # Column headers — recursive within table, skip nested tables
        headers = _collect(table.children, "columnheader")
        col_idx: int | None = None
        for i, h in enumerate(headers):
            if h.name == loc.column_header:
                col_idx = i
                break

        if col_idx is None:
            raise LocateError(
                step_id, "ax_relative",
                f'column "{loc.column_header}" not found in table '
                f'"{loc.table_name}"; columns are: '
                f"{[h.name for h in headers]}",
            )

        # Data rows — recursive within table, skip nested tables
        rows = _collect(table.children, "row")
        for row in rows:
            cells = [c for c in row.children if c.role == "cell"]
            if not cells:
                continue
            first = cells[0].name or cells[0].value or ""
            matched = (
                (loc.row_match == "exact" and first == loc.row_key)
                or (loc.row_match == "prefix" and first.startswith(loc.row_key or ""))
                or (loc.row_match is None and first == loc.row_key)
            )
            if matched and col_idx < len(cells):
                return cells[col_idx].ref

        raise LocateError(
            step_id, "ax_relative",
            f'row with key "{loc.row_key}" not found in table "{loc.table_name}"',
        )

    raise LocateError(
        step_id, "ax_relative",
        f'table "{loc.table_name}" not found in {frames}',
    )
