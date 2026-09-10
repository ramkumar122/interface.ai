# Write-Path Conventions

Applies to every state-changing CoreDesk feature (card maintenance, address
update, share opening). Read alongside `tech.md`; this refines it for writes so
the rules aren't repeated in each spec. Nothing here overrides `tech.md`.

## Post / Redirect / Get

- A state change is always a `POST`. On success, respond `303` and redirect to a
  `GET` (normally the same screen) carrying `?ref=<REFERENCE>`.
- The confirmation banner renders on the **GET redirect target**, never in a POST
  response body. This keeps refresh safe and gives the agent a distinct
  post-write page state to checkpoint.
- A validation failure re-renders the form with `200` (no redirect), shows the
  messages, and re-fills the inputs with what was submitted.

## References

- Every applied write returns a human-visible reference, shown in the banner and
  stored in `audit_log.reference`: `CRD-#####` (card), `ADR-#####` (address),
  `SRQ-#####` (share request), and the new `SHR-...` id on share open.
- Generate the reference once per operation (stable within the transaction) via
  the existing `db/queries` helper. Prefix + 5 digits.

## Audit

- Every applied change writes **exactly one** `audit_log` row via `write_audit(...)`.
- An informational "no change" outcome (e.g. lock on an already-locked card,
  unlock on an already-active card) writes **nothing** — no state change, no audit
  row. This is a business outcome, not an error.
- `actor` is `HUMAN` or `AUTOMATION`, taken from the session/caller context;
  operator UI defaults to `HUMAN`.
- **Redact before writing.** `detail` must already be redacted by the caller.
  Never put a free-text Notes value verbatim, a card number, or the canary string
  into `detail`. Summarize (e.g. `reason=Suspected fraud`), don't echo user input.

## Reversible vs irreversible

- Reversible writes (Lock, Unlock, address update) apply directly — no dialog.
- Irreversible writes get a `confirm()` dialog and are the **only** JavaScript in
  CoreDesk. There are exactly two: **Report Lost or Stolen** (hotlist) and
  **Open Account**. The dialog text states the action cannot be undone.
- The irreversible control must always render with a correct, visible accessible
  name even when the agent is expected to decline it — the agent needs to see the
  option exists and choose not to take it.

## Validation

- Field-level: each failed rule renders its own message beside the offending
  input; multiple failures render simultaneously.
- Required `<select>`s (Reason, State, Share Type) are validated server-side;
  never trust the client.
- Dates are `MM/DD/YYYY` free text, never `type="date"` (native pickers are
  invisible to the accessibility tree).

## Permission and status gating

Both are business outcomes with a specific, stable message and HTTP `200` — never
a crash or a `500`. No state change, no audit row.

- Role `TELLER_RO`: refuse every write with `Your role does not permit this
  function.`
- Member status `RESTRICTED`: refuse with `Member status is RESTRICTED. This
  function is unavailable.`

## Business outcomes are results, not errors

Every refusal or no-op condition renders a distinguishable, stable message the
agent can detect. Distinct wording per condition; no stack traces; the message is
the contract.

## Multi-step flows (review steps)

- A review/confirmation step is a real server round-trip with its own URL, not a
  client-side panel — the agent needs a distinct page state to checkpoint.
- Values shown on a review page are the parsed-and-reformatted values (e.g. money
  through `cents_to_display`), not raw echoes of what was typed.
- Reaching a review URL with no pending change renders an informational message,
  not an error.

## Still in force (from tech.md)

Zero JSON endpoints. Full-page loads only. No `data-testid`. WebForms-style ids.
Nested-`<table>` layout. Money as integer cents rendered via `db/money.py`. A real
`<label for>` on every visible input, real `<th>` headers and a `<caption>` on
every data table.
