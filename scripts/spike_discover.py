"""SCRATCH discovery spike — an LLM drives CoreDesk through the Surface.

This is the throwaway proof that a model can complete a goal from the
accessibility tree alone. It is the ONLY file that imports ``google.genai``; when
``agent/brain.py`` lands this disappears. Not a permanent module, not unit
-tested — exercised by running it.

Usage (CoreDesk must be live on the target host):

    GOOGLE_API_KEY=... GEMINI_MODEL=gemini-2.5-pro \\
    ./.venv/bin/python scripts/spike_discover.py \\
        --goal "Read member 100101's primary savings available balance." \\
        --target http://127.0.0.1:8001/menu \\
        --inputs '{"member_no": "100101"}' \\
        --outputs '{"savings_balance": "money"}'

Credentials come from COREDESK_USER / COREDESK_PASS (default mreyes/demo1234)
and are used by the harness sign-in only — the model never sees them, and
`surface/` never learns that authentication exists.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from a repo-root .env (gitignored) into os.environ.

    Lightweight (no python-dotenv dependency); does not overwrite values already
    set in the real environment.
    """
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

from surface.base import (  # noqa: E402
    Click,
    Done,
    Escalate,
    GiveUp,
    Navigate,
    Read,
    Select,
    Type,
    action_function_declarations,
)
from surface.web import WebSurface  # noqa: E402

CANARY = "CANARY-A7F3-DONOTLOG"
MAX_STEPS = 25
MAX_SECONDS = 300
CONFIG = Path(__file__).resolve().parents[1] / "surfaces" / "coredesk.yaml"

RULES = """You are operating a legacy web application through an accessibility
view. Each turn you receive the current page as roles + accessible names, each
line tagged with a [ref=eN] valid for THIS observation only. You must respond
with exactly one function call.

Rules:
- Act only on a ref present in the current observation.
- Use the declared inputs exactly; never invent a value (e.g. a member number).
- When typing a declared input, prefer from_input=<input name> over literal text.
- Read a value by the cell under its column header, not by position.
- You are NOT told the routes or where data lives; navigate by what you see.
- Before calling done, state in the rationale what proves the goal is met.
- If you cannot proceed or the app blocks you, call escalate or give_up."""


def redact(text: str) -> str:
    return (text or "").replace(CANARY, "[REDACTED-CANARY]")


def observation_text(obs) -> str:
    parts = [f"URL: {obs.url}", f"TITLE: {obs.title}"]
    if obs.warnings:
        parts.append("WARNINGS: " + "; ".join(obs.warnings))
    for path, yaml_text in obs.frames.items():
        parts.append(f"\n[FRAME {path}]\n{yaml_text}")
    return redact("\n".join(parts))


def build_action(name: str, args: dict):
    if name == "navigate":
        return Navigate(url=args["url"])
    if name == "click":
        return Click(ref=args["ref"])
    if name == "type":
        return Type(ref=args["ref"], from_input=args.get("from_input"), text=args.get("text"))
    if name == "select":
        return Select(ref=args["ref"], option=args["option"])
    if name == "read":
        return Read(ref=args["ref"], into_output=args["into_output"])
    if name == "done":
        return Done(rationale=args.get("rationale", ""))
    if name == "escalate":
        return Escalate(reason=args.get("reason", ""))
    if name == "give_up":
        return GiveUp(rationale=args.get("rationale", ""))
    raise ValueError(f"unknown action {name}")


def sign_in(page, base: str) -> None:
    user = os.environ.get("COREDESK_USER", "mreyes")
    pw = os.environ.get("COREDESK_PASS", "demo1234")
    origin = base.split("//")[0] + "//" + base.split("//")[1].split("/")[0]
    page.goto(f"{origin}/")
    page.fill("#ctl00_MainContent_txtUserId", user)
    page.fill("#ctl00_MainContent_txtPassword", pw)
    page.click("#ctl00_MainContent_btnSignOn")
    page.wait_for_load_state("networkidle")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--inputs", default="{}")
    ap.add_argument("--outputs", default="{}")
    args = ap.parse_args()

    inputs = json.loads(args.inputs)
    outputs = json.loads(args.outputs)
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    if "GOOGLE_API_KEY" not in os.environ:
        print("GOOGLE_API_KEY is not set", file=sys.stderr)
        return 2

    from google import genai
    from google.genai import types

    run_id = f"spike-{model}-{uuid.uuid4().hex[:8]}"
    evidence_dir = Path("evidence") / run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    transcript = (evidence_dir / "transcript.jsonl").open("w")

    decls = action_function_declarations(list(inputs), list(outputs))
    cfg = types.GenerateContentConfig(
        temperature=0,
        system_instruction=RULES,
        tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**d) for d in decls])],
        tool_config=types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="ANY")
        ),
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

    filled: dict[str, str] = {}
    prompt_tokens = out_tokens = 0
    started = time.time()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        sign_in(page, args.target)
        surface = WebSurface(
            page, evidence_dir=evidence_dir, config_path=CONFIG, inputs=inputs
        )
        page.goto(args.target)
        page.wait_for_load_state("networkidle")

        goal_msg = (
            f"GOAL: {args.goal}\nINPUTS: {json.dumps(inputs)}\n"
            f"OUTPUTS TO FILL: {list(outputs)}\n"
        )
        obs = surface.observe()
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=goal_msg + "\n" + observation_text(obs))],
            )
        ]

        last_hash = None
        want_screenshot = False
        status = "max_steps"

        for step in range(1, MAX_STEPS + 1):
            if time.time() - started > MAX_SECONDS:
                status = "timeout"
                break

            resp = client.models.generate_content(model=model, contents=contents, config=cfg)
            um = resp.usage_metadata
            if um:
                prompt_tokens += um.prompt_token_count or 0
                out_tokens += um.candidates_token_count or 0
            cand = resp.candidates[0]
            contents.append(cand.content)  # preserves any thought_signature

            fc = next(
                (pt.function_call for pt in cand.content.parts if pt.function_call), None
            )
            if fc is None:
                status = "no_function_call"
                break

            name, fargs = fc.name, dict(fc.args)
            action = build_action(name, fargs)
            result = surface.act(action)
            if isinstance(action, Read) and result.ok and result.detail is not None:
                filled[action.into_output] = result.detail

            transcript.write(
                redact(
                    json.dumps(
                        {
                            "step": step,
                            "obs_hash": obs.hash,
                            "prompt_tokens": um.prompt_token_count if um else None,
                            "call": name,
                            "args": fargs,
                            "ok": result.ok,
                            "reason": result.reason,
                            "detail": result.detail,
                        }
                    )
                )
                + "\n"
            )
            transcript.flush()

            if name in ("done", "give_up", "escalate"):
                status = name
                break

            # stuck detection: same hash twice -> ask for a screenshot next time
            page.wait_for_load_state("networkidle")
            obs = surface.observe(screenshot=want_screenshot)
            want_screenshot = obs.hash == last_hash
            last_hash = obs.hash

            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name=name,
                            response={
                                "ok": result.ok,
                                "reason": result.reason,
                                "detail": redact(result.detail or ""),
                            },
                        ),
                        types.Part.from_text(text=observation_text(obs)),
                    ],
                )
            )

        browser.close()

    summary = {
        "run_id": run_id,
        "model": model,
        "goal": args.goal,
        "status": status,
        "steps": step,
        "prompt_tokens": prompt_tokens,
        "output_tokens": out_tokens,
        "seconds": round(time.time() - started, 1),
        "outputs_filled": filled,
    }
    (evidence_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    transcript.close()
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
