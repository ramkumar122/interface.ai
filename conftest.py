"""Pytest bootstrap.

Ensures the repository root is importable so tests can `import db...` and
`import reset_db` regardless of where pytest is invoked from.

Also registers the ``live`` marker (browser + server required) and makes the
skipping of live tests **loud**: a session-end summary names how many live tests
were skipped and why. The surface layer's whole value is browser behaviour, so a
silent skip would mean the core is untested by default. Run them for real with
``pytest -m live`` or ``make test-live``.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live: requires a running CoreDesk on :8001 and an installed Chromium. "
        "Skipped (loudly) otherwise. Run with `pytest -m live`.",
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    skipped = terminalreporter.stats.get("skipped", [])
    live_skips = [r for r in skipped if "live" in getattr(r, "keywords", {})]
    if not live_skips:
        return
    reasons = []
    for r in live_skips:
        reason = ""
        lr = getattr(r, "longrepr", None)
        if isinstance(lr, tuple) and len(lr) == 3:
            reason = lr[2]
        reasons.append(reason)
    unique = sorted({x.replace("Skipped: ", "") for x in reasons if x})
    terminalreporter.write_sep("!", "LIVE TESTS SKIPPED", yellow=True, bold=True)
    terminalreporter.write_line(
        f"{len(live_skips)} live surface test(s) were skipped and did NOT run."
    )
    for u in unique:
        terminalreporter.write_line(f"  reason: {u}")
    terminalreporter.write_line(
        "The surface's browser behaviour is UNTESTED in this run. "
        "Run `make test-live` (CoreDesk on :8001 + `playwright install chromium`)."
    )
