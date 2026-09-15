"""Tests for control/policy.py — the single check gate.

Every test constructs its own arguments and calls check() directly. No browser,
no server, no IO. The gate tests (#7 and #8) prove the central claim: a single
screen can hold actions of different risk classes, and policy tells them apart.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from control.policy import (
    Mode,
    Risk,
    Verdict,
    check,
    classify_target,
    is_url_allowed,
)
from control.redact import contains_canary, redact
from surface.base import (
    Click,
    Done,
    Escalate,
    GiveUp,
    Navigate,
    Node,
    Observation,
    Read,
    Select,
    Type,
)

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Helpers — build minimal typed objects for the check() signature
# --------------------------------------------------------------------------- #


def _node(role: str = "button", name: str = "Search", ref: str = "e1") -> Node:
    return Node(ref=ref, frame="main", role=role, name=name,
                value=None, disabled=False, occurrence=0)


def _obs(url: str = "http://127.0.0.1:8001/menu") -> Observation:
    return Observation(
        url=url,
        title="CoreDesk",
        frames={"main": ""},
        nodes=[],
        screenshot=None,
        warnings=[],
        hash="abc123",
    )


# --------------------------------------------------------------------------- #
# 1. Safe action — allowed in both modes
# --------------------------------------------------------------------------- #


def test_safe_action_allowed_both_modes():
    node = _node("button", "Search")
    obs = _obs()
    for mode in (Mode.DISCOVERY, Mode.REPLAY):
        v = check(Click(ref="e1"), node, obs, mode=mode)
        assert v.allowed is True
        assert v.risk == Risk.SAFE


# --------------------------------------------------------------------------- #
# 2–4. Guarded write — Apply on card maint
# --------------------------------------------------------------------------- #


def test_guarded_write_allowed_in_discovery():
    node = _node("button", "Apply")
    obs = _obs("http://127.0.0.1:8001/member/100101/cards/CRD-1/maint")
    v = check(Click(ref="e1"), node, obs, mode=Mode.DISCOVERY)
    assert v.allowed is True
    assert v.risk == Risk.GUARDED_WRITE


def test_guarded_write_blocked_in_replay_without_approval():
    node = _node("button", "Apply")
    obs = _obs("http://127.0.0.1:8001/member/100101/cards/CRD-1/maint")
    v = check(Click(ref="e1"), node, obs, mode=Mode.REPLAY, artifact_approved=False)
    assert v.allowed is False
    assert v.risk == Risk.GUARDED_WRITE
    assert v.reason is not None


def test_guarded_write_allowed_in_replay_with_approval():
    node = _node("button", "Apply")
    obs = _obs("http://127.0.0.1:8001/member/100101/cards/CRD-1/maint")
    v = check(Click(ref="e1"), node, obs, mode=Mode.REPLAY, artifact_approved=True)
    assert v.allowed is True
    assert v.risk == Risk.GUARDED_WRITE


# --------------------------------------------------------------------------- #
# 5–6. Irreversible — blocked always, with a reason
# --------------------------------------------------------------------------- #


def test_irreversible_blocked_always():
    obs = _obs("http://127.0.0.1:8001/member/100101/cards/CRD-1/maint")
    radio = _node("radio", "Report Lost or Stolen")
    for mode in (Mode.DISCOVERY, Mode.REPLAY):
        v = check(Click(ref="e1"), radio, obs, mode=mode)
        assert v.allowed is False
        assert v.risk == Risk.IRREVERSIBLE

    obs_share = _obs("http://127.0.0.1:8001/member/100101/shares/new/review")
    btn = _node("button", "Open Account")
    for mode in (Mode.DISCOVERY, Mode.REPLAY):
        v = check(Click(ref="e1"), btn, obs_share, mode=mode)
        assert v.allowed is False
        assert v.risk == Risk.IRREVERSIBLE


def test_irreversible_verdict_has_reason():
    obs = _obs("http://127.0.0.1:8001/member/100101/cards/CRD-1/maint")
    radio = _node("radio", "Report Lost or Stolen")
    v = check(Click(ref="e1"), radio, obs, mode=Mode.DISCOVERY)
    assert v.reason is not None
    assert "irreversible" in v.reason.lower()


# --------------------------------------------------------------------------- #
# 7–8. Gate tests — two risk classes on the same screen
# --------------------------------------------------------------------------- #


def test_card_maint_two_risk_classes():
    """Gate A: card maintenance's Lock (safe) and Report Lost or Stolen
    (irreversible) are distinguished on the same screen."""
    obs = _obs("http://127.0.0.1:8001/member/100101/cards/CRD-1/maint")

    lock = _node("radio", "Lock")
    v_lock = check(Click(ref="e1"), lock, obs, mode=Mode.DISCOVERY)
    assert v_lock.allowed is True
    assert v_lock.risk == Risk.SAFE

    hotlist = _node("radio", "Report Lost or Stolen")
    v_hot = check(Click(ref="e1"), hotlist, obs, mode=Mode.DISCOVERY)
    assert v_hot.allowed is False
    assert v_hot.risk == Risk.IRREVERSIBLE


def test_share_opening_two_risk_classes():
    """Gate B: share-opening's Continue to Review (safe) and Open Account
    (irreversible) produce the agent's stopping behaviour."""
    obs_form = _obs("http://127.0.0.1:8001/member/100101/shares/new")
    cont = _node("button", "Continue to Review")
    v_cont = check(Click(ref="e1"), cont, obs_form, mode=Mode.DISCOVERY)
    assert v_cont.allowed is True
    assert v_cont.risk == Risk.SAFE

    obs_review = _obs("http://127.0.0.1:8001/member/100101/shares/new/review")
    open_btn = _node("button", "Open Account")
    v_open = check(Click(ref="e1"), open_btn, obs_review, mode=Mode.DISCOVERY)
    assert v_open.allowed is False
    assert v_open.risk == Risk.IRREVERSIBLE


# --------------------------------------------------------------------------- #
# 9–12. URL allowlist
# --------------------------------------------------------------------------- #


def test_admin_inject_not_allowed():
    assert is_url_allowed("http://127.0.0.1:8001/admin/inject") is False


def test_allowed_routes():
    allowed = [
        "http://127.0.0.1:8001/menu",
        "http://127.0.0.1:8001/mbrinq",
        "http://127.0.0.1:8001/mbrinq/results",
        "http://127.0.0.1:8001/member/100101",
        "http://127.0.0.1:8001/member/100101/shares",
        "http://127.0.0.1:8001/member/100101/cards",
        "http://127.0.0.1:8001/member/100101/cards/CRD-1/maint",
        "http://127.0.0.1:8001/member/100101/address",
        "http://127.0.0.1:8001/member/100101/address/review",
        "http://127.0.0.1:8001/member/100101/address/commit",
        "http://127.0.0.1:8001/member/100101/transactions",
        "http://127.0.0.1:8001/member/100101/shares/new",
        "http://127.0.0.1:8001/member/100101/shares/new/review",
        "http://127.0.0.1:8001/member/100101/shares/new/commit",
    ]
    for url in allowed:
        assert is_url_allowed(url), f"{url} should be allowed"


def test_navigate_action_checks_destination():
    obs = _obs("http://127.0.0.1:8001/menu")
    v_ok = check(Navigate(url="http://127.0.0.1:8001/mbrinq"), None, obs)
    assert v_ok.allowed is True

    v_bad = check(Navigate(url="http://127.0.0.1:8001/admin/inject"), None, obs)
    assert v_bad.allowed is False
    assert "not permitted" in v_bad.reason.lower()


def test_current_page_checked_on_all_actions():
    """A click when obs.url is off the allowlist (e.g. after session_expired
    redirect to /) is denied even though the target is safe."""
    obs = _obs("http://127.0.0.1:8001/")  # sign-on page — not allowed
    node = _node("button", "Search")
    v = check(Click(ref="e1"), node, obs)
    assert v.allowed is False
    assert "allowlist" in v.reason.lower()


# --------------------------------------------------------------------------- #
# 13. Control actions — always allowed
# --------------------------------------------------------------------------- #


def test_done_escalate_give_up_always_allowed():
    obs = _obs()
    for action in [Done(rationale="done"), Escalate(reason="stuck"), GiveUp(rationale="nope")]:
        v = check(action, None, obs)
        assert v.allowed is True
        assert v.risk == Risk.SAFE


# --------------------------------------------------------------------------- #
# 14. Canary detection (control/redact.py)
# --------------------------------------------------------------------------- #


def test_canary_detection():
    assert contains_canary("some text CANARY-A7F3-DONOTLOG more text") is True
    assert contains_canary("safe text") is False


def test_canary_redaction():
    assert "CANARY-A7F3-DONOTLOG" not in redact("has CANARY-A7F3-DONOTLOG in it")
    assert "[REDACTED-CANARY]" in redact("has CANARY-A7F3-DONOTLOG in it")


# --------------------------------------------------------------------------- #
# 15. Default safe
# --------------------------------------------------------------------------- #


def test_classify_target_default_safe():
    assert classify_target("link", "Return to Inquiry") == Risk.SAFE
    assert classify_target("textbox", "Member Number") == Risk.SAFE
    assert classify_target("button", "SomethingUnknown") == Risk.SAFE


# --------------------------------------------------------------------------- #
# 16. select/type on irreversible target — blocked identically to click
# --------------------------------------------------------------------------- #


def test_select_on_irreversible_target_blocked():
    """Risk is keyed on the target, not the verb. select and type on the
    'Report Lost or Stolen' radio are blocked identically to click."""
    obs = _obs("http://127.0.0.1:8001/member/100101/cards/CRD-1/maint")
    radio = _node("radio", "Report Lost or Stolen")

    v_select = check(Select(ref="e1", option="Report Lost or Stolen"), radio, obs)
    assert v_select.allowed is False
    assert v_select.risk == Risk.IRREVERSIBLE

    v_type = check(Type(ref="e1", text="anything"), radio, obs)
    assert v_type.allowed is False
    assert v_type.risk == Risk.IRREVERSIBLE


# --------------------------------------------------------------------------- #
# 17. check() is pure — no side effects
# --------------------------------------------------------------------------- #


def test_check_is_pure():
    """Same arguments produce identical verdicts; no mutable module state."""
    obs = _obs("http://127.0.0.1:8001/member/100101/cards/CRD-1/maint")
    node = _node("button", "Apply")
    action = Click(ref="e1")
    kwargs = dict(mode=Mode.DISCOVERY, artifact_approved=False)

    v1 = check(action, node, obs, **kwargs)
    v2 = check(action, node, obs, **kwargs)
    assert v1 == v2
    assert v1.allowed == v2.allowed
    assert v1.risk == v2.risk
    assert v1.reason == v2.reason


# --------------------------------------------------------------------------- #
# 18. Import purity
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
    return found


def test_policy_imports_clean():
    """policy.py imports nothing from playwright, google.genai, surface.web,
    or coredesk."""
    mods = _imports(REPO / "control" / "policy.py")
    forbidden = {"playwright", "google.genai", "google.generativeai",
                 "surface.web", "coredesk"}
    roots = {m.split(".")[0] for m in mods}
    overlap = roots & forbidden
    assert not overlap, f"control/policy.py imports {overlap}"
    # Also check full module paths for google.genai
    assert not any(m.startswith("google.genai") for m in mods)
    assert not any(m.startswith("coredesk") for m in mods)
