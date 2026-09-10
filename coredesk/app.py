"""CoreDesk -- a mock legacy credit-union servicing web app.

FastAPI is used here strictly as an HTML page server. Every route returns a
full HTML page via TemplateResponse; there are no JSON endpoints and no
client-side rendering. Every user action is a plain form POST or an anchor.
"""

import os
from datetime import datetime
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from coredesk.config import (
    fn_function,
    get_cfg,
    get_tenant_key,
    menu_items_for,
    route_for_code,
)
from coredesk.session import COOKIE_NAME, load_session, sign_session
from db.money import cents_to_display
from db.queries import authenticate, find_members, get_member, list_shares

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# App wiring -- JSON docs disabled; this is not an API.
# ---------------------------------------------------------------------------
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# ---------------------------------------------------------------------------
# Session + render helpers
# ---------------------------------------------------------------------------
def current_session(request):
    """Return the signed-in session dict for this request, or None."""
    return load_session(request.cookies.get(COOKIE_NAME))


def render(request, name, status_code=200, **ctx):
    """Render a template with the shared chrome context injected.

    The tenant config is resolved per call via get_cfg(), so nothing caches a
    stale reference and tests can select a tenant with an env var.
    """
    context = {
        "cfg": get_cfg(),
        "today": datetime.now().strftime("%m/%d/%Y"),
        "sess": current_session(request),
    }
    context.update(ctx)
    return templates.TemplateResponse(
        request=request, name=name, context=context, status_code=status_code
    )


# ---------------------------------------------------------------------------
# Presentation helpers (money comes from the data layer; keep IO out of here)
# ---------------------------------------------------------------------------
def _fmt_name(last, first):
    """Render a member name as 'LAST, FIRST' in uppercase."""
    return ("%s, %s" % (last, first)).upper()


def _fmt_date(iso):
    """Reformat a YYYY-MM-DD date string as MM/DD/YYYY (pure string, no locale)."""
    try:
        year, month, day = iso.split("-")
        return "%s/%s/%s" % (month, day, year)
    except (ValueError, AttributeError):
        return iso


def _share_type_label(share_type):
    """Render a share type with spaces instead of underscores."""
    return share_type.replace("_", " ")


def _query(**params):
    """Build a query string from the non-empty params (stable key order)."""
    pairs = [(k, v) for k, v in params.items() if v]
    return urlencode(pairs)


# ---------------------------------------------------------------------------
# Sign-on
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def signon_page(request: Request, err: int = 0):
    if current_session(request):
        return RedirectResponse("/menu", status_code=303)
    return render(request, "signon.html", err=bool(err))


@app.post("/signon")
def do_signon(request: Request, userid: str = Form(""), password: str = Form("")):
    # NOTE: a hidden canary field is posted with this form. We deliberately do
    # not declare, read, echo, or log its value.
    staff = authenticate(userid.strip().lower(), password)
    if staff is not None:
        token = sign_session(
            {
                "username": staff.username,
                "display_name": staff.display_name,
                "role": staff.role,
                "branch": staff.branch,
            }
        )
        resp = RedirectResponse("/menu", status_code=303)
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax")
        return resp
    # Never reveal which field was wrong.
    return RedirectResponse("/?err=1", status_code=303)


@app.get("/signoff")
def signoff(request: Request):
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------
@app.get("/menu", response_class=HTMLResponse)
def menu_page(request: Request, err: int = 0):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    return render(
        request, "menu.html", menu_items=menu_items_for(get_tenant_key()), err=bool(err)
    )


@app.post("/menu")
def menu_fastpath(request: Request, fncode: str = Form("")):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    route = route_for_code(get_tenant_key(), fncode)
    if route:
        return RedirectResponse(route, status_code=303)
    return render(
        request, "menu.html", menu_items=menu_items_for(get_tenant_key()), err=True
    )


# ---------------------------------------------------------------------------
# Member Inquiry
# ---------------------------------------------------------------------------
def _criteria_ctx(fn_token, member_no="", last_name="", error=None):
    """Shared context for the criteria screen (also used on empty-criteria re-render)."""
    function = fn_function(fn_token)
    heading = "MEMBER INQUIRY"
    if function is not None:
        heading = "MEMBER INQUIRY - %s" % function["description"].upper()
    return {
        "heading": heading,
        "fn": function["fn"] if function else "",
        "member_no": member_no,
        "last_name": last_name,
        "error": error,
    }


@app.get("/mbrinq", response_class=HTMLResponse)
def member_inquiry(request: Request, member_no: str = "", last_name: str = "", fn: str = ""):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "mbrinq.html", **_criteria_ctx(fn, member_no, last_name))


@app.get("/mbrinq/results", response_class=HTMLResponse)
def member_inquiry_results(request: Request, member_no: str = "", last_name: str = "", fn: str = ""):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)

    mno = member_no.strip()
    lname = last_name.strip()
    if not mno and not lname:
        # Empty criteria: re-render the criteria screen with a validation message.
        ctx = _criteria_ctx(fn, error="Enter a member number or last name.")
        return render(request, "mbrinq.html", **ctx)

    members = find_members(mno or None, lname or None)
    function = fn_function(fn)
    action_suffix = function["member_action"] if function else None

    rows = []
    for m in members:
        href = "/member/%s" % m.member_no
        if action_suffix:
            href = "%s/%s" % (href, action_suffix)
        rows.append(
            {
                "member_no": m.member_no,
                "name": _fmt_name(m.last_name, m.first_name),
                "status": m.status,
                "joined": _fmt_date(m.joined_on),
                "branch": m.branch,
                "action_href": href,
            }
        )

    inquiry_href = "/mbrinq"
    qs = _query(member_no=mno, last_name=lname, fn=(function["fn"] if function else ""))
    if qs:
        inquiry_href = "/mbrinq?%s" % qs

    return render(
        request,
        "mbrinq_results.html",
        rows=rows,
        count=len(rows),
        inquiry_href=inquiry_href,
    )


# ---------------------------------------------------------------------------
# Member record + shares panel
# ---------------------------------------------------------------------------
@app.get("/member/{member_no}", response_class=HTMLResponse)
def member_record(request: Request, member_no: str):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)

    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)

    banner = None
    if m.status in ("INACTIVE", "RESTRICTED"):
        banner = "Member status is %s. Some functions are unavailable." % m.status

    record = {
        "member_no": m.member_no,
        "name": _fmt_name(m.last_name, m.first_name),
        "status": m.status,
        "joined": _fmt_date(m.joined_on),
        "branch": m.branch,
        "phone": m.phone,
    }
    return render(request, "member_record.html", m=record, banner=banner, member_no=m.member_no)


@app.get("/member/{member_no}/shares", response_class=HTMLResponse)
def member_shares(request: Request, member_no: str):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)

    m = get_member(member_no)
    if m is None:
        return render(request, "member_shares.html", not_found=True, rows=[])

    rows = []
    for s in list_shares(member_no):
        rows.append(
            {
                "suffix": s.suffix,
                "type": _share_type_label(s.share_type),
                "description": s.description,
                "available": cents_to_display(s.balance_available_cents),
                "ledger": cents_to_display(s.balance_ledger_cents),
                "status": s.status,
                "is_open": s.status == "OPEN",
            }
        )
    return render(request, "member_shares.html", not_found=False, rows=rows)


# ---------------------------------------------------------------------------
# Member-scoped not-implemented stubs -- so the record nav strip never dead-ends.
# Every path here is more specific than /member/{member_no}, so route order is
# irrelevant.
# ---------------------------------------------------------------------------
def _not_implemented(request):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "notimpl.html")


@app.get("/member/{member_no}/cards", response_class=HTMLResponse)
def stub_member_cards(request: Request, member_no: str):
    return _not_implemented(request)


@app.get("/member/{member_no}/address", response_class=HTMLResponse)
def stub_member_address(request: Request, member_no: str):
    return _not_implemented(request)


@app.get("/member/{member_no}/transactions", response_class=HTMLResponse)
def stub_member_transactions(request: Request, member_no: str):
    return _not_implemented(request)


@app.get("/member/{member_no}/shares/new", response_class=HTMLResponse)
def stub_member_shares_new(request: Request, member_no: str):
    return _not_implemented(request)
