# Tasks — Runtime Conditions

- [ ] 1. `coredesk/inject.py`: `InjectionMiddleware` + flag state (`set_injection`/`clear_injection`/`injection_state`) + condition behaviours. _Req 1, 2_
- [ ] 2. `coredesk/app.py`: `add_middleware`; `current_session` `readonly_role` override; sign-on `?expired=1` notice; `/admin/inject` GET/POST. _Req 1, 2.1, 2.6_
- [ ] 3. `coredesk/templates/inject_admin.html` + sign-on expired notice. _Req 1.2, 2.1_
- [ ] 4. `docs/EVIDENCE_MATRIX.md`. _Req 4_
- [ ] 5. `tests/test_runtime_conditions.py` (each condition by header; idle invisible; one-shot flag; patched sleep). _Req 1–3_
- [ ] 6. `docs/implementation-notes/runtime-conditions.md` (standing format).
- [ ] 7. pytest green.

## Record what you did
On completion write `docs/implementation-notes/runtime-conditions.md` (standing format).
