"""Canary detection and redaction for evidence writers.

Separated from ``policy.py`` because redaction is a write concern, not a policy
decision. Evidence writers import these without pulling in the policy module.
"""

from __future__ import annotations

CANARY = "CANARY-A7F3-DONOTLOG"
_REPLACEMENT = "[REDACTED-CANARY]"


def contains_canary(text: str) -> bool:
    """True if the canary string appears anywhere in *text*."""
    return CANARY in text


def redact(text: str) -> str:
    """Replace every occurrence of the canary string with a safe marker."""
    return text.replace(CANARY, _REPLACEMENT)
