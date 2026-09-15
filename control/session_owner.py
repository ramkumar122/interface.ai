"""Ownership model: AUTOMATION | HUMAN | NONE, with a real lock.

Exactly one holder at a time.  ``Surface.act()`` asserts ownership before
touching the browser.  The lock makes concurrent access structurally
impossible rather than conventionally avoided.

The lock is reentrant (``RLock``) because recovery flows call ``act()``
multiple times without releasing — dismiss clicks, retry navigations.
"""

from __future__ import annotations

import threading
from enum import Enum


class Owner(Enum):
    AUTOMATION = "AUTOMATION"
    HUMAN = "HUMAN"
    NONE = "NONE"


# Valid transitions.  Anything not listed raises.
_VALID_TRANSITIONS: set[tuple[Owner, Owner]] = {
    (Owner.NONE, Owner.AUTOMATION),
    (Owner.AUTOMATION, Owner.HUMAN),
    (Owner.HUMAN, Owner.AUTOMATION),
    (Owner.AUTOMATION, Owner.NONE),
    (Owner.HUMAN, Owner.NONE),
}


class SessionOwner:
    """Thread-safe session ownership with a reentrant lock."""

    def __init__(self) -> None:
        self._owner = Owner.NONE
        self._lock = threading.RLock()

    def current(self) -> Owner:
        with self._lock:
            return self._owner

    def acquire(self, who: Owner) -> None:
        """Transition to *who*.  Raises ``ValueError`` on invalid transition."""
        with self._lock:
            transition = (self._owner, who)
            if transition not in _VALID_TRANSITIONS:
                raise ValueError(
                    f"Invalid ownership transition: {self._owner.value} -> {who.value}"
                )
            self._owner = who

    def release(self, who: Owner) -> None:
        """Release from *who*.  Only the current owner can release.

        After release, ownership transitions to the "other side":
        AUTOMATION -> HUMAN, HUMAN -> AUTOMATION.  Use ``release_to_none``
        for final cleanup.
        """
        with self._lock:
            if self._owner != who:
                raise ValueError(
                    f"Cannot release: current owner is {self._owner.value}, "
                    f"not {who.value}"
                )
            if who == Owner.AUTOMATION:
                self._owner = Owner.HUMAN
            elif who == Owner.HUMAN:
                self._owner = Owner.AUTOMATION
            else:
                raise ValueError("Cannot release from NONE")

    def release_to_none(self) -> None:
        """Final cleanup: set ownership to NONE regardless of who holds it."""
        with self._lock:
            self._owner = Owner.NONE
