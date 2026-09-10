# Requirements — Runtime Conditions

## Introduction

Infrastructure, not a screen: middleware that forces specific runtime conditions on
demand, so the automation's error taxonomy can be demonstrated by a reviewer with
one command instead of waiting for something to break.

## Requirement 1 — Activation
1. WHEN a request carries header `X-CoreDesk-Inject: <name>` THE SYSTEM SHALL apply that condition to that request only.
2. THE SYSTEM SHALL provide `GET/POST /admin/inject`: a plain form listing the conditions with radio buttons, an `Apply` (with a count, default 1) and a `Clear` action, setting a process-level flag that applies to the next N requests then clears itself.
3. THE SYSTEM SHALL never apply an injection to `/admin/*` or `/static/*` requests, so the control page is always reachable.
4. THE `/admin/inject` route SHALL be trivially identifiable by its `/admin/` prefix so the policy layer can exclude it from the agent allowlist.

## Requirement 2 — Conditions
1. `session_expired` — clear the cookie and 302-redirect to `/` showing `Your session has expired.` (recoverable → escalate).
2. `maintenance_notice` — prepend a `role="dialog"` element (accessible name `Scheduled maintenance`) containing `Scheduled maintenance Saturday 02:00-06:00` and a real `Close` control (recoverable → auto-dismiss).
3. `slow` — sleep ~8s before responding (recoverable → wait/retry).
4. `app_error` — a rendered 500 HTML page containing `CoreDesk Application Error` and `Reference ERR-7731`, visually/structurally distinct from any business-outcome message (hard failure).
5. `db_timeout` — render the page with `Unable to retrieve records. Please retry.` in place of grid rows (retry once, then hard failure).
6. `readonly_role` — force the session role to `TELLER_RO` for that request (business outcome `PERMISSION_DENIED`).

## Requirement 3 — Invisible when idle
1. WHEN no injection is active THE SYSTEM SHALL behave exactly as without the middleware (asserted explicitly).

## Requirement 4 — Evidence matrix
1. THE SYSTEM SHALL include `docs/EVIDENCE_MATRIX.md` mapping each planned evidence run to capability, fixture, injection, and expected result status.

## Tests
- Each condition, activated by header, produces its documented behaviour.
- Idle middleware is invisible on every route.
- `/admin/inject` sets a condition that fires once then clears.
- `slow` is exercised with a patched sleep (no real 8s wait).
