# Tasks — Share Opening

- [ ] 1. `db/queries.py`: add `cancel_share_request(request_id)` (DRAFT → CANCELLED, no audit). _Req 5.2_
- [ ] 2. `coredesk/app.py`: type-label map + `_share_new` helpers; `GET /member/{m}/shares/new` + `share_new_form.html` (replace stub). _Req 2_
- [ ] 3. `POST /member/{m}/shares/new`: gating + business outcomes + create DRAFT → 303 review. _Req 1, 3_
- [ ] 4. `GET /member/{m}/shares/new/review` + `share_new_review.html` (parsed values, SRQ ref, Open Account confirm + Cancel Request). _Req 4_
- [ ] 5. `POST /member/{m}/shares/new/commit`: open (commit_share_request → record) / cancel (cancel_share_request → form); idempotent guard. _Req 5_
- [ ] 6. `tests/test_share_opening.py`. _Req 1–6_
- [ ] 7. `docs/implementation-notes/share-opening.md` (standing format). 
- [ ] 8. pytest green.

## Record what you did
On completion write `docs/implementation-notes/share-opening.md` in the standing
format.
