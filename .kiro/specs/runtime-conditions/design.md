# Design — Runtime Conditions

## Component

`coredesk/inject.py`: a Starlette `BaseHTTPMiddleware` (`InjectionMiddleware`) plus a
tiny process-level flag with `set_injection(name, count)`, `clear_injection()`,
`injection_state()`. Registered in `app.py` via `app.add_middleware`. Self-contained
(inline HTML for the error page and injected snippets) so it does not import the
app's templates — avoiding a circular import.

## Resolution + exemption

`dispatch`: if the path starts with `/admin` or `/static`, pass through untouched
(the control page and assets are always clean). Otherwise the active condition is
the `X-CoreDesk-Inject` header if present (that request only), else the process flag
consumed one request at a time (`remaining -= 1`, clears at 0). The resolved name is
stored on `request.state.inject` so downstream code (session role) can see it.

## Per-condition behaviour

- `session_expired` → return `RedirectResponse("/?expired=1", 302)` with the cookie
  deleted; the sign-on page shows `Your session has expired.` on `?expired=1`.
- `app_error` → return `HTMLResponse(_APP_ERROR_HTML, 500)` — a standalone styled
  page with `CoreDesk Application Error` and `Reference ERR-7731`.
- `slow` → `time.sleep(SLOW_SECONDS)` then proceed (`SLOW_SECONDS = 8`; tests patch
  `coredesk.inject.time.sleep`).
- `maintenance_notice` / `db_timeout` → call the app, then, for a `text/html`
  response, splice a snippet in just after the opening `<body>` tag:
  - maintenance: `<div role="dialog" aria-label="Scheduled maintenance">…<a href="{path}">Close</a></div>` — a real accessible dialog whose Close is a plain link back to the same path (the one-shot injection is gone on reload, so no JS needed).
  - db_timeout: `<p class="error" id="ctl00_DbTimeout">Unable to retrieve records. Please retry.</p>`.
- `readonly_role` → pass through; `current_session` reads `request.state.inject` and,
  when it is `readonly_role`, forces the returned session's role to `TELLER_RO`.

Body splice reads `response.body_iterator`, decodes UTF-8, inserts after `<body…>`,
and returns a new `Response` with recomputed length (only for `text/html`).

## /admin/inject (in app.py)

`GET` renders `inject_admin.html`: a `method=post` form with a radio per condition,
a `count` input (default 1), an `Apply` submit and a `Clear` submit (`op` field);
shows the current flag state. `POST`: `op=apply` → `set_injection(name, count)`;
`op=clear` → `clear_injection()`; 303 back to `/admin/inject`. No auth (reviewer
tool; off the agent allowlist by prefix).

## Distinctness (the point)

The `app_error` 500 page has its own heading and an `ERR-####` reference; it is not
a `class="error"` inline message inside a normal screen. `db_timeout`'s message and
the maintenance dialog are recoverable in-page states. The agent's taxonomy keys off
these differences: 500 page → hard failure; dialog → auto-dismiss; expired → escalate.

## Files

Created: `coredesk/inject.py`, `coredesk/templates/inject_admin.html`,
`docs/EVIDENCE_MATRIX.md`, `tests/test_runtime_conditions.py`,
`docs/implementation-notes/runtime-conditions.md`.
Modified: `coredesk/app.py` (`add_middleware`, `current_session` role override,
sign-on `?expired=1` notice, `/admin/inject` routes), `coredesk/templates/signon.html`
(expired notice).

## Tests

Header-driven: `app_error` → 500 + `ERR-7731`; `maintenance_notice` → `role="dialog"`
+ Close; `db_timeout` → the message; `session_expired` → 302 to `/` + expired notice;
`readonly_role` → a write POST refused with the role message. Idle: a normal GET is
byte-for-byte a normal page (no injected markers). `/admin/inject` apply fires once
then clears (second request clean). `slow`: monkeypatch `inject.time.sleep` and
assert it was called with 8.
