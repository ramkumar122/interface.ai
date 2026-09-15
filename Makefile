PY := ./.venv/bin/python

.PHONY: test test-live db run coredesk coredesk-summit console seed-draft

# Full suite. Live surface tests auto-skip (loudly) when Chromium or CoreDesk
# on :8001 is absent, so this stays green in a bare environment.
test:
	$(PY) -m pytest -q

# Run the live surface tests for real. Requires `playwright install chromium`
# and CoreDesk running on :8001 (make run, in another shell).
test-live:
	$(PY) -m pytest -q -m live

# Rebuild the SQLite database from fixtures.
db:
	$(PY) reset_db.py

# The target app. Headed by default — the demo is worth watching.
coredesk:
	TENANT=riverbend PORT=8001 $(PY) run_coredesk.py

# The second tenant, for the cross-tenant demo. Same code, same database,
# different labels, menu codes and column order.
coredesk-summit:
	TENANT=summit PORT=8002 $(PY) run_coredesk.py

# Kept: `make run` predates `make coredesk` and still works.
run: coredesk

# Operator console on :8010. Separate process from CoreDesk, which it drives
# over HTTP on :8001 and which knows nothing about it.
console:
	OPERATOR_PORT=8010 $(PY) -m operator_console

# Compile the discovery transcript into a draft artifact so the review queue
# has something in it. Add --verify (with CoreDesk on :8001) to make it
# approvable; without it the queue shows it blocked, which is also a screen
# worth demonstrating.
seed-draft:
	$(PY) scripts/seed_draft_artifact.py
