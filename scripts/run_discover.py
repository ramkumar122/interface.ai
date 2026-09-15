"""Thin harness: sign in, run the discovery loop, print the summary.

Lives in ``scripts/`` (not ``agent/``) because it needs Playwright for sign-in
and browser launch — and ``agent/`` must never import Playwright (boundary rule).

Usage:

    GOOGLE_API_KEY=... GEMINI_MODEL=gemini-3.6-flash \
        python scripts/run_discover.py \
        --goal "Read the member's available savings balance" \
        --target http://127.0.0.1:8001/menu \
        --inputs '{"member_no": "100101"}' \
        --outputs '{"savings_balance": "money"}'

Fails cleanly when ``GOOGLE_API_KEY`` is unset — a clear message, not a stack
trace from inside the SDK.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

# Ensure repo root is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Where the console is reachable, for the review prompt printed on success.
# A constant with an override, not a string inside a print.
OPERATOR_CONSOLE_URL = os.environ.get(
    "OPERATOR_CONSOLE_URL", "http://127.0.0.1:8010"
)


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from a repo-root .env (gitignored) into os.environ."""
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _print_review_prompt(artifact, path: Path, verify_n: int) -> None:
    """What to do next.  The lifecycle is real in the code and was invisible
    in the flow — the script wrote a draft and exited, and you had to already
    know the console existed to find it."""
    major = artifact.version.split(".")[0]
    key = f"{artifact.capability_id}@{major}"
    print(f"\n\u2713 Verified {verify_n}/{verify_n} runs \u2014 outputs matched")
    print(f"\u2713 Artifact written: {path}")
    print("\n  Status: DRAFT \u2014 cannot run unattended until approved.\n")
    print(f"  Review:  python -m control review {path}")
    print(f"  Console: {OPERATOR_CONSOLE_URL}/review/{key}")


def main() -> int:
    _load_dotenv()

    ap = argparse.ArgumentParser(description="Run a discovery loop against CoreDesk.")
    ap.add_argument("--goal", required=True, help="The goal in plain English.")
    ap.add_argument("--target", required=True, help="Entry-point URL (e.g. http://127.0.0.1:8001/menu).")
    ap.add_argument("--inputs", default="{}", help="JSON dict of declared inputs.")
    ap.add_argument("--outputs", default="{}", help="JSON dict of declared outputs (name -> type).")
    ap.add_argument("--capability-id", default=None, help="Capability ID (e.g. coredesk.member.read_savings_balance).")
    ap.add_argument("--verify-runs", type=int, default=2, help="Number of verification replays (0 to skip).")
    ap.add_argument(
        "--verify-inputs-2", default=None,
        help="JSON dict: a SECOND parameter set, with different values. "
             "Verification needs it — running the same values twice proves "
             "determinism, not that the capability works for another record.",
    )
    ap.add_argument("--inject-break", default=None,
                    help="Testing only: mutate last read step's column_header to this value before verification.")
    args = ap.parse_args()

    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print(
            "ERROR: GOOGLE_API_KEY is not set.\n"
            "Set it in the environment or in a .env file at the repo root.\n"
            "Get a key from https://aistudio.google.com/apikey",
            file=sys.stderr,
        )
        return 2

    # Checked before the first model call, not after it: a rejected
    # sign-in would otherwise cost a discovery run to discover.
    from scripts._preflight import check_coredesk, die

    problems = check_coredesk(args.target)
    if problems:
        return die(problems)

    model = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
    inputs = json.loads(args.inputs)
    outputs = json.loads(args.outputs)
    verify_also = (
        [json.loads(args.verify_inputs_2)] if args.verify_inputs_2 else None
    )

    from playwright.sync_api import sync_playwright

    from agent.brain import GeminiBrain
    from agent.pipeline import discover_and_verify
    from surface.web import WebSurface

    config_path = Path(__file__).resolve().parents[1] / "surfaces" / "coredesk.yaml"
    run_id = f"discover-{model}-{uuid.uuid4().hex[:8]}"
    evidence_dir = Path("evidence") / run_id

    user = os.environ.get("COREDESK_USER", "mreyes")
    pw = os.environ.get("COREDESK_PASS", "demo1234")

    brain = GeminiBrain(model=model, api_key=api_key)

    with sync_playwright() as p:
        headless = os.environ.get("HEADLESS", "").lower() in ("1", "true", "yes")
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        # Sign in (harness only — credentials never enter the model context)
        origin = args.target.split("//")[0] + "//" + args.target.split("//")[1].split("/")[0]
        page.goto(f"{origin}/")
        page.fill("#ctl00_MainContent_txtUserId", user)
        page.fill("#ctl00_MainContent_txtPassword", pw)
        page.click("#ctl00_MainContent_btnSignOn")
        page.wait_for_load_state("networkidle")

        surface = WebSurface(
            page,
            evidence_dir=evidence_dir,
            config_path=config_path,
            inputs=inputs,
        )
        page.goto(args.target)
        page.wait_for_load_state("networkidle")

        # ---- Loop, compile, verify: one shared pipeline ----
        # `agent/pipeline.py` holds this so the console runs the same code.
        # The browser stays here: `agent/` must never import Playwright.
        def surface_factory():
            vpage = browser.new_page()
            vpage.goto(f"{origin}/")
            vpage.fill("#ctl00_MainContent_txtUserId", user)
            vpage.fill("#ctl00_MainContent_txtPassword", pw)
            vpage.click("#ctl00_MainContent_btnSignOn")
            vpage.wait_for_load_state("networkidle")
            vsurface = WebSurface(
                vpage, evidence_dir=evidence_dir,
                config_path=config_path, inputs=inputs,
            )
            vpage.goto(args.target)
            vpage.wait_for_load_state("networkidle")
            return vsurface

        cap_id = args.capability_id or f"coredesk.{run_id}"

        # --inject-break mutates the compiled artifact before verification,
        # so the gate evidence can show verification catching a break.
        def _inject_break(art):
            if not args.inject_break:
                return
            for step in reversed(art.steps):
                if step.action == "read" and step.target.column_header:
                    original = step.target.column_header
                    step.target.column_header = args.inject_break
                    print(
                        f"INJECTED BREAK: column_header {original!r} -> "
                        f"{args.inject_break!r}", file=sys.stderr,
                    )
                    break

        pipe = discover_and_verify(
            surface, brain,
            goal=args.goal, inputs=inputs, outputs=outputs,
            capability_id=cap_id, run_id=run_id, model=model,
            evidence_dir=evidence_dir, entry_url=args.target,
            verify_runs=args.verify_runs,
            verify_also=verify_also,
            surface_factory=surface_factory,
            on_compiled=_inject_break,
        )
        result = pipe.result
        artifact = pipe.artifact
        compile_error = pipe.compile_error
        verify_ok = pipe.verified
        verify_n = args.verify_runs

        if compile_error:
            print(f"COMPILATION FAILED: {compile_error}", file=sys.stderr)
        if pipe.verify_result is not None and not verify_ok:
            print(
                f"VERIFICATION FAILED: {pipe.verify_result.failure_summary}",
                file=sys.stderr,
            )
            print("No artifact was written.", file=sys.stderr)

        browser.close()

    # ---- Save artifact (only if verified) ----
    artifact_path = None
    if artifact is not None and verify_ok:
        # Through `write_artifact`, not `write_text`: the one code path that
        # *produces* artifacts was the one path that never checked them, so
        # `ApprovalIntegrityError` could not fire on the writer most likely
        # to need it.  `artifact_filename` is shared so two writers cannot
        # disagree about the name and leave two files for one capability.
        from control.approval import artifact_filename, write_artifact

        artifact_path = Path("artifacts") / artifact_filename(artifact)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        write_artifact(artifact_path, artifact)
        _print_review_prompt(artifact, artifact_path, verify_n)
    elif artifact is not None and verify_n == 0:
        # Compiled, deliberately not verified, and therefore not written.
        # Say so: the absence of a "written" line is not a signal, and this
        # is the one outcome where nothing went wrong and nothing was kept.
        print(
            f"\nCompiled {len(artifact.steps)} steps, but --verify-runs 0 "
            f"was passed.\n"
            f"  No artifact was written: the machine gate must pass before "
            f"an artifact exists.\n"
            f"  Re-run with --verify-runs 1 or more to keep it.",
            file=sys.stderr,
        )

    summary = pipe.summary(
        run_id=run_id, model=model, goal=args.goal,
        artifact_path=str(artifact_path) if artifact_path else None,
    )
    (evidence_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0 if verify_ok or artifact is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
