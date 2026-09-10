"""Address Maintenance tests (multi-field write with a review round-trip)."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

import reset_db
from coredesk.app import app
from db.connection import get_connection
from db.queries import get_member
from helpers import assert_no_data_testid, assert_visible_inputs_have_labels

FUTURE = (date.today() + timedelta(days=30)).strftime("%m/%d/%Y")
PAST = (date.today() - timedelta(days=1)).strftime("%m/%d/%Y")


def _valid(**over):
    payload = {"line1": "1 New St", "line2": "", "city": "Newtown",
               "state": "OR", "zip": "97001", "eff": FUTURE}
    payload.update(over)
    return payload


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


def _addr_audit_count():
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'ADDRESS_UPDATE'"
        ).fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------
def test_form_shows_current_and_fields(auth):
    html = auth.get("/member/100101/address").text
    assert "ADDRESS CHANGE" in html
    assert "742 Evergreen Ter" in html  # current address
    assert "Effective Date" in html


# ---------------------------------------------------------------------------
# Validation (each rule independently)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "over,message",
    [
        ({"line1": ""}, "Address line 1 is required."),
        ({"city": ""}, "City is required."),
        ({"state": ""}, "State is required."),
        ({"zip": "123"}, "ZIP code must be 5 or 9 digits."),
        ({"eff": "13/40/2026"}, "Effective date must be MM/DD/YYYY."),
        ({"eff": PAST}, "Effective date cannot be in the past."),
    ],
)
def test_validation_rule(auth, over, message):
    resp = auth.post("/member/100101/address", data=_valid(**over))
    assert message in resp.text


def test_two_failures_show_together(auth):
    resp = auth.post("/member/100101/address", data=_valid(line1="", city=""))
    assert "Address line 1 is required." in resp.text
    assert "City is required." in resp.text


def test_values_survive_validation_failure(auth):
    resp = auth.post("/member/100101/address", data=_valid(line1="", city="KEEPCITY"))
    assert 'value="KEEPCITY"' in resp.text


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------
def test_valid_reaches_review(auth):
    resp = auth.post("/member/100101/address", data=_valid())
    assert "Address change review" in resp.text
    assert "742 Evergreen Ter" in resp.text  # current
    assert "1 New St" in resp.text            # new


def test_review_without_pending(auth):
    resp = auth.get("/member/100101/address/review")
    assert "No pending address change." in resp.text


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------
def test_commit_writes_and_refs(auth):
    before = _addr_audit_count()
    resp = auth.post("/member/100101/address/commit", data=_valid())
    assert "Address updated. Reference: ADR-" in resp.text
    assert _addr_audit_count() - before == 1
    assert get_member("100101").addr_line1 == "1 New St"


def test_double_commit_does_not_double_write(auth):
    auth.post("/member/100101/address/commit", data=_valid())
    after_first = _addr_audit_count()
    # Stale re-submit of the same (now-current) values.
    resp = auth.post("/member/100101/address/commit", data=_valid())
    assert _addr_audit_count() == after_first
    assert "No change to apply." in resp.text


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------
def test_teller_ro_refused_at_both_posts(auth_ro):
    before = _addr_audit_count()
    r1 = auth_ro.post("/member/100101/address", data=_valid())
    r2 = auth_ro.post("/member/100101/address/commit", data=_valid())
    assert "Your role does not permit this function." in r1.text
    assert "Your role does not permit this function." in r2.text
    assert _addr_audit_count() == before
    assert get_member("100101").addr_line1 == "742 Evergreen Ter"


def test_restricted_member_refused(auth):
    assert "RESTRICTED" in auth.get("/member/100108/address").text
    before = _addr_audit_count()
    auth.post("/member/100108/address/commit", data=_valid())
    assert _addr_audit_count() == before


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------
def test_invariants_form(auth):
    html = auth.get("/member/100101/address").text
    assert_no_data_testid(html)
    assert_visible_inputs_have_labels(html)
