"""FastAPI routes for the operator console.

Routes only: resolve, delegate, render.  The logic lives in `catalog.py`,
`describe.py`, `review.py` and `runner.py` so it can be tested without a
client, and so a route that grows a branch is a signal something belongs
in a module.

This is our UI, not CoreDesk's.  CoreDesk's template rules — nested
tables, WebForms ids, no JSON — are its costume, and they are scoped to
`coredesk/` and `db/`.  They do not apply here, and imitating them would
make the console harder to read for no gain.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from control.approval import (
    ApprovalIntegrityError,
    ApprovalPreconditionError,
    approve,
    revoke,
    write_artifact,
)
from control.policy import Mode
from control.redact import redact
from operator_console import discovery as discovery_mod
from operator_console import result_view, transcript_view
from operator_console.runner import RUNNER, RunnerBusy
from operator_console.catalog import (
    AmbiguousCapability,
    CapabilityNotFound,
    CatalogEntry,
    load_catalog,
    newer_versions,
    resolve,
)
from operator_console.describe import count_rejected, describe_steps
from operator_console.review import (
    APPROVAL_INTRO,
    CHECKLIST,
    ROLES,
    checklist_for,
    describe_recoverable,
    evidence_section,
    outcome_rows,
    validate_approval,
)

HERE = Path(__file__).resolve().parent
EVIDENCE_ROOT = Path(
    os.environ.get("OPERATOR_EVIDENCE_DIR", "evidence")
).resolve()


def evidence_root() -> Path:
    """Where evidence is read from, resolved at call time.

    A module constant captured at import cannot be redirected, which tied
    the console's tests to whatever happened to be in the real `evidence/`
    directory — a directory whose whole purpose is to be regenerated.  Five
    tests hardcoded a run id and failed the moment it was cleared.
    """
    return EVIDENCE_ROOT
TARGET_ORIGIN = os.environ.get("OPERATOR_TARGET", "http://127.0.0.1:8001")

# Only these are served from evidence/.  A screenshot and a transcript are
# what the screens link to; anything else is a path the console has no
# reason to hand out.
_SERVABLE = {
    ".png": "image/png",
    ".json": "application/json",
    ".jsonl": "text/plain; charset=utf-8",
}

app = FastAPI(title="Operator Console", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

templates = Jinja2Templates(directory=str(HERE / "templates"))

# Every runtime value reaches a template through this filter.  A `read`
# step's output is page text, and the canary rule has no exceptions.
templates.env.filters["redact"] = redact
templates.env.globals["describe_steps"] = describe_steps
templates.env.globals["target_origin"] = TARGET_ORIGIN


def _page(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx)


def _not_found(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "not_found.html", {"message": message}, status_code=404
    )


# --------------------------------------------------------------------------- #
# Screen 1 — catalog
# --------------------------------------------------------------------------- #


@app.get("/", response_class=HTMLResponse)
def catalog(request: Request) -> Response:
    cat = load_catalog()
    return _page(request, "catalog.html", catalog=cat)


@app.get("/evidence/{rel_path:path}")
def evidence_file(request: Request, rel_path: str) -> Response:
    """Serve one file from `evidence/`.

    Screens link screenshots and transcripts the system already wrote.
    The guard is resolve-then-contain rather than a string check on `..`,
    because a symlink inside evidence/ would pass the string check and
    still escape.
    """
    root = evidence_root()
    candidate = (root / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return _not_found(request, "That path is outside evidence/.")

    if not candidate.is_file():
        return _not_found(request, f"No evidence file at {rel_path!r}.")

    media_type = _SERVABLE.get(candidate.suffix)
    if media_type is None:
        return _not_found(
            request,
            f"{candidate.suffix!r} files are not served from evidence/.",
        )

    return Response(content=candidate.read_bytes(), media_type=media_type)


@app.get("/capability/{key}", response_class=HTMLResponse)
def capability(request: Request, key: str) -> Response:
    cat = load_catalog()
    try:
        entry = resolve(key, cat)
    except (CapabilityNotFound, AmbiguousCapability) as exc:
        return _not_found(request, str(exc))
    return _page(
        request, "capability.html", entry=entry, prefill={},
        newer=newer_versions(entry, cat),
        resolved_from_bare_id="@" not in key,
    )


# --------------------------------------------------------------------------- #
# Screens 2-4 — run, result, intervention
# --------------------------------------------------------------------------- #


@app.post("/run/{key}")
async def start_run(request: Request, key: str) -> Response:
    """Queue a replay and redirect to its status page.

    The path converter is `{key}`, not `{key:path}`: a greedy one matches
    `operator-1234/resume` as a capability key and shadows the resume route
    below, which shares this method and prefix.  A capability key is always
    one segment.

    `async` because the input names are dynamic — they come from
    `artifact.inputs` — so the form cannot be declared as typed parameters
    and has to be read from the request. That is safe here precisely
    because this route never touches the browser: it posts a job to the
    runner's worker thread and returns at once.
    """
    cat = load_catalog()
    try:
        entry = resolve(key, cat)
    except (CapabilityNotFound, AmbiguousCapability) as exc:
        return _not_found(request, str(exc))

    form = await request.form()
    inputs = {i.name: str(form.get(i.name, "")) for i in entry.artifact.inputs}
    watch = request.query_params.get("watch") == "1"

    # An approved artifact runs the production path.  A draft cannot: the
    # pre-replay hash check refuses it, and the reviewer would get a
    # `(pre-replay)` failure instead of a demonstration.  Verification uses
    # DISCOVERY for exactly this reason, and the screen says which it used.
    mode = Mode.REPLAY if entry.runnable else Mode.DISCOVERY
    if not entry.runnable and not watch:
        return _page(
            request, "capability.html", entry=entry, prefill=inputs,
            newer=newer_versions(entry, cat), resolved_from_bare_id=False,
        )

    try:
        state = RUNNER.start(
            entry.artifact, entry.key, inputs, policy_mode=mode, watch=watch
        )
    except RunnerBusy as exc:
        active = RUNNER.active()
        return _page(
            request, "busy.html", message=str(exc), active=active,
        )

    return RedirectResponse(f"/run/{state.run_id}/status", status_code=303)


@app.get("/run/{run_id}/status", response_class=HTMLResponse)
def run_status(request: Request, run_id: str) -> Response:
    state = RUNNER.get(run_id)
    if state is None:
        return _not_found(request, f"No run {run_id!r}. Run state is in memory.")

    if state.is_discovery:
        ds = state.discovery
        loop = ds.loop
        obs = loop.current_observation if loop is not None else None
        return _page(
            request, "discover_run.html", state=state, ds=ds,
            view=transcript_view.read_transcript(
                ds.transcript_path, run_id=state.run_id
            ),
            nodes=discovery_mod.picker_nodes(obs),
            obs=obs,
        )

    view = None
    if state.result is not None:
        view = result_view.build(
            state.result, state.artifact, state.inputs,
            evidence_root=evidence_root().name,
        )
    return _page(request, "run.html", state=state, view=view)


@app.post("/run/{run_id}/resume")
def resume_run(request: Request, run_id: str) -> Response:
    """Signal that the human is done.

    The `ReplayEscalated` result is passed back to `engine.resume()` whole.
    The resume token is opaque: it is displayed, never parsed.
    """
    state = RUNNER.get(run_id)
    if state is None:
        return _not_found(request, f"No run {run_id!r}.")
    if state.is_discovery:
        return _not_found(
            request,
            "This is a discovery pause, not a replay escalation. "
            "Use Hand off to model.",
        )
    try:
        RUNNER.resume(state)
    except RunnerBusy as exc:
        return _page(request, "busy.html", message=str(exc), active=state)
    return RedirectResponse(f"/run/{run_id}/status", status_code=303)


@app.post("/run/{run_id}/human-step")
async def human_step(request: Request, run_id: str) -> Response:
    """Record one operator action while discovery is paused."""
    from agent.loop import build_action

    state = RUNNER.get(run_id)
    if state is None or not state.is_discovery:
        return _not_found(request, f"No paused discovery {run_id!r}.")

    form = await request.form()
    kind = str(form.get("action", "")).strip()
    ref = str(form.get("ref", "")).strip()
    args: dict = {}
    if kind == "navigate":
        args["url"] = str(form.get("url", "")).strip()
    elif kind in ("click", "type", "select", "read"):
        args["ref"] = ref
    if kind == "type":
        from_input = str(form.get("from_input", "")).strip()
        text = str(form.get("text", "")).strip()
        if from_input:
            args["from_input"] = from_input
        elif text:
            args["text"] = text
    if kind == "select":
        args["option"] = str(form.get("option", "")).strip()
    if kind == "read":
        args["into_output"] = str(form.get("into_output", "")).strip()

    ds = state.discovery
    try:
        action = build_action(
            kind, args,
            input_names=frozenset(ds.inputs),
            output_names=frozenset(ds.outputs),
        )
    except (ValueError, KeyError) as exc:
        ds.human_error = str(exc)
        return RedirectResponse(f"/run/{run_id}/status", status_code=303)

    try:
        RUNNER.human_step(state, action)
    except RunnerBusy as exc:
        return _page(request, "busy.html", message=str(exc), active=state)
    return RedirectResponse(f"/run/{run_id}/status", status_code=303)


@app.post("/run/{run_id}/handoff")
def handoff_model(request: Request, run_id: str) -> Response:
    """Give the paused discovery back to the model."""
    state = RUNNER.get(run_id)
    if state is None or not state.is_discovery:
        return _not_found(request, f"No paused discovery {run_id!r}.")
    try:
        RUNNER.handoff_model(state)
    except RunnerBusy as exc:
        return _page(request, "busy.html", message=str(exc), active=state)
    return RedirectResponse(f"/run/{run_id}/status", status_code=303)


# --------------------------------------------------------------------------- #
# Discovery — the natural-language entry point
# --------------------------------------------------------------------------- #

DEFAULT_GOAL_PLACEHOLDER = (
    "Find member 100101 and read their primary savings "
    "(SAVINGS suffix 0000) available balance."
)

# The types `replay/engine.py::_coerce_output` actually validates.
#
# Only `money` is checked — it parses through `db.money.display_to_cents`
# and fails the run if it cannot.  Everything else passes through
# untouched, which is why the list is two entries and not three: offering
# `number` would name a validation that does not exist, and an output
# labelled `number` would accept "OPEN" exactly as `text` does.  A type
# that promises checking and does none is worse than no type at all —
# that coercion is what caught the STATUS bug.
OUTPUT_TYPES = ["money", "text"]

# Defaults for the common case, not properties of the system.  Inputs start
# with member_no because almost every CoreDesk capability needs it.  Outputs
# start empty: a lock or a navigation has nothing to return, and stuffing
# ``savings_balance`` in the form made every discovery look like a balance
# read until the user noticed.
DEFAULT_INPUT_ROWS = [
    {"name": "member_no", "value": "100101", "value2": "100110"}
]
DEFAULT_OUTPUT_ROWS: list[dict[str, str]] = []


def _discover_form(request: Request, **over) -> Response:
    ctx = {
        "blocked": discovery_mod.key_missing_reason(),
        "model": discovery_mod.model_name(),
        "goal_placeholder": DEFAULT_GOAL_PLACEHOLDER,
        "target_default": TARGET_ORIGIN + "/menu",
        "goal": "",
        "capability_id": "",
        "verify_runs": 2,
        "input_rows": [dict(r) for r in DEFAULT_INPUT_ROWS],
        "output_rows": [dict(r) for r in DEFAULT_OUTPUT_ROWS],
        "output_types": OUTPUT_TYPES,
        "errors": [],
        "recordings": transcript_view.available_recordings(evidence_root()),
    }
    ctx.update(over)
    return _page(request, "discover.html", **ctx)


@app.get("/discover", response_class=HTMLResponse)
def discover(request: Request) -> Response:
    resp = _discover_form(request)
    # The form defaults live in Python. A stale process plus a reloaded
    # template is how a "fixed" page still showed savings_balance.
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _input_rows(form) -> list[dict]:
    """Read the input rows: one declared name, two records’ worth of values.

    Both values live on the same row deliberately.  A separate "second
    record" block would let someone name a parameter in set two that was
    never declared in set one — a state this shape cannot represent, which
    beats one the form has to detect.
    """
    names = form.getlist("input_name")
    values = form.getlist("input_value")
    seconds = form.getlist("input_value2")
    # zip() would silently truncate if a browser omitted a field; pad instead
    # so a short list surfaces as a blank the validator reports.
    def at(xs, i):
        return xs[i] if i < len(xs) else ""
    return [
        {"name": at(names, i), "value": at(values, i), "value2": at(seconds, i)}
        for i in range(len(names))
    ]


def _rows(form, prefix: str, second: str) -> list[dict]:
    """Read the repeated name/value rows back out of the posted form."""
    names = form.getlist(f"{prefix}_name")
    seconds = form.getlist(f"{prefix}_{second}")
    rows = [
        {"name": n, second: s}
        for n, s in zip(names, seconds)
    ]
    return rows


@app.post("/discover")
async def start_discovery(request: Request) -> Response:
    """Validate, then queue a discovery on the runner's worker thread."""
    form = await request.form()
    goal = str(form.get("goal", "")).strip()
    target = str(form.get("target", "")).strip() or TARGET_ORIGIN + "/menu"
    capability_id = str(form.get("capability_id", "")).strip()
    try:
        verify_runs = int(str(form.get("verify_runs", "2")))
    except ValueError:
        verify_runs = 2

    input_rows = _input_rows(form)
    output_rows = _rows(form, "output", "type")

    def redisplay(**extra):
        base = dict(
            goal=goal, capability_id=capability_id, verify_runs=verify_runs,
            input_rows=input_rows, output_rows=output_rows,
            target_default=target,
        )
        base.update(extra)
        return _discover_form(request, **base)

    # "Add another row" is a submit button, so the form round-trips through
    # the server instead of needing JavaScript to grow.
    if form.get("add_input") is not None:
        input_rows.append({"name": "", "value": "", "value2": ""})
        return redisplay()
    if form.get("add_output") is not None:
        output_rows.append({"name": "", "type": OUTPUT_TYPES[0]})
        return redisplay()

    blocked = discovery_mod.key_missing_reason()
    if blocked:
        return redisplay(errors=[blocked])

    errors = []
    if not goal:
        errors.append("A goal is required — that is what this screen is for.")
    if not capability_id:
        errors.append("A capability id is required (e.g. coredesk.member.read_x).")
    inputs = {r["name"]: r["value"] for r in input_rows if r["name"].strip()}
    outputs = {r["name"]: r["type"] for r in output_rows if r["name"].strip()}

    # The second record, checked here rather than left to `verify_artifact`.
    # That gate refuses the same two cases, but only after the model has run
    # and the credits are spent — technically correct and practically
    # useless.  A capability with no declared inputs cannot be run two ways,
    # and the gate does not ask it to.
    second = {
        r["name"]: r["value2"] for r in input_rows if r["name"].strip()
    }
    if inputs:
        if not all(v.strip() for v in second.values()):
            errors.append(
                "Every input needs a value for the second record. A "
                "capability is only verified once it has replayed against "
                "another record."
            )
        elif second == inputs:
            errors.append(
                "The second record must differ from the first — the same "
                "values twice prove determinism, not portability."
            )
    if errors:
        return redisplay(errors=errors)

    model = discovery_mod.model_name()
    run_id = discovery_mod.new_run_id(model)
    state = discovery_mod.DiscoveryState(
        goal=goal, capability_id=capability_id, model=model,
        inputs=inputs, outputs=outputs, verify_runs=verify_runs,
        evidence_dir=evidence_root() / run_id,
        verify_also=[second] if inputs else [],
    )

    try:
        run = RUNNER.start_discovery(state, entry_url=target)
    except RunnerBusy as exc:
        return _page(request, "busy.html", message=str(exc),
                     active=RUNNER.active())

    return RedirectResponse(f"/run/{run.run_id}/status", status_code=303)


@app.get("/discover/rehearse", response_class=HTMLResponse)
def rehearse(request: Request) -> Response:
    """Replay a saved transcript, step by step, with no model and no browser.

    Recording the demo takes several takes, and each live one costs credits
    and depends on the model behaving the same way twice.  This also lets
    someone who cloned the repo without a key see what a discovery looks
    like.
    """
    run_id = request.query_params.get("run_id", "")
    try:
        step = int(request.query_params.get("step", "1"))
    except ValueError:
        step = 1

    if not run_id:
        return _page(
            request, "rehearse_pick.html",
            recordings=transcript_view.available_recordings(evidence_root()),
        )

    root = evidence_root()
    path = (root / run_id / "transcript.jsonl").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return _not_found(request, "That path is outside evidence/.")
    if not path.is_file():
        return _not_found(request, f"No transcript for run {run_id!r}.")

    view = transcript_view.read_transcript(
        path, limit=max(step, 1), recorded=True, run_id=run_id
    )
    full = transcript_view.read_transcript(path, recorded=True, run_id=run_id)
    return _page(
        request, "rehearse.html", view=view, run_id=run_id,
        step=step, total=len(full.turns),
        at_end=step >= len(full.turns),
    )


# --------------------------------------------------------------------------- #
# Screens 5-7 — review queue, review detail, approve
# --------------------------------------------------------------------------- #


@app.get("/review", response_class=HTMLResponse)
def review_queue(request: Request) -> Response:
    cat = load_catalog()
    return _page(request, "review_queue.html", catalog=cat)


def _review_context(entry: CatalogEntry) -> dict:
    art = entry.artifact
    return {
        "entry": entry,
        "artifact": art,
        "steps": describe_steps(art),
        "rejected_count": count_rejected(art),
        "outcomes": outcome_rows(art),
        "recoverables": [
            (r, describe_recoverable(r)) for r in art.recoverables
        ],
        "evidence": evidence_section(art, evidence_root()),
        "checklist": checklist_for(art),
        "approval_intro": APPROVAL_INTRO,
        "roles": ROLES,
        # Byte for byte from disk, never `model_dump_json()`.  A reviewer
        # who hashes what this block shows must get the stored hash; a
        # re-serialisation with different key order or spacing would not.
        "artifact_source": _read_source(entry.path),
        "transcript": _transcript_link(art.created_from.run_id),
    }


def _read_source(path: Path) -> str:
    try:
        return path.read_text()
    except OSError as exc:
        return f"(could not read {path}: {exc})"


def _transcript_link(run_id: str) -> dict:
    """The discovery run this was compiled from, if its evidence survives."""
    d = evidence_root() / run_id
    tpath = d / "transcript.jsonl"
    return {
        "run_id": run_id,
        "exists": tpath.is_file(),
        "href": f"/evidence/{run_id}/transcript.jsonl",
        "rehearse": f"/discover/rehearse?run_id={run_id}&step=1",
    }


@app.get("/review/{key}", response_class=HTMLResponse)
def review_detail(request: Request, key: str) -> Response:
    try:
        entry = resolve(key)
    except (CapabilityNotFound, AmbiguousCapability) as exc:
        return _not_found(request, str(exc))
    return _page(request, "review_detail.html", **_review_context(entry))


@app.post("/review/{key}/decision", response_class=HTMLResponse)
def review_decision(
    request: Request,
    key: str,
    decision: str = Form(...),
    reviewer: str = Form(""),
    role: str = Form(""),
    checked: list[str] = Form(default=[]),
) -> Response:
    try:
        entry = resolve(key)
    except (CapabilityNotFound, AmbiguousCapability) as exc:
        return _not_found(request, str(exc))

    ctx = _review_context(entry)
    ctx["submitted"] = {"reviewer": reviewer, "role": role, "checked": checked}

    if decision in ("request_changes", "reject"):
        return _page(request, "review_detail.html",
                     **_decide_negative(entry, decision, ctx))

    submission, errors = validate_approval(reviewer, role, checked)
    if submission is None:
        ctx["errors"] = errors
        return _page(request, "review_detail.html", **ctx)

    try:
        approved = approve(entry.artifact, submission.approver)
        write_artifact(entry.path, approved)
    except (ApprovalPreconditionError, ApprovalIntegrityError) as exc:
        # The message is the system's own, rendered as-is.  Rephrasing it
        # would make the console and the CLI disagree about the same refusal.
        ctx["errors"] = [str(exc)]
        return _page(request, "review_detail.html", **ctx)

    # Back to the catalog, not the review page: the capability appearing in
    # the list is the state change, and it should happen in front of whoever
    # made it.
    return RedirectResponse(f"/?approved={entry.key}", status_code=303)


def _decide_negative(entry: CatalogEntry, decision: str, ctx: dict) -> dict:
    """Reject / request changes.

    On a draft this writes nothing, because the artifact is already draft
    and `control/approval.py` has no `reject()`.  Saying so beats reporting
    a state change that did not happen.
    """
    if entry.status == "approved" and decision == "reject":
        revoked = revoke(entry.artifact)
        write_artifact(entry.path, revoked)
        ctx["notice"] = (
            f"Approval revoked. {entry.capability_id}@{entry.version} is now "
            f"'revoked' and its content hash is cleared. Replay refuses it."
        )
        return ctx

    verb = "Changes requested" if decision == "request_changes" else "Rejected"
    ctx["notice"] = (
        f"{verb}. Nothing was written: the artifact is already "
        f"'{entry.status}', and the approval model has no separate rejected "
        f"state. Record your reasoning where the review was raised — the "
        f"artifact stores who approved and against which hash, nothing else."
    )
    return ctx
