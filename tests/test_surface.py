"""Tests for the perception/action surface.

Two tiers:

* **Always-run** (no browser, no key): the pure-types import stays clean, the
  module boundaries hold, and the tool schema is derived from the Action types.
* **Live** (``@pytest.mark.live``): real Chromium against CoreDesk on :8001.
  Skipped loudly (see conftest) when the browser or server is absent.
"""

from __future__ import annotations

import ast
import os
import socket
import tempfile
from pathlib import Path

import pytest

from surface.base import (
    ACTION_KINDS,
    Click,
    Read,
    action_function_declarations,
)

REPO = Path(__file__).resolve().parents[1]
BASE = os.environ.get("COREDESK_BASE", "http://127.0.0.1:8001")
USER = os.environ.get("COREDESK_USER", "mreyes")
PASS = os.environ.get("COREDESK_PASS", "demo1234")
CONFIG = REPO / "surfaces" / "coredesk.yaml"


# --------------------------------------------------------------------------- #
# Import scanning (boundary enforcement)
# --------------------------------------------------------------------------- #


def _imports(pyfile: Path) -> set[str]:
    tree = ast.parse(pyfile.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if base:
                found.add(base)
            for a in node.names:
                found.add(f"{base}.{a.name}" if base else a.name)
    return found


def _py_files(pkg: str) -> list[Path]:
    d = REPO / pkg
    return list(d.rglob("*.py")) if d.exists() else []


def _uses_playwright(mods: set[str]) -> bool:
    return any(m == "playwright" or m.startswith("playwright.") for m in mods)


def _uses_genai(mods: set[str]) -> bool:
    return any(
        m in ("google.genai", "google.generativeai")
        or m.startswith("google.genai")
        or m.startswith("google.generativeai")
        for m in mods
    )


def test_base_is_sdk_free():
    """surface/base.py imports neither playwright nor google.genai."""
    mods = _imports(REPO / "surface" / "base.py")
    assert not _uses_playwright(mods)
    assert not _uses_genai(mods)


def test_only_web_imports_playwright():
    for f in _py_files("surface"):
        if f.name == "web.py":
            continue
        assert not _uses_playwright(_imports(f)), f"{f} imports playwright"


def test_coredesk_does_not_import_automation():
    forbidden = {"surface", "agent", "replay", "control", "operator_console"}
    for f in _py_files("coredesk"):
        roots = {m.split(".")[0] for m in _imports(f)}
        assert not (roots & forbidden), f"{f} imports {roots & forbidden}"


def test_replay_never_imports_genai():
    for f in _py_files("replay"):
        assert not _uses_genai(_imports(f)), f"{f} imports google.genai"


def test_agent_and_replay_never_import_playwright():
    for pkg in ("agent", "replay"):
        for f in _py_files(pkg):
            assert not _uses_playwright(_imports(f)), f"{f} imports playwright"


# --------------------------------------------------------------------------- #
# Tool schema derived from Action types (no SDK)
# --------------------------------------------------------------------------- #


def test_tool_schema_matches_actions():
    inputs = ["member_no"]
    outputs = ["savings_balance"]
    decls = action_function_declarations(inputs, outputs)

    names = [d["name"] for d in decls]
    assert names == list(ACTION_KINDS)

    by_name = {d["name"]: d for d in decls}
    # type: ref required, from_input/text optional, from_input enum injected
    typ = by_name["type"]
    props = typ["parameters"]["properties"]
    assert set(props) == {"ref", "from_input", "text"}
    assert typ["parameters"]["required"] == ["ref"]
    assert props["from_input"]["enum"] == inputs
    # read: into_output enum injected and required
    read = by_name["read"]
    assert read["parameters"]["properties"]["into_output"]["enum"] == outputs
    assert set(read["parameters"]["required"]) == {"ref", "into_output"}


def test_declarations_have_no_sdk_types():
    """The builder returns plain dicts, safe to hand to any SDK wrapper."""
    for d in action_function_declarations([], []):
        assert isinstance(d, dict)
        assert isinstance(d["parameters"], dict)


# --------------------------------------------------------------------------- #
# Hash-exclusion matchers (no browser). Exercises BOTH config matchers so the
# vendor-app fallback path is real, not theoretical.
# --------------------------------------------------------------------------- #


def _web():
    try:
        import surface.web as web  # imports playwright (installed via requirements)
    except ImportError:
        pytest.skip("playwright not installed")
    return web


def test_coredesk_config_declares_both_matchers():
    web = _web()
    kinds = {e.match for e in web.load_hash_exclusions(CONFIG)}
    assert {"accessible_name", "text_pattern"} <= kinds


def test_accessible_name_matcher_excludes_labelled_date():
    web = _web()
    node = web._AXNode(role="cell", name="Current date", value="09/11/2026", attrs={})
    ex = [web._Exclusion(match="accessible_name", value="Current date")]
    assert web._excluded(node, [], ex) is True


def test_text_pattern_fallback_excludes_unlabelled_date():
    """When a vendor app cannot be re-tagged, a scoped regex still catches it."""
    web = _web()
    node = web._AXNode(role="cell", name="", value="10/07/2026", attrs={})
    # A tightly scoped pattern excludes the node when inside the named region.
    scoped = [web._Exclusion(match="text_pattern", value=r"\d{2}/\d{2}/\d{4}$", scope="banner")]
    inside_banner = [web.NamedRegion("banner", "banner")]
    assert web._excluded(node, inside_banner, scoped) is True
    # Outside the scope → not excluded, which is exactly the point: a date in
    # content (e.g. "Effective Date" on the address form) must stay in the hash.
    assert web._excluded(node, [], scoped) is False
    # The clean name matcher would NOT catch an unlabelled date.
    name_only = [web._Exclusion(match="accessible_name", value="Current date")]
    assert web._excluded(node, [], name_only) is False


def test_text_pattern_wildcard_scope_is_too_broad():
    """Regression: scope '*' drops date-bearing content nodes from the hash.

    Two address-form observations differing only in effective date would hash
    equal if a wildcard scope ate the date, making stuck-detection misfire and
    pruning collapse steps that mattered.
    """
    web = _web()
    node = web._AXNode(role="textbox", name="Effective Date", value="09/15/2026", attrs={})
    wildcard = [web._Exclusion(match="text_pattern", value=r"\d{2}/\d{2}/\d{4}$", scope="*")]
    # With scope: "*", ANY node whose text ends date-like would be excluded.
    # This is dangerous — the effective date is meaningful content.
    assert web._excluded(node, [], wildcard) is True  # wildcard hits it (bad)
    # With scope: "banner", it is NOT excluded (good).
    scoped = [web._Exclusion(match="text_pattern", value=r"\d{2}/\d{2}/\d{4}$", scope="banner")]
    assert web._excluded(node, [], scoped) is False  # content node safe


def test_text_pattern_respects_scope():
    web = _web()
    node = web._AXNode(role="cell", name="", value="10/07/2026", attrs={})
    scoped = [web._Exclusion(match="text_pattern", value=r"\d{2}/\d{2}/\d{4}$", scope="Footer")]
    # not inside a region named "Footer" -> not excluded
    assert web._excluded(node, [], scoped) is False
    inside = [web.NamedRegion("contentinfo", "Footer")]
    assert web._excluded(node, inside, scoped) is True


# --------------------------------------------------------------------------- #
# Live tier
# --------------------------------------------------------------------------- #


def _server_up() -> bool:
    host, port = BASE.split("//")[1].split(":")
    try:
        with socket.create_connection((host, int(port)), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.fixture(scope="module")
def live():
    if not _server_up():
        pytest.skip(f"CoreDesk not reachable on {BASE}")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True)
    except Exception as exc:  # Chromium not installed
        pw.stop()
        pytest.skip(f"Chromium not available: {exc}")
    yield {"browser": browser}
    browser.close()
    pw.stop()


def _new_surface(live, *, sign_in: bool = True, inputs=None):
    from surface.web import WebSurface

    page = live["browser"].new_page()
    if sign_in:
        page.goto(f"{BASE}/")
        page.fill("#ctl00_MainContent_txtUserId", USER)
        page.fill("#ctl00_MainContent_txtPassword", PASS)
        page.click("#ctl00_MainContent_btnSignOn")
        page.wait_for_load_state("networkidle")
    evd = Path(tempfile.mkdtemp(prefix="surface-test-"))
    return page, WebSurface(page, evidence_dir=evd, config_path=CONFIG, inputs=inputs)


@pytest.mark.live
def test_member_record_has_main_and_one_named_iframe(live):
    page, surface = _new_surface(live)
    page.goto(f"{BASE}/member/100101")
    page.wait_for_load_state("networkidle")
    obs = surface.observe()
    assert "main" in obs.frames
    child = [p for p in obs.frames if p != "main"]
    assert len(child) == 1
    assert child[0] == "frame[Share accounts for member 100101]"
    page.close()


@pytest.mark.live
def test_share_accounts_table_node_in_frame(live):
    page, surface = _new_surface(live)
    page.goto(f"{BASE}/member/100101")
    page.wait_for_load_state("networkidle")
    obs = surface.observe()
    hits = [
        n for n in obs.nodes if n.role == "table" and n.name == "Share accounts"
    ]
    assert hits, "no 'Share accounts' table node"
    assert hits[0].frame == "frame[Share accounts for member 100101]"
    page.close()


@pytest.mark.live
def test_hash_stable_then_differs(live):
    page, surface = _new_surface(live)
    page.goto(f"{BASE}/member/100101")
    page.wait_for_load_state("networkidle")
    h1 = surface.observe().hash
    h2 = surface.observe().hash
    assert h1 == h2  # date is excluded, so an unchanged page is stable
    page.goto(f"{BASE}/member/100110")
    page.wait_for_load_state("networkidle")
    assert surface.observe().hash != h1
    page.close()


@pytest.mark.live
def test_password_value_does_not_leak(live):
    """Safety property, keyed on the verified shape (task 4a).

    The field surfaces as `textbox "Password"` and the typed value would
    otherwise smear into ancestor names, so we assert the sentinel appears in NO
    node name, NO node value, and NO frame YAML.
    """
    page, surface = _new_surface(live, sign_in=False)
    page.goto(f"{BASE}/")
    page.fill("#ctl00_MainContent_txtUserId", USER)
    page.fill("#ctl00_MainContent_txtPassword", "demo1234")
    obs = surface.observe()

    pw_nodes = [n for n in obs.nodes if n.role == "textbox" and n.name == "Password"]
    assert pw_nodes, "expected a textbox 'Password' node (verified shape)"
    assert all(n.value is None for n in pw_nodes)

    assert not any("demo1234" in y for y in obs.frames.values())
    assert not any(
        "demo1234" in (n.name or "") or "demo1234" == (n.value or "")
        for n in obs.nodes
    )
    page.close()


@pytest.mark.live
def test_stale_ref_rejected(live):
    page, surface = _new_surface(live)
    page.goto(f"{BASE}/mbrinq")
    page.wait_for_load_state("networkidle")
    surface.observe()
    res = surface.act(Click(ref="e99999"))
    assert res.ok is False
    assert res.reason == "stale_ref"
    page.close()


@pytest.mark.live
def test_search_button_is_unique_in_frame(live):
    page, surface = _new_surface(live)
    page.goto(f"{BASE}/mbrinq")
    page.wait_for_load_state("networkidle")
    obs = surface.observe()
    btns = [n for n in obs.nodes if n.role == "button" and n.name == "Search"]
    assert btns
    res = surface.act(Click(ref=btns[0].ref))
    assert res.ok
    assert res.trace is not None
    assert res.trace.name_unique_in_frame is True
    page.close()


@pytest.mark.live
def test_cell_read_carries_column_header(live):
    """Reading a cell records its column header by NAME, not position."""
    page, surface = _new_surface(live)
    page.goto(f"{BASE}/member/100101")
    page.wait_for_load_state("networkidle")
    obs = surface.observe()
    cell = [n for n in obs.nodes if n.role == "cell" and n.name == "12,845.50"]
    assert cell
    res = surface.act(Read(ref=cell[0].ref, into_output="savings_balance"))
    assert res.ok
    assert res.detail == "12,845.50"
    assert res.trace.table_context is not None
    assert res.trace.table_context.column_header == "AVAILABLE"
    assert res.trace.table_context.row_key == "0000"
    page.close()


@pytest.mark.live
def test_colspan_row_omits_table_context_with_warning(live):
    page, surface = _new_surface(live)
    page.goto(f"{BASE}/member/100109/cards")  # NO_CARD_ON_FILE: colspan row
    page.wait_for_load_state("networkidle")
    obs = surface.observe()
    assert any("colspan" in w.lower() for w in obs.warnings)
    page.close()


@pytest.mark.live
def test_dialog_dismissal_backstop(live):
    """Defence in depth: a confirm() dialog is dismissed and the server state
    is unchanged. Proves the backstop works, not just that a handler is
    registered.

    Uses card maintenance: select the HOTLIST radio (which gates the confirm
    on the form's onsubmit), then click Apply. The dialog fires and is
    dismissed, so the form never POSTs and the card stays ACTIVE.
    """
    page, surface = _new_surface(live)
    page.goto(f"{BASE}/member/100101/cards/CRD-100101-1/maint")
    page.wait_for_load_state("networkidle")

    # Select the HOTLIST radio via the page (not the surface — we're testing
    # the dialog handler, not the action vocabulary).
    page.click("#ctl00_MainContent_rdoHotlist")
    page.select_option("#ctl00_MainContent_selReason", label="Member request")

    # Click Apply. The form's onsubmit fires confirm() for HOTLIST. The
    # dialog handler dismisses it, so the POST never happens.
    obs_before = surface.observe()
    page.click("#ctl00_MainContent_btnApply")

    # If the dialog was accepted, the page would redirect (303) to the maint
    # GET with ?ref=CRD-... and the card would be HOTLISTED. If dismissed,
    # we stay on the same page with the same URL and no status change.
    obs_after = surface.observe()

    # Same page — no redirect happened.
    assert "/maint" in obs_after.url
    assert "ref=" not in obs_after.url

    # Card still shows ACTIVE in the detail table.
    status_cells = [n for n in obs_after.nodes
                    if n.role == "cell" and n.name == "ACTIVE"]
    assert status_cells, "card should still be ACTIVE after dismissed dialog"
    page.close()
