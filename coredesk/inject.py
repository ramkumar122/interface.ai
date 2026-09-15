"""Runtime-condition injection middleware for CoreDesk.

A testing/demo tool that forces specific runtime conditions on demand so the
automation's error taxonomy can be reproduced by a reviewer with one command.
Two activation paths, same conditions:

  1. Header ``X-CoreDesk-Inject: <name>`` -- that request only.
  2. ``/admin/inject`` sets a process-level flag for the next N requests.

``/admin/*`` and ``/static/*`` are never injected, so the control page and assets
stay reachable. ``/admin/inject`` is intentionally under ``/admin/`` so the policy
layer can exclude it from the agent allowlist by prefix -- the agent must never be
able to disable a condition it is being tested against.

This module is self-contained (inline HTML) so it does not import the app's
templates, avoiding a circular import.
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, RedirectResponse, Response

from coredesk.session import COOKIE_NAME

SLOW_SECONDS = 8

CONDITIONS = [
    "session_expired",
    "maintenance_notice",
    "slow",
    "app_error",
    "db_timeout",
    "readonly_role",
]

# Process-level flag set via /admin/inject (applies to the next N requests).
_state = {"name": None, "remaining": 0}


def set_injection(name, count=1):
    _state["name"] = name
    _state["remaining"] = max(1, int(count))


def clear_injection():
    _state["name"] = None
    _state["remaining"] = 0


def injection_state():
    return dict(_state)


def _consume_flag():
    if _state["remaining"] > 0 and _state["name"]:
        name = _state["name"]
        _state["remaining"] -= 1
        if _state["remaining"] <= 0:
            _state["name"] = None
        return name
    return None


_APP_ERROR_HTML = (
    "<!DOCTYPE html><html><head><meta http-equiv=\"Content-Type\" "
    "content=\"text/html; charset=utf-8\"><title>CoreDesk Application Error</title>"
    "<link rel=\"stylesheet\" type=\"text/css\" href=\"/static/coredesk.css\"></head>"
    "<body><table width=\"100%\" cellpadding=\"10\" cellspacing=\"0\" border=\"0\"><tr><td>"
    "<h1 id=\"ctl00_AppError\">CoreDesk Application Error</h1>"
    "<p>An unexpected error occurred while processing your request.</p>"
    "<p id=\"ctl00_AppErrorRef\">Reference ERR-7731</p>"
    "</td></tr></table></body></html>"
)


def _maintenance_dialog(path):
    return (
        "<table role=\"dialog\" aria-label=\"Scheduled maintenance\" "
        "id=\"ctl00_MaintenanceDialog\" cellpadding=\"6\" cellspacing=\"0\" border=\"1\">"
        "<tr><td>Scheduled maintenance Saturday 02:00-06:00 &nbsp; "
        "<a id=\"ctl00_MaintenanceClose\" href=\"%s\">Close</a></td></tr></table>" % path
    )


_DB_TIMEOUT_SNIPPET = (
    "<p class=\"error\" id=\"ctl00_DbTimeout\">Unable to retrieve records. Please retry.</p>"
)


def _splice_after_body(html, snippet):
    lowered = html.lower()
    start = lowered.find("<body")
    if start == -1:
        return snippet + html
    close = html.find(">", start)
    if close == -1:
        return snippet + html
    return html[: close + 1] + snippet + html[close + 1:]


class InjectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Propagate the actor header so audit writes distinguish
        # AUTOMATION from HUMAN.  Default is HUMAN when absent.
        actor = request.headers.get("x-coredesk-actor", "HUMAN")
        if actor not in ("HUMAN", "AUTOMATION"):
            actor = "HUMAN"
        request.state.actor = actor

        path = request.url.path
        if path.startswith("/admin") or path.startswith("/static"):
            request.state.inject = None
            return await call_next(request)

        name = request.headers.get("x-coredesk-inject") or _consume_flag()
        request.state.inject = name

        if name == "session_expired":
            resp = RedirectResponse("/?expired=1", status_code=302)
            resp.delete_cookie(COOKIE_NAME)
            return resp
        if name == "app_error":
            return HTMLResponse(_APP_ERROR_HTML, status_code=500)
        if name == "slow":
            time.sleep(SLOW_SECONDS)
            return await call_next(request)

        response = await call_next(request)

        if name in ("maintenance_notice", "db_timeout"):
            if "text/html" in response.headers.get("content-type", ""):
                body = b""
                async for chunk in response.body_iterator:
                    body += chunk
                text = body.decode("utf-8")
                snippet = (
                    _maintenance_dialog(path)
                    if name == "maintenance_notice"
                    else _DB_TIMEOUT_SNIPPET
                )
                new = Response(
                    content=_splice_after_body(text, snippet),
                    status_code=response.status_code,
                    media_type="text/html",
                )
                for key, value in response.headers.items():
                    if key.lower() not in ("content-length", "content-type"):
                        new.headers[key] = value
                return new

        return response
