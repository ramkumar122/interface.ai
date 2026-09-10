"""Card Maintenance tests (the first write feature).

These tests mutate card state, so a function-scoped autouse fixture rebuilds the
seeded temp DB before each test — every test starts from clean seed. Audit
assertions use before/after deltas.
"""

import re

import pytest
from fastapi.testclient import TestClient

import reset_db
from coredesk.app import app
from coredesk.config import CONFIG
from db.connection import get_connection
from helpers import (
    assert_column_headers,
    assert_no_data_testid,
    assert_table_has_caption,
    assert_visible_inputs_have_labels,
)


@pytest.fixture(autouse=True)
def _fresh():
    # Card tests change state; reset to clean seed before each one.
    reset_db.rebuild()
    yield


@pytest.fixture(scope="module")
def auth():
    client = TestClient(app)
    client.post("/signon", data={"userid": "mreyes", "password": "demo1234"})
    return client


@pytest.fixture(scope="module")
def auth_ro():
    client = TestClient(app)
    client.post("/signon", data={"userid": "jtran", "password": "demo1234"})
    return client


def _audit_count():
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    finally:
        conn.close()


def _last_audit():
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT action, detail FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)
    finally:
        conn.close()


def _card_status(card_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM card WHERE card_id = ?", (card_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
def test_card_list_caption_headers_and_mask(auth):
    html = auth.get("/member/100101/cards").text
    assert_table_has_caption(html, "ctl00_MainContent_grdCards", "Cards on file")
    assert_column_headers(
        html, "ctl00_MainContent_grdCards",
        [c["header"] for c in CONFIG["riverbend"]["cards_columns"]],
    )
    assert "****4417" in html
    assert "MAINT" in html


def test_no_cards_message(auth):
    html = auth.get("/member/100109/cards").text
    assert "No cards on file for this member." in html


def test_two_cards_listed_and_isolated(auth):
    html = auth.get("/member/100112/cards").text
    assert html.count("_lnkMaint") == 2
    auth.post(
        "/member/100112/cards/CRD-100112-1/maint",
        data={"action": "LOCK", "reason": "Member request", "notes": ""},
    )
    assert _card_status("CRD-100112-1") == "LOCKED"
    assert _card_status("CRD-100112-2") == "ACTIVE"


# ---------------------------------------------------------------------------
# Apply / audit
# ---------------------------------------------------------------------------
def test_lock_active_card_happy_path(auth):
    before = _audit_count()
    resp = auth.post(
        "/member/100101/cards/CRD-100101-1/maint",
        data={"action": "LOCK", "reason": "Suspected fraud", "notes": ""},
    )
    assert "Card updated. Reference: CRD-" in resp.text
    assert _card_status("CRD-100101-1") == "LOCKED"
    assert _audit_count() - before == 1
    assert _last_audit()[0] == "CARD_LOCK"


def test_lock_already_locked_writes_nothing(auth):
    assert _card_status("CRD-100103-1") == "LOCKED"
    before = _audit_count()
    resp = auth.post(
        "/member/100103/cards/CRD-100103-1/maint",
        data={"action": "LOCK", "reason": "Member request", "notes": ""},
    )
    assert "Card is already locked. No change applied." in resp.text
    assert _card_status("CRD-100103-1") == "LOCKED"
    assert _audit_count() - before == 0


def test_hotlist_then_immutable(auth):
    resp = auth.post(
        "/member/100101/cards/CRD-100101-1/maint",
        data={"action": "HOTLIST", "reason": "Suspected fraud", "notes": ""},
    )
    assert "Card updated. Reference: CRD-" in resp.text
    assert _card_status("CRD-100101-1") == "HOTLISTED"
    assert _last_audit()[0] == "CARD_HOTLIST"

    before = _audit_count()
    resp2 = auth.post(
        "/member/100101/cards/CRD-100101-1/maint",
        data={"action": "LOCK", "reason": "Member request", "notes": ""},
    )
    assert "Card is hotlisted and cannot be modified." in resp2.text
    assert _audit_count() - before == 0


def test_expired_card_refused(auth):
    before = _audit_count()
    resp = auth.post(
        "/member/100113/cards/CRD-100113-1/maint",
        data={"action": "LOCK", "reason": "Member request", "notes": ""},
    )
    assert "Card is expired and cannot be modified." in resp.text
    assert _card_status("CRD-100113-1") == "EXPIRED"
    assert _audit_count() - before == 0


def test_teller_ro_refused(auth_ro):
    before = _audit_count()
    resp = auth_ro.post(
        "/member/100101/cards/CRD-100101-1/maint",
        data={"action": "LOCK", "reason": "Member request", "notes": ""},
    )
    assert "Your role does not permit this function." in resp.text
    assert _card_status("CRD-100101-1") == "ACTIVE"
    assert _audit_count() - before == 0


def test_restricted_member_blocked(auth):
    html = auth.get("/member/100108/cards").text
    assert "Member status is RESTRICTED. This function is unavailable." in html
    before = _audit_count()
    resp = auth.post(
        "/member/100108/cards/CRD-100108-1/maint",
        data={"action": "LOCK", "reason": "Member request", "notes": ""},
    )
    assert "Member status is RESTRICTED. This function is unavailable." in resp.text
    assert _audit_count() - before == 0


def test_reason_required(auth):
    before = _audit_count()
    resp = auth.post(
        "/member/100101/cards/CRD-100101-1/maint",
        data={"action": "LOCK", "reason": "", "notes": ""},
    )
    assert "Reason is required." in resp.text
    assert _card_status("CRD-100101-1") == "ACTIVE"
    assert _audit_count() - before == 0


# ---------------------------------------------------------------------------
# Data protection
# ---------------------------------------------------------------------------
def test_notes_never_stored_verbatim(auth):
    auth.post(
        "/member/100101/cards/CRD-100101-1/maint",
        data={"action": "LOCK", "reason": "Suspected fraud", "notes": "SECRETNOTE123"},
    )
    action, detail = _last_audit()
    assert action == "CARD_LOCK"
    assert "SECRETNOTE123" not in detail
    assert "reason=Suspected fraud" in detail
    assert "note=Y" in detail


def test_no_unmasked_pan_in_html(auth):
    html = auth.get("/member/100101/cards").text
    assert "****4417" in html
    # No bare 4417 that isn't immediately preceded by a mask asterisk.
    assert re.search(r"(?<!\*)4417", html) is None


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------
def test_invariants_list_and_maint(auth):
    for url in ["/member/100101/cards", "/member/100101/cards/CRD-100101-1/maint"]:
        html = auth.get(url).text
        assert_no_data_testid(html)
        assert_visible_inputs_have_labels(html)
