"""Playwright implementation of the ``Surface`` protocol.

This is the **only** module in the project that imports ``playwright`` (agent
-rules boundary 3/4). Everything above it speaks the pure vocabulary in
``surface.base``.

The parser targets the real shape of ``aria_snapshot()`` YAML observed against a
live CoreDesk: each entry is a
bare string or a single-key dict whose value is a scalar (the node's text) or a
list (children); ``/url`` pseudo-entries are link hrefs and are dropped so the
model only ever sees roles and names.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from playwright.sync_api import Frame, Page

from surface.base import (
    Action,
    ActionResult,
    Click,
    Done,
    Escalate,
    EvidenceRef,
    GiveUp,
    NamedRegion,
    Navigate,
    Node,
    Observation,
    Read,
    ResolutionTrace,
    Select,
    TableContext,
    Type,
)

# Container roles whose accessible name is worth recording as an enclosing
# region for the compiler to scope a locator within.
_REGION_ROLES = frozenset(
    {
        "table",
        "grid",
        "region",
        "form",
        "navigation",
        "main",
        "banner",
        "complementary",
        "contentinfo",
        "article",
        "dialog",
        "list",
        "listbox",
        "menu",
        "tabpanel",
        "group",
    }
)

_HEADER_RE = re.compile(
    r'^(?P<role>[^\s"\[]+)'  # role token
    r'(?:\s+"(?P<name>(?:[^"\\]|\\.)*)")?'  # optional "accessible name"
    r"(?P<attrs>(?:\s+\[[^\]]*\])*)\s*$"  # optional [attr] groups
)


def _unescape(name: str) -> str:
    return name.replace('\\"', '"').replace("\\\\", "\\")


def _parse_header(header: str) -> tuple[str, str, dict]:
    """Split a snapshot header token into (role, name, attrs)."""
    m = _HEADER_RE.match(header.strip())
    if not m:
        return header.strip(), "", {}
    role = m.group("role")
    name = _unescape(m.group("name")) if m.group("name") is not None else ""
    attrs: dict = {}
    for chunk in re.findall(r"\[([^\]]*)\]", m.group("attrs") or ""):
        chunk = chunk.strip()
        if "=" in chunk:
            k, _, v = chunk.partition("=")
            attrs[k.strip()] = v.strip()
        elif chunk:
            attrs[chunk] = True
    return role, name, attrs


# --------------------------------------------------------------------------- #
# Parsed AX node
# --------------------------------------------------------------------------- #


@dataclass
class _AXNode:
    role: str
    name: str
    value: str | None
    attrs: dict
    children: list["_AXNode"] = field(default_factory=list)
    # computed in pre-passes:
    table_context: TableContext | None = None


def _parse_entry(entry) -> _AXNode | None:
    """Convert one yaml.safe_load entry into an _AXNode (or None to drop)."""
    if isinstance(entry, str):
        header, scalar, children = entry, None, []
    elif isinstance(entry, dict):
        # single-key dict
        header = next(iter(entry))
        v = entry[header]
        if isinstance(v, list):
            scalar, children = None, v
        elif v is None:
            scalar, children = None, []
        else:
            scalar, children = str(v), []
    else:
        return None

    role, name, attrs = _parse_header(str(header))
    if role.startswith("/"):  # /url and similar pseudo-entries
        return None
    node = _AXNode(role=role, name=name, value=scalar, attrs=attrs)
    for child in children:
        parsed = _parse_entry(child)
        if parsed is not None:
            node.children.append(parsed)
    return node


def _parse_snapshot(text: str) -> list[_AXNode]:
    data = yaml.safe_load(text)
    if data is None:
        return []
    if not isinstance(data, list):
        data = [data]
    out: list[_AXNode] = []
    for entry in data:
        parsed = _parse_entry(entry)
        if parsed is not None:
            out.append(parsed)
    return out


# --------------------------------------------------------------------------- #
# Table context (positional now, recorded by header NAME)
# --------------------------------------------------------------------------- #


def _rows_of(table: _AXNode) -> list[_AXNode]:
    """Rows belonging to this table, descending through rowgroups but never
    into a nested table."""
    rows: list[_AXNode] = []

    def rec(n: _AXNode) -> None:
        for c in n.children:
            if c.role == "table":
                continue
            if c.role == "row":
                rows.append(c)
            else:
                rec(c)

    rec(table)
    return rows


def _annotate_tables(tree: list[_AXNode], warnings: list[str]) -> None:
    """Attach TableContext to each data cell, applying the colspan guard."""

    def visit(node: _AXNode) -> None:
        if node.role in ("table", "grid"):
            _annotate_one_table(node, warnings)
        for c in node.children:
            visit(c)

    for n in tree:
        visit(n)


def _annotate_one_table(table: _AXNode, warnings: list[str]) -> None:
    table_name = table.name or "(unnamed table)"
    rows = _rows_of(table)
    header_cells: list[_AXNode] = []
    for r in rows:
        if any(c.role == "columnheader" for c in r.children):
            header_cells = [c for c in r.children if c.role == "columnheader"]
            break
    n_cols = len(header_cells)

    data_row_index = 0
    for r in rows:
        cells = [c for c in r.children if c.role == "cell"]
        if not cells:
            continue  # header row or empty
        if n_cols and len(cells) != n_cols:
            # colspan guard: a full-width row (e.g. zero-results) does not align
            # with the header. Omit context rather than guess a column.
            warnings.append(
                f"table {table_name!r}: row has {len(cells)} cell(s) but header "
                f"has {n_cols}; table_context omitted (colspan?)"
            )
            data_row_index += 1
            continue
        row_key = cells[0].name or (cells[0].value or "")
        for idx, cell in enumerate(cells):
            header_name = header_cells[idx].name if idx < n_cols else None
            cell.table_context = TableContext(
                table_name=table_name,
                column_header=header_name or None,
                row_key=row_key or None,
                col_index=idx,
                row_index=data_row_index,
            )
        data_row_index += 1


# --------------------------------------------------------------------------- #
# Hash exclusions (per-app config)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Exclusion:
    match: str  # "accessible_name" | "text_pattern"
    value: str
    scope: str | None = None


def load_hash_exclusions(config_path: Path) -> list[_Exclusion]:
    cfg = yaml.safe_load(Path(config_path).read_text()) or {}
    out: list[_Exclusion] = []
    for item in cfg.get("hash_exclusions", []):
        out.append(
            _Exclusion(
                match=item["match"],
                value=item["value"],
                scope=item.get("scope"),
            )
        )
    return out


def _excluded(
    node: _AXNode, enclosing: list[NamedRegion], exclusions: list[_Exclusion]
) -> bool:
    for ex in exclusions:
        if ex.match == "accessible_name":
            if node.name == ex.value:
                return True
        elif ex.match == "text_pattern":
            in_scope = ex.scope in (None, "*") or any(
                r.name == ex.scope for r in enclosing
            )
            if in_scope:
                hay = " ".join(x for x in (node.name, node.value) if x)
                if re.search(ex.value, hay):
                    return True
    return False


# --------------------------------------------------------------------------- #
# WebSurface
# --------------------------------------------------------------------------- #


@dataclass
class _Meta:
    frame_path: str
    role: str
    name: str
    occurrence: int
    enclosing: list[NamedRegion]
    table_context: TableContext | None


class WebSurface:
    """A live browser presented through the pure ``Surface`` vocabulary."""

    def __init__(
        self,
        page: Page,
        *,
        evidence_dir: Path,
        config_path: Path,
        inputs: dict[str, str] | None = None,
    ) -> None:
        self.page = page

        # Defence in depth: dismiss every confirm() dialog by default.
        # Registered FIRST, before any navigation or state setup, so a dialog
        # fired during a subsequent page load cannot hang Playwright.
        # CoreDesk uses confirm() only on irreversible actions (Report Lost or
        # Stolen, Open Account). This makes dismissal explicit and permanent,
        # so "the agent cannot complete an irreversible action" is true by
        # construction even if risk classification misses.
        # Scope: covers dialogs on self.page only, not new popups/pages.
        # CoreDesk has no popups; noted as a known limitation.
        self.page.on("dialog", lambda d: d.dismiss())

        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._exclusions = load_hash_exclusions(config_path)
        self._inputs = dict(inputs or {})
        self._owner = "AUTOMATION"  # AUTOMATION | HUMAN | NONE
        self._meta: dict[str, _Meta] = {}
        self._frames: dict[str, Frame] = {}
        self._evidence_seq = 0
        # Set the actor header so audit writes distinguish AUTOMATION/HUMAN
        self.page.set_extra_http_headers({"X-CoreDesk-Actor": "AUTOMATION"})

    # -- frame naming (design E) -------------------------------------------- #

    def _resolve_labelledby(self, frame: Frame, ids: str) -> str:
        owner = frame.parent_frame or self.page.main_frame
        texts = []
        for _id in ids.split():
            loc = owner.locator(f"#{_id}")
            if loc.count():
                texts.append(loc.first.inner_text().strip())
        return " ".join(t for t in texts if t)

    def _frame_path(self, frame: Frame, index: int) -> tuple[str, str | None]:
        if frame == self.page.main_frame:
            return "main", None
        fe = frame.frame_element()
        label = fe.get_attribute("aria-label")
        if label:
            return f"frame[{label}]", None
        labelledby = fe.get_attribute("aria-labelledby")
        if labelledby:
            text = self._resolve_labelledby(frame, labelledby)
            if text:
                return f"frame[{text}]", None
        title = fe.get_attribute("title")
        if title:
            return f"frame[{title}]", None
        return (
            f"frame[{index}]",
            f"iframe #{index} has no accessible name "
            f"(aria-label/aria-labelledby/title); used positional frame[{index}]",
        )

    # -- frame re-derivation (act time, not cached) -------------------------- #

    def _resolve_frame(self, frame_path: str) -> Frame | None:
        """Re-derive the live Frame for a frame path at act time.

        Using cached Frame objects from observe() is fragile: a navigation
        between observe() and act() can destroy an iframe, leaving a stale
        object. Re-deriving from the current page.frames is safe.
        """
        if frame_path == "main":
            return self.page.main_frame
        for i, frame in enumerate(self.page.frames):
            if frame == self.page.main_frame:
                continue
            path, _ = self._frame_path(frame, i)
            if path == frame_path:
                return frame
        return None

    # -- password redaction (task 4a finding) ------------------------------- #

    @staticmethod
    def _secrets(frame: Frame) -> list[str]:
        vals: list[str] = []
        loc = frame.locator("input[type=password]")
        for i in range(loc.count()):
            v = loc.nth(i).input_value()
            if v:
                vals.append(v)
        return vals

    # -- observe ------------------------------------------------------------ #

    def observe(self, screenshot: bool = False) -> Observation:
        self._meta.clear()
        self._frames.clear()
        warnings: list[str] = []
        frames_yaml: dict[str, str] = {}
        canonical_by_path: dict[str, str] = {}
        raw_nodes: list[dict] = []
        ref_counter = 0

        for i, frame in enumerate(self.page.frames):
            path, warn = self._frame_path(frame, i)
            if warn:
                warnings.append(warn)
            self._frames[path] = frame

            raw = frame.locator(":root").aria_snapshot()
            for secret in self._secrets(frame):
                raw = raw.replace(secret, "")  # redact before parse/hash/model
            tree = _parse_snapshot(raw)
            _annotate_tables(tree, warnings)

            annotated: list[str] = []
            canonical: list[str] = []

            def walk(node: _AXNode, depth: int, enclosing: list[NamedRegion]) -> None:
                nonlocal ref_counter
                ref_counter += 1
                ref = f"e{ref_counter}"
                value = node.value if (node.value not in (None, "")) else None
                indent = "  " * depth
                label = f"{node.role}"
                if node.name:
                    label += f' "{node.name}"'
                if value is not None:
                    label += f": {value}"
                annotated.append(f"{indent}- {label} [ref={ref}]")

                if not _excluded(node, enclosing, self._exclusions):
                    canonical.append(f"{node.role}|{node.name}|{value or ''}")

                raw_nodes.append(
                    {
                        "ref": ref,
                        "frame": path,
                        "role": node.role,
                        "name": node.name,
                        "value": value,
                        "disabled": bool(node.attrs.get("disabled", False)),
                        "enclosing": list(enclosing),
                        "table_context": node.table_context,
                    }
                )

                child_enclosing = enclosing
                if node.role in _REGION_ROLES and node.name:
                    child_enclosing = enclosing + [NamedRegion(node.role, node.name)]
                for c in node.children:
                    walk(c, depth + 1, child_enclosing)

            for n in tree:
                walk(n, 0, [])

            frames_yaml[path] = "\n".join(annotated)
            canonical_by_path[path] = "\n".join(canonical)

        # occurrence per (frame, role, name), then build Node list + meta
        counts: dict[tuple, int] = {}
        nodes: list[Node] = []
        for rn in raw_nodes:
            key = (rn["frame"], rn["role"], rn["name"])
            occ = counts.get(key, 0)
            counts[key] = occ + 1
            nodes.append(
                Node(
                    ref=rn["ref"],
                    frame=rn["frame"],
                    role=rn["role"],
                    name=rn["name"],
                    value=rn["value"],
                    disabled=rn["disabled"],
                    occurrence=occ,
                )
            )
            self._meta[rn["ref"]] = _Meta(
                frame_path=rn["frame"],
                role=rn["role"],
                name=rn["name"],
                occurrence=occ,
                enclosing=rn["enclosing"],
                table_context=rn["table_context"],
            )

        canonical = "\n".join(
            f"{path}\n{canonical_by_path[path]}" for path in sorted(canonical_by_path)
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

        shot: Path | None = None
        if screenshot:
            self._evidence_seq += 1
            shot = self.evidence_dir / f"observe-{self._evidence_seq:03d}.png"
            self.page.screenshot(path=str(shot))

        return Observation(
            url=self.page.url,
            title=self.page.title(),
            frames=frames_yaml,
            nodes=nodes,
            screenshot=shot,
            warnings=warnings,
            hash=digest,
        )

    # -- act ---------------------------------------------------------------- #

    def act(self, action: Action, *, as_human: bool = False) -> ActionResult:
        # Control actions never touch the browser.
        if isinstance(action, (Done, Escalate, GiveUp)):
            return ActionResult(ok=True, reason=action.kind)

        owner_ok = (
            (as_human and self._owner == "HUMAN")
            or (not as_human and self._owner == "AUTOMATION")
        )
        if not owner_ok:
            return ActionResult(ok=False, reason="not_owner")

        if isinstance(action, Navigate):
            try:
                self.page.goto(action.url)
                return ActionResult(ok=True)
            except Exception as exc:
                return ActionResult(
                    ok=False,
                    reason="navigate_failed",
                    detail=f"{type(exc).__name__}: {exc}",
                )

        ref = getattr(action, "ref", None)
        if ref is None or ref not in self._meta:
            return ActionResult(ok=False, reason="stale_ref")

        meta = self._meta[ref]
        frame = self._resolve_frame(meta.frame_path)
        if frame is None:
            return ActionResult(ok=False, reason="stale_ref")

        started = time.perf_counter()
        try:
            locator = frame.get_by_role(meta.role, name=meta.name, exact=True)
            match_count = locator.count()
            if match_count == 0:
                return ActionResult(ok=False, reason="not_resolved")
            target = locator.nth(meta.occurrence) if match_count > 1 else locator.first

            detail: str | None = None
            if isinstance(action, Click):
                target.click()
            elif isinstance(action, Type):
                if action.from_input is not None:
                    # Not `.get(name, "")`.  A silent default here types an
                    # empty string into the field and the run carries on
                    # looking successful — on an address form that is a
                    # member record blanked.  A missing key is an error.
                    if action.from_input not in self._inputs:
                        return ActionResult(
                            ok=False,
                            reason="undeclared_input",
                            detail=(
                                f"from_input={action.from_input!r} was not "
                                f"supplied to this surface. Known: "
                                f"{sorted(self._inputs)}"
                            ),
                        )
                    value = self._inputs[action.from_input]
                else:
                    value = action.text or ""
                target.fill(value)
            elif isinstance(action, Select):
                target.select_option(label=action.option)
            elif isinstance(action, Read):
                detail = target.inner_text().strip()
            else:  # pragma: no cover - union is exhaustive above
                return ActionResult(ok=False, reason="unsupported_action")

            dom_id = None
            try:
                # Short timeout: after a click that navigates, the target
                # locator is stale and get_attribute would wait 30s for it
                # to reappear. 500ms is enough for a non-navigating action.
                dom_id = target.get_attribute("id", timeout=500)
            except Exception:
                pass

            trace = ResolutionTrace(
                frame=meta.frame_path,
                role=meta.role,
                name=meta.name,
                name_unique_in_frame=(match_count == 1),
                enclosing_named_regions=meta.enclosing,
                table_context=meta.table_context,
                dom_id_observed=dom_id,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )
            return ActionResult(ok=True, trace=trace, detail=detail)
        except Exception as exc:  # locator/action failure is a surface result
            return ActionResult(
                ok=False,
                reason="action_failed",
                detail=f"{type(exc).__name__}: {exc}",
            )

    # -- evidence / ownership ---------------------------------------------- #

    def evidence(self, label: str) -> EvidenceRef:
        self._evidence_seq += 1
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", label)
        path = self.evidence_dir / f"{self._evidence_seq:03d}-{safe}.png"
        self.page.screenshot(path=str(path))
        return EvidenceRef(label=label, path=path)

    def release(self) -> None:
        self._owner = "HUMAN"
        # Clear actor header so human actions log as HUMAN in audit
        self.page.set_extra_http_headers({})

    def reacquire(self) -> None:
        self._owner = "AUTOMATION"
        # Restore actor header for automation audit trail
        self.page.set_extra_http_headers({"X-CoreDesk-Actor": "AUTOMATION"})
