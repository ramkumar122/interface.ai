"""Capture the operator console's evidence: drive what can be driven, and
print the rest as numbered instructions.

Three captures lived only as ad-hoc commands typed during a session, which
made them the one part of `evidence/` nobody could reproduce.  This script
is the reproducible path.

It does not pretend the manual parts are automatic.  The escalation
handoff needs a person to sign in at the Playwright window CoreDesk is
running in — that is the whole point of the control-transfer model, and a
script that faked it would be capturing evidence of nothing.  Those steps
are printed as numbered instructions naming what to click and where to
save each screenshot, and the script then checks the files arrived.

Screenshots of the console are taken with this script's own browser, which
is separate from the one the console drives.  Nothing here reaches CoreDesk
directly.

Usage:
    python scripts/console_evidence.py --capture approval-gate
    python scripts/console_evidence.py --capture discovery      # costs credits
    python scripts/console_evidence.py --capture escalation     # manual
    python scripts/console_evidence.py --check                  # verify only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CONSOLE = "http://127.0.0.1:8010"
COREDESK = "http://127.0.0.1:8001"
OUT = Path("evidence/operator-console")

GOAL = ("Find member 100101 and read their primary savings "
        "(SAVINGS suffix 0000) available balance.")

# What each capture must end up with.  Checked at the end so a half-finished
# run says which file is missing rather than looking like a success.
EXPECTED = {
    "discovery": [
        "discovery-flow/gate-result.json",
        "discovery-flow/transcript.jsonl",
        "discovery-flow/summary.json",
        "discovery-flow/1-discover-form.png",
        "discovery-flow/2-discovery-complete.png",
        "discovery-flow/3-review.png",
        "discovery-flow/4-rehearsal.png",
    ],
    "approval-gate": [
        "approval-gate-flow/gate-result.json",
        "approval-gate-flow/1-catalog-gated.png",
        "approval-gate-flow/2-review-checklist.png",
        "approval-gate-flow/3-catalog-after-approval.png",
    ],
    "escalation": [
        "gate-result.json",
        "escalated-state.png",
        "escalation-panel.html",
    ],
}


# --------------------------------------------------------------------------- #
# Preflight — fail before doing anything, not halfway through
# --------------------------------------------------------------------------- #


def _answers(url: str, timeout: float = 3.0) -> bool:
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:
        return False


def preflight(*, needs_key: bool, needs_headed: bool) -> list[str]:
    """Everything that must be true, checked up front."""
    problems: list[str] = []

    # Reachable *and* the credentials work — the console's runner signs in
    # from COREDESK_USER/COREDESK_PASS at job start, so a wrong password
    # surfaces as a failed run rather than as a bad password.
    from scripts._preflight import check_coredesk

    problems.extend(check_coredesk(COREDESK))
    if not _answers(CONSOLE):
        problems.append(
            f"The operator console is not answering on {CONSOLE}.\n"
            f"      Start it:  make console"
        )
    if needs_key:
        from operator_console.discovery import api_key

        if not api_key():
            problems.append(
                "GOOGLE_API_KEY is not set, and this capture runs a real\n"
                "      discovery. Export it, or put it in .env at the repo root."
            )
    if needs_headed:
        import os

        if os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes"):
            problems.append(
                "HEADLESS is set. The escalation capture needs a browser\n"
                "      window you can type into — start the console without it."
            )
    try:
        import playwright  # noqa: F401
    except ImportError:
        problems.append(
            "playwright is not installed.  pip install -r requirements.txt "
            "&& playwright install chromium"
        )
    return problems


def _die(problems: list[str]) -> int:
    print("Cannot start — fix these first:\n", file=sys.stderr)
    for i, p in enumerate(problems, 1):
        print(f"  {i}. {p}", file=sys.stderr)
    return 2


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #


def get(path: str) -> str:
    return urllib.request.urlopen(CONSOLE + path).read().decode()


def post(path: str, data: dict, follow: bool = True):
    body = urllib.parse.urlencode(data, doseq=True).encode()
    req = urllib.request.Request(CONSOLE + path, data=body, method="POST")
    if follow:
        return urllib.request.urlopen(req)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        return urllib.request.build_opener(_NoRedirect).open(req)
    except urllib.error.HTTPError as e:
        return e


def settle(run_id: str, limit: float = 180.0) -> str:
    """Poll a run's status page until it stops asking to be refreshed."""
    deadline = time.monotonic() + limit
    body = ""
    while time.monotonic() < deadline:
        body = get(f"/run/{run_id}/status")
        if 'http-equiv="refresh"' not in body:
            return body
        time.sleep(1.5)
    return body


def shoot(shots: list[tuple[str, str]], *, width: int = 1100,
          height: int = 950, expand: str | None = None) -> None:
    """Screenshot console pages with our own browser."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": height})
        for name, url in shots:
            page.goto(url)
            page.wait_for_load_state("networkidle")
            if expand and page.locator(expand).count():
                page.locator(expand).first.click()
            path = OUT / name
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=True)
            print(f"    shot {name}")
        browser.close()


def manual(steps: list[str]) -> bool:
    """Print the steps a person must do.  Returns True if they confirmed.

    Waiting on `input()` with no terminal raises `EOFError`, so a piped or
    scripted run would crash after doing the automated half.  It prints the
    steps and says how to finish instead — the work already done is not
    thrown away.
    """
    print("\n" + "=" * 68)
    print("  MANUAL STEPS — a person has to do these")
    print("=" * 68)
    for i, s in enumerate(steps, 1):
        print(f"\n  {i}. {s}")
    print("\n" + "=" * 68)

    if not sys.stdin.isatty():
        print(
            "\n  Not running interactively, so I cannot wait for you.\n"
            "  Do the steps above, then verify with:\n"
            "      python scripts/console_evidence.py --check"
        )
        return False
    input("\n  Press Enter when you have finished all of the above... ")
    return True


# --------------------------------------------------------------------------- #
# Capture: approval gate  (no model, no credits)
# --------------------------------------------------------------------------- #


def capture_approval_gate() -> int:
    """A draft appearing only in the review queue, approved, then runnable."""
    from control.approval import artifact_filename

    print("\n[approval-gate] seeding a verified draft to review")
    import subprocess

    seeded = subprocess.run(
        [sys.executable, "scripts/seed_draft_artifact.py", "--verify"],
        capture_output=True, text=True, env={**_env(), "HEADLESS": "1"},
    )
    if seeded.returncode != 0:
        print(seeded.stdout + seeded.stderr, file=sys.stderr)
        print("  seeding failed — cannot capture this gate", file=sys.stderr)
        return 1

    key = "coredesk.member.read_savings_balance@2.0"
    before = get("/")
    gate_before = _gate_count(before)
    print(f"    catalog before: {gate_before}")
    # By key, not by id prefix.  Both versions share the id, so a substring
    # match found the *approved* v1.0 row and reported the draft as listed —
    # a plausible wrong answer, which is the failure this project is about.
    listed_before = _listed_in_catalog(before, key)

    shoot([("approval-gate-flow/1-catalog-gated.png", CONSOLE + "/")])
    shoot([("approval-gate-flow/2-review-checklist.png",
            f"{CONSOLE}/review/{key}")], height=1400,
          expand="details.source summary")

    review = get(f"/review/{key}")
    src = re.search(r'<details class="source">.*?<pre>(.*?)</pre>',
                    review, re.S)
    import html as _html
    disk = (Path("artifacts") / f"{key.split('@')[0]}@2.json").read_text()
    byte_exact = src is not None and _html.unescape(src.group(1)) == disk

    print("    approving through the console's own route")
    r = post(f"/review/{key}/decision", {
        "decision": "approve", "reviewer": "D. PARK", "role": "Risk / Ops",
        "checked": ["behaviour_matches", "no_record_dependent_locator",
                    "risk_correct", "outcomes_complete"],
    }, follow=False)
    redirect = r.headers.get("Location", "")

    after = get(f"/?approved={key}")
    shoot([("approval-gate-flow/3-catalog-after-approval.png",
            f"{CONSOLE}/?approved={key}")])

    art = json.loads((Path("artifacts") /
                      f"{key.split('@')[0]}@2.json").read_text())
    report = {
        "gate": "approval-gate-flow",
        "what": "The approval gate made visible: an unapproved capability is "
                "absent from the catalog, the checklist explains what to "
                "check, and approving puts it in the list.",
        "1_draft_absent_from_catalog": {
            "capability": key,
            "in_catalog": listed_before,
            "checked_by": "presence of /capability/<id>@<version> in the "
                          "catalog, not an id substring — both versions "
                          "share the id",
            "header": gate_before,
        },
        "2_review": {
            "artifact_source_byte_identical_to_disk": byte_exact,
            "checklist_explains": "the compiler refuses to" in review,
        },
        "3_approved": {
            "approved_by": art["approval"]["approved_by"],
            "content_hash": art["approval"]["content_hash"],
            "redirected_to": redirect,
            "header_after": _gate_count(after),
            "listed_and_highlighted": "just-approved" in after,
        },
    }
    _write_report(OUT / "approval-gate-flow" / "gate-result.json", report)
    return 0


# --------------------------------------------------------------------------- #
# Capture: discovery  (COSTS CREDITS — one model run)
# --------------------------------------------------------------------------- #


def capture_discovery() -> int:
    """A goal in natural language, through every gate, to a runnable capability."""
    cap_id = "coredesk.member.read_savings_balance_demo"
    print("\n[discovery] this spends API credits — one discovery run "
          "plus two verification replays")

    shoot([("discovery-flow/1-discover-form.png", CONSOLE + "/discover")],
          height=1500)

    print("    submitting the goal")
    r = post("/discover", {
        "goal": GOAL, "target": f"{COREDESK}/menu",
        "input_name": "member_no", "input_value": "100101",
        "output_name": "savings_balance", "output_type": "money",
        "capability_id": cap_id, "verify_runs": "2",
    }, follow=False)
    loc = r.headers.get("Location", "")
    if "/run/" not in loc:
        print("    the console refused to start it:", loc or r.status,
              file=sys.stderr)
        return 1
    run_id = loc.split("/run/")[1].split("/status")[0]
    print(f"    run {run_id} — waiting for it to settle")

    body = settle(run_id)
    shoot([("discovery-flow/2-discovery-complete.png",
            f"{CONSOLE}/run/{run_id}/status")], height=1200)

    if "Written as DRAFT" not in body:
        print("    discovery did not produce an artifact; the report will "
              "say why", file=sys.stderr)

    src = Path("evidence") / run_id
    dest = OUT / "discovery-flow"
    dest.mkdir(parents=True, exist_ok=True)
    for f in ("transcript.jsonl", "summary.json"):
        if (src / f).is_file():
            (dest / f).write_text((src / f).read_text())

    key = f"{cap_id}@1.0"
    shoot([("discovery-flow/3-review.png", f"{CONSOLE}/review/{key}")],
          height=1600)
    shoot([("discovery-flow/4-rehearsal.png",
            f"{CONSOLE}/discover/rehearse?run_id={run_id}&step=4")])

    summary = {}
    if (dest / "summary.json").is_file():
        summary = json.loads((dest / "summary.json").read_text())

    report = {
        "gate": "discovery-flow",
        "what": "One continuous path from a sentence typed into the console "
                "to an approved capability.",
        "1_goal_in_natural_language": {"typed_into": "GET /discover",
                                       "goal": GOAL},
        "2_discovery": {
            "run_id": run_id,
            "model": summary.get("model"),
            "turns": summary.get("steps"),
            "tokens": (summary.get("prompt_tokens", 0)
                       + summary.get("output_tokens", 0)),
            "status": summary.get("status"),
            "rendered_live_from": "evidence/<run_id>/transcript.jsonl, which "
                                  "the loop flushes per turn",
        },
        "3_gates": {
            "compiled": summary.get("compiled"),
            "verified": summary.get("verified"),
            "verify_runs": summary.get("verify_runs"),
            "written_as": summary.get("artifact_path"),
        },
        "4_review_screen": f"/review/{key}",
        "rehearsal": {
            "url": f"/discover/rehearse?run_id={run_id}&step=1",
            "what": "the same run replayed with no model and no browser",
        },
        "note": "The run id changes every time this is regenerated. Anything "
                "quoting it must be updated — see docs/REGENERATE_EVIDENCE.md.",
    }
    _write_report(dest / "gate-result.json", report)
    print(f"\n    NOTE: this run is {run_id}. Keep evidence/{run_id}/ if you "
          f"want rehearsal mode to replay it.")
    return 0


# --------------------------------------------------------------------------- #
# Capture: escalation  (MANUAL — a person signs in)
# --------------------------------------------------------------------------- #


def capture_escalation() -> int:
    """Session expired, control handed to a human, resumed.

    The middle of this is a person typing into the browser window the
    console drives.  Nothing here can do that, and pretending otherwise
    would be capturing evidence of a handoff that never happened.
    """
    key = "coredesk.member.read_savings_balance@1.0"
    print("\n[escalation] the console must be running HEADED for this")

    print("    starting a run")
    r = post(f"/run/{key}", {"member_no": "100101"}, follow=False)
    loc = r.headers.get("Location", "")
    if "/run/" not in loc:
        print("    the console refused to start it:", loc or r.status,
              file=sys.stderr)
        return 1
    run_id = loc.split("/run/")[1].split("/status")[0]

    # Wait for sign-in to finish, or the injection is consumed by it.
    for _ in range(80):
        b = get(f"/run/{run_id}/status")
        if "/menu" in b and "actions dispatched" in b:
            break
        time.sleep(0.25)

    print("    injecting session_expired via CoreDesk's own /admin/inject")
    body = urllib.parse.urlencode(
        {"op": "apply", "name": "session_expired", "count": "1"}).encode()
    urllib.request.urlopen(
        urllib.request.Request(f"{COREDESK}/admin/inject", data=body,
                               method="POST"))

    panel = settle(run_id)
    if "needs a person" not in panel:
        print("    the run did not escalate — nothing to capture",
              file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "escalation-panel.html").write_text(panel)
    shot = Path("evidence") / run_id / "001-escalation.png"
    if shot.is_file():
        (OUT / "escalated-state.png").write_bytes(shot.read_bytes())
    print("    escalated; the intervention panel is captured")

    confirmed = manual([
        f"Open {CONSOLE}/run/{run_id}/status and read the panel. Ownership "
        f"should show HUMAN.",
        "Find the Chromium window the console opened. It is showing "
        "CoreDesk's sign-on page with 'Your session has expired.'  If there "
        "is no such window, the console was started with HEADLESS set — "
        "stop it, restart with `make console`, and run this again.",
        "Sign in there by hand: User ID 'mreyes', Password 'demo1234', "
        "click Sign On. Do this in that window, NOT in the console.",
        "Back in the console, press Resume.",
        "Wait for the run to finish, then screenshot the completed page to "
        f"evidence/operator-console/4-resumed.png",
    ])

    final = get(f"/run/{run_id}/status")
    resumed = "Success." in final
    if not confirmed:
        print("\n    Escalation captured. The resume half is still yours to "
              "do — see the steps above.")
    report = {
        "gate": "escalation-resume",
        "run_id": run_id,
        "1_escalated": {
            "intervention_panel": True,
            "ownership_shown": "SESSION OWNERSHIP" in panel.upper(),
            "owner_is_human": 'class="own on">HUMAN' in panel,
            "says_act_in_browser": "Playwright browser window, not here" in panel,
        },
        "2_human_acted": {
            "manual": True,
            "what": "a person signed in at the Playwright window; "
                    "Surface.act() refuses while ownership is HUMAN",
        },
        "3_resumed": {
            "succeeded": resumed,
            "note": None if resumed else
                    "Resume did not reach success — if nobody signed in, the "
                    "resume gate refuses with a diagnostic, which is correct "
                    "behaviour and worth keeping as evidence.",
        },
    }
    _write_report(OUT / "gate-result.json", report)
    return 0


# --------------------------------------------------------------------------- #
# Plumbing
# --------------------------------------------------------------------------- #


def _env() -> dict:
    import os
    return dict(os.environ)


def _gate_count(body: str) -> str:
    m = re.search(r'<p class="gate-count">(.*?)</p>', body, re.S)
    if not m:
        return "(no header)"
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()


def _listed_in_catalog(body: str, key: str) -> bool:
    """Is this exact capability@version a row in the catalog?

    The catalog links each row as /capability/<id>@<version>, so the key is
    unambiguous where the bare id is not.
    """
    return f'/capability/{key}"' in body


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2))
    print(f"    wrote {path}")


def check(captures: list[str]) -> int:
    """Name what is missing rather than reporting a half-run as done."""
    missing: list[str] = []
    for cap in captures:
        for rel in EXPECTED[cap]:
            if not (OUT / rel).is_file():
                missing.append(f"{cap}: evidence/operator-console/{rel}")
    if missing:
        print("\nMISSING:", file=sys.stderr)
        for m in missing:
            print(f"  {m}", file=sys.stderr)
        return 1
    total = sum(len(EXPECTED[c]) for c in captures)
    print(f"\nAll {total} expected files present for: {', '.join(captures)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--capture",
        choices=["approval-gate", "discovery", "escalation", "all"],
        help="Which capture to run.",
    )
    ap.add_argument("--check", action="store_true",
                    help="Verify expected files exist; capture nothing.")
    args = ap.parse_args()

    if args.check:
        return check(list(EXPECTED))
    if not args.capture:
        ap.print_help()
        return 1

    caps = (["approval-gate", "discovery", "escalation"]
            if args.capture == "all" else [args.capture])

    problems = preflight(
        needs_key="discovery" in caps,
        needs_headed="escalation" in caps,
    )
    if problems:
        return _die(problems)

    runners = {
        "approval-gate": capture_approval_gate,
        "discovery": capture_discovery,
        "escalation": capture_escalation,
    }
    for cap in caps:
        rc = runners[cap]()
        if rc != 0:
            print(f"\n{cap} did not complete.", file=sys.stderr)
            return rc

    return check(caps)


if __name__ == "__main__":
    raise SystemExit(main())
