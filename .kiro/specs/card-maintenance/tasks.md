# Tasks — Card Maintenance

Incremental build order; app stays runnable and tests green after each task.

- [ ] 1. Config: add `cards_columns` (CARD|NETWORK|LAST 4|STATUS|LINKED) to both tenants in `coredesk/config.py`.
  - _Requirements: 2.2, 7.5_

- [ ] 2. Data layer: redact `set_card_status` `detail` to `reason=<value>` (+ `; note=Y` when a note is present) in `db/queries.py`.
  - _Requirements: 6.2_

- [ ] 3. Helpers in `coredesk/app.py`: `_mask_last4` (`****NNNN`), network spacing, `_linked_suffix` (suffix or `—`).
  - _Requirements: 2.3, 2.4, 3.1, 6.1_

- [ ] 4. Card list route `GET /member/{member_no}/cards` + `cards_list.html` (replace stub): heading + member context, `grdCards` caption + config headers + masked last4 + `MAINT` link, no-cards message, return links, member-not-found reuse.
  - _Requirements: 1.1, 1.2, 2.1–2.8, 7.x_

- [ ] 5. Maint GET `GET /member/{member_no}/cards/{card_id}/maint` + `card_maint.html`: read-only detail, Action radio group (fieldset/legend), required Reason select, Notes textarea, Apply/Cancel, scoped `confirm()`, `?ref=` banner, card-not-found.
  - _Requirements: 3.1–3.8, 4.6, 7.2_

- [ ] 6. Maint POST: precedence (member-notfound→RESTRICTED→TELLER_RO→card-notfound→HOTLISTED→EXPIRED→reason-required→no-op→apply); apply via `set_card_status` → 303 `?ref`; non-success re-render at 200.
  - _Requirements: 1.2–1.4, 4.1–4.7, 5.1–5.4_

- [ ] 7. Tests `tests/test_card_maintenance.py`: function-scoped `reset_db.rebuild()`; `auth`/`auth_ro` clients; list/caption/headers/mask, no-cards, two-cards isolation, lock happy path (+1 `CARD_LOCK`), already-locked (0 audit), hotlist + subsequent refusal, expired, TELLER_RO refusal, RESTRICTED, reason-required, notes redaction, no-PAN, invariants.
  - _Requirements: 1, 2, 4, 5, 6, 7, 8_

- [ ] 8. Write `docs/implementation-notes/card-maintenance.md` (standing format).

- [ ] 9. Verify: `python reset_db.py && pytest` green; live-check lock/hotlist/already-locked on 8001; stop servers, clean up.
  - _Requirements: all_

## Record what you did

On completion, write `docs/implementation-notes/card-maintenance.md` with:
What was built · Files created/modified (table) · Decisions I made (never omit) ·
AX contract added (aria-snapshot, per tenant where differing) · Audit rows this
feature writes (table) · Tests added (each with its requirement) · Known gaps.
