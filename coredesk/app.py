"""CoreDesk -- a mock legacy credit-union servicing web app.

FastAPI is used here strictly as an HTML page server. Every route returns a
full HTML page via TemplateResponse; there are no JSON endpoints and no
client-side rendering. Every user action is a plain form POST or an anchor.
"""

import os
from datetime import date, datetime
from urllib.parse import urlencode

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from coredesk.config import (
    STATE_CODES,
    fn_function,
    get_cfg,
    get_tenant_key,
    menu_items_for,
    route_for_code,
)
from coredesk.inject import (
    CONDITIONS,
    InjectionMiddleware,
    clear_injection,
    injection_state,
    set_injection,
)
from coredesk.session import COOKIE_NAME, load_session, sign_session
from db.money import cents_to_display, display_to_cents
from db.queries import (
    authenticate,
    cancel_share_request,
    commit_share_request,
    create_share_request,
    find_members,
    get_card,
    get_member,
    get_primary_savings,
    get_share,
    get_share_request,
    list_cards,
    list_shares,
    list_transactions,
    set_card_status,
    update_member_address,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# App wiring -- JSON docs disabled; this is not an API.
# ---------------------------------------------------------------------------
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(InjectionMiddleware)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# ---------------------------------------------------------------------------
# Session + render helpers
# ---------------------------------------------------------------------------
def current_session(request):
    """Return the signed-in session dict for this request, or None.

    Honors the `readonly_role` runtime injection: when active, the effective role
    is forced to TELLER_RO for this request without touching the real cookie.
    """
    sess = load_session(request.cookies.get(COOKIE_NAME))
    if sess and getattr(request.state, "inject", None) == "readonly_role":
        sess = dict(sess)
        sess["role"] = "TELLER_RO"
    return sess


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


def _fmt_amount(cents):
    """Accounting-style amount: negatives in parentheses, no currency symbol."""
    if cents < 0:
        return "(%s)" % cents_to_display(-cents)
    return cents_to_display(cents)


def _mask_last4(last4):
    """Render the last four digits masked so they can't read as a full PAN."""
    return "****" + last4


def _linked_suffix(linked_share_id):
    """The suffix segment of a linked share id (SHR-100101-0070 -> 0070), or a dash."""
    if not linked_share_id:
        return "\u2014"  # em dash
    return linked_share_id.rsplit("-", 1)[-1]


def _parse_mmddyyyy(value):
    """Parse an MM/DD/YYYY string to a date, or None if unparseable."""
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").date()
    except (ValueError, AttributeError):
        return None


def _validate_address(line1, city, state, zip_code, eff):
    """Validate address fields. Returns (errors_by_field, effective_date_or_None)."""
    errors = {}
    if not line1.strip():
        errors["line1"] = "Address line 1 is required."
    if not city.strip():
        errors["city"] = "City is required."
    if not state.strip():
        errors["state"] = "State is required."
    digits = zip_code.strip().replace("-", "")
    if not (digits.isdigit() and len(digits) in (5, 9)):
        errors["zip"] = "ZIP code must be 5 or 9 digits."
    eff_date = None
    if not eff.strip():
        errors["eff"] = "Effective date must be MM/DD/YYYY."
    else:
        eff_date = _parse_mmddyyyy(eff)
        if eff_date is None:
            errors["eff"] = "Effective date must be MM/DD/YYYY."
        elif eff_date < date.today():
            errors["eff"] = "Effective date cannot be in the past."
    return errors, eff_date


def _query(**params):
    """Build a query string from the non-empty params (stable key order)."""
    pairs = [(k, v) for k, v in params.items() if v]
    return urlencode(pairs)


# ---------------------------------------------------------------------------
# Sign-on
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def signon_page(request: Request, err: int = 0, expired: int = 0):
    if current_session(request):
        return RedirectResponse("/menu", status_code=303)
    return render(request, "signon.html", err=bool(err), expired=bool(expired))


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
# Runtime-condition control page.
# Under /admin so the middleware never injects it and the policy layer can
# exclude it from the agent allowlist by prefix. Off the agent allowlist.
# ---------------------------------------------------------------------------
@app.get("/admin/inject", response_class=HTMLResponse)
def admin_inject(request: Request):
    return render(request, "inject_admin.html", conditions=CONDITIONS, state=injection_state())


@app.post("/admin/inject")
def admin_inject_apply(
    request: Request, op: str = Form(""), name: str = Form(""), count: str = Form("1"),
):
    if op == "clear":
        clear_injection()
    elif op == "apply" and name in CONDITIONS:
        try:
            n = int(count)
        except ValueError:
            n = 1
        set_injection(name, n)
    return RedirectResponse("/admin/inject", status_code=303)


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
# Card Maintenance (first write feature)
# ---------------------------------------------------------------------------
_CARD_ACTION_STATUS = {"LOCK": "LOCKED", "UNLOCK": "ACTIVE", "HOTLIST": "HOTLISTED"}


def _card_for(member_no, card_id):
    """Return the card only if it belongs to this member, else None."""
    c = get_card(card_id)
    if c is None or c.member_no != member_no:
        return None
    return c


def _card_detail(c):
    return {
        "card_id": c.card_id,
        "network": c.network.replace("_", " "),
        "last4": _mask_last4(c.last4),
        "status": c.status,
        "linked": _linked_suffix(c.linked_share_id),
        "issued": _fmt_date(c.issued_on),
    }


@app.get("/member/{member_no}/cards", response_class=HTMLResponse)
def cards_list(request: Request, member_no: str):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)
    member_name = _fmt_name(m.last_name, m.first_name)
    if m.status == "RESTRICTED":
        return render(
            request, "cards_list.html", member_no=member_no, member_name=member_name,
            rows=[], blocked="Member status is RESTRICTED. This function is unavailable.",
        )
    rows = []
    for c in list_cards(member_no):
        rows.append(
            {
                "card_id": c.card_id,
                "network": c.network.replace("_", " "),
                "last4": _mask_last4(c.last4),
                "status": c.status,
                "linked": _linked_suffix(c.linked_share_id),
                "maint_href": "/member/%s/cards/%s/maint" % (member_no, c.card_id),
            }
        )
    return render(
        request, "cards_list.html", member_no=member_no, member_name=member_name, rows=rows
    )


@app.get("/member/{member_no}/cards/{card_id}/maint", response_class=HTMLResponse)
def card_maint(request: Request, member_no: str, card_id: str, ref: str = ""):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)
    if m.status == "RESTRICTED":
        return render(
            request, "card_maint.html", member_no=member_no,
            blocked="Member status is RESTRICTED. This function is unavailable.",
        )
    c = _card_for(member_no, card_id)
    if c is None:
        return render(request, "card_maint.html", member_no=member_no, card=None)
    banner = ("Card updated. Reference: %s." % ref) if ref else None
    return render(
        request, "card_maint.html", member_no=member_no, card=_card_detail(c), banner=banner
    )


@app.post("/member/{member_no}/cards/{card_id}/maint")
def card_maint_apply(
    request: Request,
    member_no: str,
    card_id: str,
    action: str = Form(""),
    reason: str = Form(""),
    notes: str = Form(""),
):
    sess = current_session(request)
    if not sess:
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)

    def _re(kind, message):
        """Re-render the maint screen with an error/info message and re-filled input."""
        c = _card_for(member_no, card_id)
        ctx = {
            "member_no": member_no,
            "card": _card_detail(c) if c else None,
            "sel_action": action,
            "sel_notes": notes,
            kind: message,
        }
        return render(request, "card_maint.html", **ctx)

    if m.status == "RESTRICTED":
        return render(
            request, "card_maint.html", member_no=member_no,
            blocked="Member status is RESTRICTED. This function is unavailable.",
        )
    if sess.get("role") == "TELLER_RO":
        return _re("info", "Your role does not permit this function.")

    c = _card_for(member_no, card_id)
    if c is None:
        return render(request, "card_maint.html", member_no=member_no, card=None)
    if c.status == "HOTLISTED":
        return _re("info", "Card is hotlisted and cannot be modified.")
    if c.status == "EXPIRED":
        return _re("info", "Card is expired and cannot be modified.")
    if not reason:
        return _re("error", "Reason is required.")

    new_status = _CARD_ACTION_STATUS.get(action)
    if new_status is None:
        return _re("error", "Select an action.")
    if action == "LOCK" and c.status == "LOCKED":
        return _re("info", "Card is already locked. No change applied.")
    if action == "UNLOCK" and c.status == "ACTIVE":
        return _re("info", "Card is already active. No change applied.")

    actor = getattr(request.state, "actor", "HUMAN")
    ref = set_card_status(card_id, new_status, reason, notes, sess["username"], actor)
    return RedirectResponse(
        "/member/%s/cards/%s/maint?ref=%s" % (member_no, card_id, ref), status_code=303
    )


# ---------------------------------------------------------------------------
# Member-scoped not-implemented stubs -- so the record nav strip never dead-ends.
# Every path here is more specific than /member/{member_no}, so route order is
# irrelevant.
# ---------------------------------------------------------------------------
def _not_implemented(request):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    return render(request, "notimpl.html")


# ---------------------------------------------------------------------------
# Address Maintenance
# ---------------------------------------------------------------------------
_ADDR_BLOCKED = "Member status is RESTRICTED. This function is unavailable."
_ADDR_RO = "Your role does not permit this function."


def _current_address(m):
    return {
        "line1": m.addr_line1,
        "line2": m.addr_line2 or "",
        "city": m.city,
        "state": m.state,
        "zip": m.zip,
    }


@app.get("/member/{member_no}/address", response_class=HTMLResponse)
def address_form(request: Request, member_no: str, ref: str = "", nochange: int = 0):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)
    if m.status == "RESTRICTED":
        return render(request, "address_form.html", member_no=member_no, blocked=_ADDR_BLOCKED)
    current = _current_address(m)
    vals = dict(current)
    vals["eff"] = ""
    banner = ("Address updated. Reference: %s." % ref) if ref else None
    info = "No change to apply." if nochange else None
    return render(
        request, "address_form.html", member_no=member_no, current=current, vals=vals,
        errors={}, states=STATE_CODES, banner=banner, info=info,
    )


@app.post("/member/{member_no}/address")
def address_validate(
    request: Request, member_no: str,
    line1: str = Form(""), line2: str = Form(""), city: str = Form(""),
    state: str = Form(""), zip_code: str = Form("", alias="zip"), eff: str = Form(""),
):
    sess = current_session(request)
    if not sess:
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)
    if m.status == "RESTRICTED":
        return render(request, "address_form.html", member_no=member_no, blocked=_ADDR_BLOCKED)

    vals = {"line1": line1, "line2": line2, "city": city, "state": state, "zip": zip_code, "eff": eff}
    if sess.get("role") == "TELLER_RO":
        return render(
            request, "address_form.html", member_no=member_no, current=_current_address(m),
            vals=vals, errors={}, states=STATE_CODES, info=_ADDR_RO,
        )

    errors, _ = _validate_address(line1, city, state, zip_code, eff)
    if errors:
        return render(
            request, "address_form.html", member_no=member_no, current=_current_address(m),
            vals=vals, errors=errors, states=STATE_CODES,
        )
    qs = urlencode(
        {
            "line1": line1.strip(), "line2": line2.strip(), "city": city.strip(),
            "state": state, "zip": zip_code.strip(), "eff": eff.strip(),
        }
    )
    return RedirectResponse("/member/%s/address/review?%s" % (member_no, qs), status_code=303)


@app.get("/member/{member_no}/address/review", response_class=HTMLResponse)
def address_review(request: Request, member_no: str):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)
    if m.status == "RESTRICTED":
        return render(request, "address_review.html", member_no=member_no, blocked=_ADDR_BLOCKED)

    q = request.query_params
    new = {k: q.get(k, "") for k in ("line1", "line2", "city", "state", "zip", "eff")}
    if not (new["line1"] and new["city"] and new["state"] and new["zip"] and new["eff"]):
        return render(request, "address_review.html", member_no=member_no, pending=False)

    rows = [
        ("Address Line 1", m.addr_line1, new["line1"]),
        ("Address Line 2", m.addr_line2 or "", new["line2"]),
        ("City", m.city, new["city"]),
        ("State", m.state, new["state"]),
        ("ZIP Code", m.zip, new["zip"]),
        ("Effective Date", "\u2014", new["eff"]),
    ]
    return render(request, "address_review.html", member_no=member_no, pending=True, rows=rows, new=new)


@app.post("/member/{member_no}/address/commit")
def address_commit(
    request: Request, member_no: str,
    line1: str = Form(""), line2: str = Form(""), city: str = Form(""),
    state: str = Form(""), zip_code: str = Form("", alias="zip"), eff: str = Form(""),
):
    sess = current_session(request)
    if not sess:
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)
    if m.status == "RESTRICTED":
        return render(request, "address_form.html", member_no=member_no, blocked=_ADDR_BLOCKED)

    vals = {"line1": line1, "line2": line2, "city": city, "state": state, "zip": zip_code, "eff": eff}
    if sess.get("role") == "TELLER_RO":
        return render(
            request, "address_form.html", member_no=member_no, current=_current_address(m),
            vals=vals, errors={}, states=STATE_CODES, info=_ADDR_RO,
        )

    errors, _ = _validate_address(line1, city, state, zip_code, eff)
    if errors:
        return render(
            request, "address_form.html", member_no=member_no, current=_current_address(m),
            vals=vals, errors=errors, states=STATE_CODES,
        )

    # Idempotent guard: if nothing actually changed, do not write or audit.
    unchanged = (
        line1.strip() == m.addr_line1
        and line2.strip() == (m.addr_line2 or "")
        and city.strip() == m.city
        and state == m.state
        and zip_code.strip() == m.zip
    )
    if unchanged:
        return RedirectResponse("/member/%s/address?nochange=1" % member_no, status_code=303)

    actor = getattr(request.state, "actor", "HUMAN")
    ref = update_member_address(
        member_no, line1.strip(), line2.strip() or None, city.strip(), state,
        zip_code.strip(), eff.strip(), sess["username"], actor,
    )
    return RedirectResponse("/member/%s/address?ref=%s" % (member_no, ref), status_code=303)


# ---------------------------------------------------------------------------
# Transaction History (read-only)
# ---------------------------------------------------------------------------
_TXN_PAGE_SIZE = 10


@app.get("/member/{member_no}/transactions", response_class=HTMLResponse)
def transactions(request: Request, member_no: str):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)

    shares = list_shares(member_no)
    share_options = [{"share_id": s.share_id, "label": "%s - %s" % (s.suffix, s.description)} for s in shares]
    ps = get_primary_savings(member_no)
    default_share = ps.share_id if ps else (shares[0].share_id if shares else "")

    q = request.query_params
    sel_share = q.get("share") or default_share
    today = date.today()
    from_str = q.get("from") or (today - timedelta(days=30)).strftime("%m/%d/%Y")
    to_str = q.get("to") or today.strftime("%m/%d/%Y")
    try:
        offset = max(0, int(q.get("offset", "0")))
    except ValueError:
        offset = 0

    fd = _parse_mmddyyyy(from_str)
    td = _parse_mmddyyyy(to_str)

    error = None
    rows = []
    total = 0
    if fd and td and fd > td:
        error = "From date must be on or before To date."
    elif sel_share:
        found = list_transactions(
            sel_share, fd.isoformat() if fd else None, td.isoformat() if td else None
        )
        found = sorted(found, key=lambda t: (t.posted_on, t.txn_id), reverse=True)
        total = len(found)
        for t in found[offset:offset + _TXN_PAGE_SIZE]:
            rows.append(
                {
                    "date": _fmt_date(t.posted_on),
                    "description": t.description,
                    "type": t.txn_type,
                    "amount": _fmt_amount(t.amount_cents),
                }
            )

    def _page_href(new_offset):
        qs = _query(share=sel_share, **{"from": from_str, "to": to_str})
        return "/member/%s/transactions?%s&offset=%d" % (member_no, qs, new_offset)

    prev_href = _page_href(offset - _TXN_PAGE_SIZE) if offset > 0 else None
    next_href = _page_href(offset + _TXN_PAGE_SIZE) if (offset + _TXN_PAGE_SIZE) < total else None

    return render(
        request, "transactions.html", member_no=member_no, shares=share_options,
        sel_share=sel_share, from_str=from_str, to_str=to_str, rows=rows,
        error=error, prev_href=prev_href, next_href=next_href,
    )


# ---------------------------------------------------------------------------
# Share Opening (irreversible capability; agent stops at review)
# ---------------------------------------------------------------------------
_SHARE_TYPE_OPTIONS = [
    ("PRIMARY_SAVINGS", "Regular Share"),
    ("MONEY_MARKET", "Money Market"),
    ("CERTIFICATE", "Certificate 12mo"),
]
_SHARE_TYPE_LABELS = dict(_SHARE_TYPE_OPTIONS)


def _open_funding_shares(member_no):
    out = []
    for s in list_shares(member_no):
        if s.status == "OPEN":
            out.append(
                {
                    "share_id": s.share_id,
                    "label": "%s - %s - %s"
                    % (s.suffix, s.description, cents_to_display(s.balance_available_cents)),
                }
            )
    return out


def _empty_share_vals():
    return {"share_type": "", "description": "", "deposit": "", "funding_share_id": ""}


@app.get("/member/{member_no}/shares/new", response_class=HTMLResponse)
def share_new_form(request: Request, member_no: str, cancelled: int = 0):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)
    if m.status == "INACTIVE":
        return render(request, "share_new_form.html", member_no=member_no,
                      blocked="Member status is INACTIVE. Accounts cannot be opened.")
    if m.status == "RESTRICTED":
        return render(request, "share_new_form.html", member_no=member_no,
                      blocked="Member status is RESTRICTED. This function is unavailable.")
    return render(
        request, "share_new_form.html", member_no=member_no,
        share_types=_SHARE_TYPE_OPTIONS, funding=_open_funding_shares(member_no),
        vals=_empty_share_vals(), error=None,
        info=("Request cancelled." if cancelled else None),
    )


@app.post("/member/{member_no}/shares/new")
def share_new_validate(
    request: Request, member_no: str,
    share_type: str = Form(""), description: str = Form(""),
    deposit: str = Form(""), funding_share_id: str = Form(""),
):
    sess = current_session(request)
    if not sess:
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)
    if m.status == "INACTIVE":
        return render(request, "share_new_form.html", member_no=member_no,
                      blocked="Member status is INACTIVE. Accounts cannot be opened.")
    if m.status == "RESTRICTED":
        return render(request, "share_new_form.html", member_no=member_no,
                      blocked="Member status is RESTRICTED. This function is unavailable.")

    funding = _open_funding_shares(member_no)
    vals = {"share_type": share_type, "description": description,
            "deposit": deposit, "funding_share_id": funding_share_id}

    def _form_err(msg):
        return render(request, "share_new_form.html", member_no=member_no,
                      share_types=_SHARE_TYPE_OPTIONS, funding=funding, vals=vals, error=msg)

    if sess.get("role") == "TELLER_RO":
        return _form_err("Your role does not permit this function.")
    if not funding:
        return _form_err("No eligible funding share on file.")
    if share_type not in _SHARE_TYPE_LABELS:
        return _form_err("Share type is required.")
    try:
        cents = display_to_cents(deposit)
    except (ValueError, TypeError):
        cents = None
    if cents is None or cents <= 0:
        return _form_err("Initial deposit must be a positive amount.")
    for s in list_shares(member_no):
        if s.status == "OPEN" and s.share_type == share_type:
            return _form_err("Member already has a share of type %s." % _share_type_label(share_type))
    fs = get_share(funding_share_id)
    if fs is None or fs.member_no != member_no or fs.status != "OPEN":
        return _form_err("No eligible funding share on file.")
    if fs.balance_available_cents < cents:
        return _form_err("Funding share has insufficient available balance.")

    desc = description.strip() or _SHARE_TYPE_LABELS[share_type]
    req_id = create_share_request(member_no, share_type, desc, cents, funding_share_id, sess["username"])
    return RedirectResponse("/member/%s/shares/new/review?req=%s" % (member_no, req_id), status_code=303)


@app.get("/member/{member_no}/shares/new/review", response_class=HTMLResponse)
def share_new_review(request: Request, member_no: str, req: str = ""):
    if not current_session(request):
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)
    sr = get_share_request(req) if req else None
    if sr is None or sr.member_no != member_no or sr.status != "DRAFT":
        return render(request, "share_new_review.html", member_no=member_no, pending=False)
    fs = get_share(sr.funding_share_id) if sr.funding_share_id else None
    funding_label = (
        "%s - %s - %s" % (fs.suffix, fs.description, cents_to_display(fs.balance_available_cents))
        if fs else "\u2014"
    )
    rows = [
        ("Share Type", _share_type_label(sr.share_type)),
        ("Description", sr.description),
        ("Initial Deposit", cents_to_display(sr.initial_deposit_cents)),
        ("Funding Share", funding_label),
        ("Request", sr.request_id),
    ]
    return render(request, "share_new_review.html", member_no=member_no,
                  pending=True, rows=rows, request_id=sr.request_id)


@app.post("/member/{member_no}/shares/new/commit")
def share_new_commit(
    request: Request, member_no: str, request_id: str = Form(""), op: str = Form(""),
):
    sess = current_session(request)
    if not sess:
        return RedirectResponse("/", status_code=303)
    m = get_member(member_no)
    if m is None:
        return render(request, "member_notfound.html", member_no=member_no)
    if sess.get("role") == "TELLER_RO":
        return render(request, "share_new_form.html", member_no=member_no,
                      share_types=_SHARE_TYPE_OPTIONS, funding=_open_funding_shares(member_no),
                      vals=_empty_share_vals(), error="Your role does not permit this function.")

    sr = get_share_request(request_id) if request_id else None
    if sr is None or sr.member_no != member_no or sr.status != "DRAFT":
        # Already committed/cancelled or unknown -> no double-open.
        return RedirectResponse("/member/%s" % member_no, status_code=303)

    if op == "cancel":
        cancel_share_request(request_id)
        return RedirectResponse("/member/%s/shares/new?cancelled=1" % member_no, status_code=303)

    actor = getattr(request.state, "actor", "HUMAN")
    commit_share_request(request_id, sess["username"], actor)
    return RedirectResponse("/member/%s" % member_no, status_code=303)
