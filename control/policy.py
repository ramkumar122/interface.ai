"""Allowlist, risk classification, and the single ``check()`` gate.

Called by **both** discovery and replay — never duplicated. Pure: no side effects,
no IO, no clock, no network. Same arguments always produce the same verdict.

Risk attaches to the **target**, not the verb. Clicking is not risky; clicking
``Open Account`` is. Classification matches on ``(role, accessible_name)``
regardless of action kind, so a ``click``, ``select``, or ``type`` on the same
element get the same risk. A single screen can hold a reversible action (``Lock``)
and an irreversible one (``Report Lost or Stolen``) and policy tells them apart.

``guarded_write`` means a member-visible record changed. A draft nobody can see
isn't that — which is why ``Continue to Review`` is ``safe`` and ``Open Account``
is ``irreversible``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Union
from urllib.parse import urlparse

from surface.base import (
    Action,
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


# --------------------------------------------------------------------------- #
# Types
# --------------------------------------------------------------------------- #


class Risk(str, Enum):
    SAFE = "safe"
    GUARDED_WRITE = "guarded_write"
    IRREVERSIBLE = "irreversible"


class Mode(str, Enum):
    DISCOVERY = "discovery"
    REPLAY = "replay"


@dataclass(frozen=True)
class Verdict:
    """The result of a policy check, fed back to the model as a tool result."""

    allowed: bool
    risk: Risk
    reason: str | None = None


# --------------------------------------------------------------------------- #
# Risk classification rules — code, not config
#
# Security-critical: reviewable in the same PR that changes routes or screens.
# First match wins; default safe.
# --------------------------------------------------------------------------- #

_RISK_RULES: list[tuple[str, str, Risk]] = [
    # Irreversible actions — blocked always, both paths
    ("button", "Open Account",          Risk.IRREVERSIBLE),
    ("radio",  "Report Lost or Stolen", Risk.IRREVERSIBLE),

    # Guarded writes — proceed in discovery, require approval for replay
    ("button", "Apply",           Risk.GUARDED_WRITE),
    ("button", "Confirm Update",  Risk.GUARDED_WRITE),
]


def classify_target(role: str, name: str) -> Risk:
    """Risk of acting on a target identified by ``(role, name)``.

    First-match in the rules list; default ``safe``.
    """
    for rule_role, rule_name, risk in _RISK_RULES:
        if role == rule_role and name == rule_name:
            return risk
    return Risk.SAFE


# --------------------------------------------------------------------------- #
# URL allowlist — default-deny
#
# /admin/* is excluded: the agent must never disable an injection it is being
# tested against. The sign-on page (/) is also excluded: the agent should not
# interact with the authentication flow.
# --------------------------------------------------------------------------- #

_ALLOWED_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(r"^/menu$"),
    re.compile(r"^/mbrinq$"),
    re.compile(r"^/mbrinq/results$"),
    re.compile(r"^/member/\d+$"),
    re.compile(r"^/member/\d+/shares$"),
    re.compile(r"^/member/\d+/cards$"),
    re.compile(r"^/member/\d+/cards/[^/]+/maint$"),
    re.compile(r"^/member/\d+/address$"),
    re.compile(r"^/member/\d+/address/review$"),
    re.compile(r"^/member/\d+/address/commit$"),
    re.compile(r"^/member/\d+/transactions$"),
    re.compile(r"^/member/\d+/shares/new$"),
    re.compile(r"^/member/\d+/shares/new/review$"),
    re.compile(r"^/member/\d+/shares/new/commit$"),
]


def is_url_allowed(url: str) -> bool:
    """Is *url* on the permitted-routes allowlist?

    Checks the path component only. Query strings are ignored.
    """
    path = urlparse(url).path.rstrip("/") or "/"
    return any(p.match(path) for p in _ALLOWED_PATH_PATTERNS)


# --------------------------------------------------------------------------- #
# The single check gate
# --------------------------------------------------------------------------- #

# Action kinds that never touch the browser and are always allowed.
_CONTROL_KINDS = frozenset({"done", "escalate", "give_up"})


def check(
    action: Action,
    node: Node | None,
    obs: Observation,
    *,
    mode: Mode = Mode.DISCOVERY,
    artifact_approved: bool = False,
) -> Verdict:
    """One function, called by both discovery and replay. Never duplicated.

    *action*: the ``Action`` the agent wants to perform.
    *node*: the target ``Node`` from the current observation (``None`` for
    control actions and ``navigate``).
    *obs*: the current ``Observation`` (used for URL allowlist checks).
    *mode*: ``DISCOVERY`` or ``REPLAY``.
    *artifact_approved*: whether an approved artifact is driving this replay.

    Returns a ``Verdict``. A blocked verdict is feedback, not failure — the loop
    feeds it back to the model as a tool result.
    """
    # Control actions never touch the browser.
    if isinstance(action, (Done, Escalate, GiveUp)):
        return Verdict(allowed=True, risk=Risk.SAFE)

    # Belt-and-suspenders: check the current page is on the allowlist for every
    # action, not just navigate. session_expired can bounce to / mid-flow.
    if not is_url_allowed(obs.url):
        return Verdict(
            allowed=False,
            risk=Risk.SAFE,
            reason=(
                f"Current page {obs.url} is not on the permitted-routes "
                f"allowlist. The agent should not interact with this page."
            ),
        )

    # Navigate: also check the destination URL.
    if isinstance(action, Navigate):
        if not is_url_allowed(action.url):
            return Verdict(
                allowed=False,
                risk=Risk.SAFE,
                reason=(
                    f"Navigation to {action.url} is not permitted. "
                    f"This URL is outside the agent's allowlist."
                ),
            )
        return Verdict(allowed=True, risk=Risk.SAFE)

    # For actions that target an element, classify by (role, name).
    if node is not None:
        risk = classify_target(node.role, node.name)
    else:
        risk = Risk.SAFE

    if risk == Risk.IRREVERSIBLE:
        return Verdict(
            allowed=False,
            risk=Risk.IRREVERSIBLE,
            reason=(
                f"Action on {node.role} \"{node.name}\" is classified as "
                f"irreversible and is blocked in both discovery and replay. "
                f"Consider completing your goal without this action, or "
                f"call escalate to hand off to a human operator."
            ),
        )

    if risk == Risk.GUARDED_WRITE:
        if mode == Mode.DISCOVERY:
            return Verdict(allowed=True, risk=Risk.GUARDED_WRITE)
        # Replay: allowed only with an approved artifact.
        if artifact_approved:
            return Verdict(allowed=True, risk=Risk.GUARDED_WRITE)
        return Verdict(
            allowed=False,
            risk=Risk.GUARDED_WRITE,
            reason=(
                f"Action on {node.role} \"{node.name}\" is a guarded write "
                f"and requires an approved artifact for unattended replay."
            ),
        )

    return Verdict(allowed=True, risk=Risk.SAFE)
