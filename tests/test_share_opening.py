"""Share Opening tests (irreversible capability; commit path built for humans)."""

import re

import pytest
from fastapi.testclient import TestClient

import reset_db
from coredesk.app import app
from db.connection import get_connection
from db.queries import list_shares
from helpers import assert_no_data_testid, assert_visible_inputs_have_labels


@pytest.fixture(autouse=True)
def _fresh():
    reset_db.rebuild()
    yield


@pytest.fixture(scope="module")
def auth():
    c = TestClient(app)
    c.post("/signon", data={"userid": "mreyes", "password": "demo1234"})
    return c


@pytest.fixture(scope="module")
def auth_ro():
    c = TestClient(app)
    c.post("/signon", data={"userid": "jtran", "password": "demo1234"})
    return c


def _share_open_audit_count():
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'SHARE_OPEN'"
        ).fetchone()[0]
    finally:
        conn.close()


def _request_status(request_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM share_request WHERE request_id = ?", (request_id,)
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _open_types(member_no):
    return {s.share_type for s in list_shares(member_no) if s.status == "OPEN"}


def _make_draft(client, member_no="100101", share_type="MONEY_MARKET",
                deposit="1,000.00", funding="SHR-100101-0000"):
    resp = client.post(
        "/member/%s/shares/new" % member_no,
        data={"share_type": share_type, "description": "",
              "deposit": deposit, "funding_share_id": funding},
    )
    match = re.search(r"SRQ-\d+", resp.text)
    return (match.group(0) if match else None), resp


# ---------------------------------------------------------------------------
# Form + business outcomes
# ---------------------------------------------------------------------------
def test_form_lists_types_and_funding(auth):
    html = auth.get("/member/100101/shares/new").text
    assert "OPEN SHARE ACCOUNT" in html
    for label in ("Regular Share", "Money Market", "Certificate 12mo"):
        assert label in html
    assert "0000 - Regular Share - 12,845.50" in html


def test_inactive_member_refused(auth):
    html = auth.get("/member/100104/shares/new").text
    assert "Member status is INACTIVE. Accounts cannot be opened." in html


def test_duplicate_type_refused(auth):
    resp = auth.post(
        "/member/100105/shares/new",
        data={"share_type": "PRIMARY_SAVINGS", "description": "",
              "deposit": "100.00", "funding_share_id": "SHR-100105-0000"},
    )
    assert "Member already has a share of type PRIMARY SAVINGS." in resp.text


def test_insufficient_funding_refused(auth):
    resp = auth.post(
        "/member/100111/shares/new",
        data={"share_type": "MONEY_MARKET", "description": "",
              "deposit": "100.00", "funding_share_id": "SHR-100111-0000"},
    )
    assert "Funding share has insufficient available balance." in resp.text


def test_deposit_must_be_positive(auth):
    resp = auth.post(
        "/member/100101/shares/new",
        data={"share_type": "MONEY_MARKET", "description": "",
              "deposit": "abc", "funding_share_id": "SHR-100101-0000"},
    )
    assert "Initial deposit must be a positive amount." in resp.text


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------
def test_review_shows_parsed_values(auth):
    _req, resp = _make_draft(auth, deposit="1,000.00")
    assert "New share account review" in resp.text
    assert "MONEY MARKET" in resp.text
    assert "1,000.00" in resp.text
    assert "0000 - Regular Share - 12,845.50" in resp.text
    assert re.search(r"SRQ-\d+", resp.text)


def test_open_account_carries_confirm(auth):
    _req, resp = _make_draft(auth)
    assert "confirm('Open this account? This cannot be undone.')" in resp.text


# ---------------------------------------------------------------------------
# Commit / cancel
# ---------------------------------------------------------------------------
def test_commit_creates_share_and_audits(auth):
    before = _share_open_audit_count()
    req_id, _ = _make_draft(auth)
    auth.post("/member/100101/shares/new/commit", data={"request_id": req_id, "op": "open"})
    assert "MONEY_MARKET" in _open_types("100101")
    assert _share_open_audit_count() - before == 1
    # new share visible in the shares panel
    panel = auth.get("/member/100101/shares").text
    assert "MONEY MARKET" in panel


def test_cancel_sets_cancelled_and_creates_no_share(auth):
    before_types = _open_types("100101")
    before_audit = _share_open_audit_count()
    req_id, _ = _make_draft(auth)
    resp = auth.post("/member/100101/shares/new/commit", data={"request_id": req_id, "op": "cancel"})
    assert "Request cancelled." in resp.text
    assert _request_status(req_id) == "CANCELLED"
    assert _open_types("100101") == before_types
    assert _share_open_audit_count() == before_audit


# ---------------------------------------------------------------------------
# Gating + invariants
# ---------------------------------------------------------------------------
def test_teller_ro_refused(auth_ro):
    resp = auth_ro.post(
        "/member/100101/shares/new",
        data={"share_type": "MONEY_MARKET", "description": "",
              "deposit": "100.00", "funding_share_id": "SHR-100101-0000"},
    )
    assert "Your role does not permit this function." in resp.text


def test_invariants_form(auth):
    html = auth.get("/member/100101/shares/new").text
    assert_no_data_testid(html)
    assert_visible_inputs_have_labels(html)
