# Implementation notes — runtime-conditions

## What was built

Middleware (`coredesk/inject.py`, `InjectionMiddleware`) that forces one of six
runtime conditions on demand, plus a control page `GET/POST /admin/inject`. A
reviewer can reproduce the automation's error taxonomy with one toggle instead of
waiting for a real failure. Conditions activate per-request via the
`X-CoreDesk-Inject` header, or for the next N requests via the admin page. When no
condition is set, the middleware is invisible.

## Files created / modified

| Path | Created/Modified | Reason |
|---|---|---|
| `coredesk/inject.py` | created | middleware + flag state + condition behaviours |
| `coredesk/templates/inject_admin.html` | created | `/admin/inject` control form |
| `docs/EVIDENCE_MATRIX.md` | created | capability × fixture × injection × expected status |
| `tests/test_runtime_conditions.py` | created | feature tests (9) |
| `docs/implementation-notes/runtime-conditions.md` | created | this record |
| `coredesk/app.py` | modified | `add_middleware`; `current_session` readonly override; sign-on `?expired=1`; `/admin/inject` routes |
| `coredesk/templates/signon.html` | modified | expired notice |

## Decisions I made

- **`/admin/*` and `/static/*` are exempt** from injection so the control page and
  assets are always reachable (and the agent can't be handed a broken admin page).
  `/admin/inject` is under `/admin/` precisely so the future policy layer excludes
  it by prefix.
- **Header takes precedence and doesn't consume the flag**; the process flag is
  consumed one request at a time (`remaining`), matching "next N requests".
- **`readonly_role` via `current_session`**, not by rewriting the cookie: the
  middleware sets `request.state.inject`, and `current_session` forces the role to
  `TELLER_RO` for that request only. Clean, and the real cookie is untouched.
- **Body splicing** for `maintenance_notice`/`db_timeout` reads the response body
  and inserts a snippet after `<body>` (only for `text/html`). The maintenance
  dialog's `Close` is a plain link back to the same path — the one-shot injection
  is gone on reload, so no JavaScript is needed to dismiss it.
- **`inject.py` is template-free** (inline HTML for the 500 page and snippets) to
  avoid a circular import with `app.py`'s templates.
- **`/admin/inject` has no auth** — it's a reviewer/test tool and per `product.md`
  CoreDesk has no real security. It stays off the agent allowlist by prefix.
- **`slow` uses `time.sleep`** (blocks briefly; a mock); tests patch
  `coredesk.inject.time.sleep` so no real 8s wait.

## AX contract added

Injected `maintenance_notice` dialog (on any page):

```
- table "Scheduled maintenance" [role=dialog]:
  - text: Scheduled maintenance Saturday 02:00-06:00
  - link "Close"
```

`app_error` page: `heading "CoreDesk Application Error"` + text `Reference ERR-7731`
(a distinct 500 document, not an inline `class="error"` message).
`db_timeout`: `text "Unable to retrieve records. Please retry."` spliced into the page.
`/admin/inject`: `heading "RUNTIME CONDITION INJECTION"`, a `radio` per condition,
`textbox "Apply to next N requests"`, `button "Apply"`, `button "Clear"`.

## Audit rows this feature writes

None. Injection is a transport-level test tool and writes no audit rows.

## Tests added

- `test_idle_middleware_is_invisible` — Req 3.1
- `test_app_error_500_page` — Req 2.4
- `test_maintenance_dialog_injected` — Req 2.2
- `test_db_timeout_message_injected` — Req 2.5
- `test_session_expired_redirects` — Req 2.1
- `test_readonly_role_forces_teller` — Req 2.6
- `test_slow_uses_patched_sleep` — Req 2.3 (patched sleep)
- `test_admin_inject_one_shot` — Req 1.2
- `test_admin_page_not_injected` — Req 1.3

## Known gaps

- `db_timeout` splices a message banner into the page rather than literally
  replacing a grid's `<tbody>`; the message is what the agent detects, but the
  underlying rows still render below it on data screens.
- `slow` blocks the event loop for the sleep (acceptable for a single-user mock;
  real load would want an async sleep).
- The middleware runs for every non-exempt route; there is no per-route scoping of
  which condition is "appropriate" — any condition can be forced on any page.
