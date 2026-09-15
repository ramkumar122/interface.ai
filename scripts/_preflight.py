"""Checks every harness script runs before it starts costing anything.

A run that launches a browser, signs in, and dies on turn one because the
password is wrong has spent a model call to discover something a single
POST could have told it. Discovery is the expensive case, but the same
failure wastes a minute on every replay script too.

Shared rather than copied into each script: three near-identical
credential checks would drift, and the one that drifted would be the one
nobody ran.

Deliberately dependency-free — `urllib` only, no Playwright, no browser.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_USER = "mreyes"
DEFAULT_PASS = "demo1234"


def credentials() -> tuple[str, str]:
    """What the harness will sign in with. Defaults match the fixtures."""
    return (
        os.environ.get("COREDESK_USER", DEFAULT_USER),
        os.environ.get("COREDESK_PASS", DEFAULT_PASS),
    )


def origin_of(url: str) -> str:
    """`http://host:port` from any URL under it."""
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def answers(url: str, timeout: float = 3.0) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True  # answering, even if not with a 200
    except Exception:
        return False


def signon_accepted(
    origin: str, user: str, password: str, timeout: float = 5.0
) -> bool | None:
    """Does CoreDesk accept these credentials?

    ``True`` accepted, ``False`` rejected, ``None`` couldn't tell.

    CoreDesk redirects to ``/menu`` on success and ``/?err=1`` on failure —
    it never says which field was wrong, so the destination is the only
    signal, and that is enough here.
    """
    body = urllib.parse.urlencode(
        {"userid": user, "password": password}
    ).encode()
    req = urllib.request.Request(
        f"{origin}/signon", data=body, method="POST"
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        resp = urllib.request.build_opener(_NoRedirect).open(req, timeout=timeout)
        location = resp.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        location = e.headers.get("Location", "") if e.headers else ""
    except Exception:
        return None

    if not location:
        return None
    return "/menu" in location


def check_coredesk(target: str) -> list[str]:
    """Reachable, and the credentials work. Returns problems, empty if fine."""
    origin = origin_of(target)
    problems: list[str] = []

    if not answers(origin):
        problems.append(
            f"CoreDesk is not answering on {origin}.\n"
            f"      Start it:  make run"
        )
        return problems  # no point testing credentials against nothing

    user, password = credentials()
    accepted = signon_accepted(origin, user, password)
    if accepted is False:
        problems.append(
            f"CoreDesk rejected COREDESK_USER={user}.\n"
            f"      Check the credentials, or reseed:  python reset_db.py"
        )
    elif accepted is None:
        problems.append(
            f"Could not tell whether {origin} accepts COREDESK_USER={user} —\n"
            f"      /signon did not redirect. Is that really CoreDesk?"
        )
    return problems


def die(problems: list[str], stream=sys.stderr) -> int:
    """Print every problem at once. One at a time wastes a round trip each."""
    print("Cannot start — fix these first:\n", file=stream)
    for i, p in enumerate(problems, 1):
        print(f"  {i}. {p}", file=stream)
    return 2
