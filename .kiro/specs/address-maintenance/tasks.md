# Tasks — Address Maintenance

- [ ] 1. `coredesk/config.py`: add `STATE_CODES` (50 + DC). _Req 2.2_
- [ ] 2. `coredesk/app.py`: `_validate_address(...)` pure helper. _Req 3.1–3.6_
- [ ] 3. `GET /member/{m}/address` + `address_form.html` (current read-only, prefilled inputs, state select, btn_continue, banners). Replace stub. _Req 2, 5.1 banner_
- [ ] 4. `POST /member/{m}/address`: gating, validate → re-render with messages+refill, or 303 to review. _Req 1, 3.7, 3.8_
- [ ] 5. `GET /member/{m}/address/review` + `address_review.html` (side-by-side, hidden-field commit form, no-pending message). _Req 4_
- [ ] 6. `POST /member/{m}/address/commit`: gating, no-op guard, `update_member_address`, 303 `?ref`. _Req 5_
- [ ] 7. `tests/test_address_maintenance.py`. _Req 1–6_
- [ ] 8. `docs/implementation-notes/address-maintenance.md` (standing format).
- [ ] 9. pytest green.

## Record what you did
On completion write `docs/implementation-notes/address-maintenance.md` in the
standing format (What was built · Files · Decisions · AX contract · Audit rows ·
Tests · Known gaps).
