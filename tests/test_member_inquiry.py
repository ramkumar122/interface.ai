"""Member Inquiry screen tests.

Runs against the seeded temp DB (shared conftest fixture). The tenant is
resolved per request via get_cfg(), so a test switches tenant with
monkeypatch.setenv("TENANT", ...) — no module reload.
"""

import pytest
from fastapi.testclient import TestClient

from coredesk.app import app
from coredesk.config import CONFIG
from helpers import (
    assert_column_headers,
    assert_iframe_has_title,
    assert_no_data_testid,
    assert_table_has_caption,
    assert_visible_inputs_have_labels,
)

# Unauthenticated client that does not follow redirects (to inspect 303s).
unauth = TestClient(app, follow_redirects=False)


@pytest.fixture(scope="module")
def auth():
    """A signed-in client (cookies persist across requests)."""
    client = TestClient(app)
    resp = client.post("/signon", data={"userid": "mreyes", "password": "demo1234"})
    assert resp.status_code == 200  # followed 303 -> /menu
    return client


PROTECTED_ROUTES = [
    "/mbrinq",
    "/mbrinq/results",
    "/member/100101",
    "/member/100101/shares",
]


@pytest.mark.parametrize("route", PROTECTED_ROUTES)
def test_unauthenticated_redirects_to_root(route):
    resp = unauth.get(route)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def test_empty_criteria_reprompts(auth):
    resp = auth.get("/mbrinq/results")
    assert resp.status_code == 200
    assert "Enter a member number or last name." in resp.text


def test_unknown_member_no_shows_not_found(auth):
    resp = auth.get("/mbrinq/results", params={"member_no": "999999"})
    assert resp.status_code == 200
    assert "No records found for the specified criteria." in resp.text


def test_last_name_returns_two_millers_in_order(auth):
    resp = auth.get("/mbrinq/results", params={"last_name": "miller"})
    assert "2 record(s) found." in resp.text
    assert resp.text.index("100106") < resp.text.index("100107")


def test_last_name_is_case_insensitive(auth):
    resp = auth.get("/mbrinq/results", params={"last_name": "MILLER"})
    assert "100106" in resp.text and "100107" in resp.text


def test_exact_member_no_one_row_no_redirect(auth):
    resp = auth.get("/mbrinq/results", params={"member_no": "100101"})
    assert resp.status_code == 200
    assert "1 record(s) found." in resp.text
    assert resp.text.count("_lnkSel") == 1
    assert 'href="/member/100101"' in resp.text


def test_name_rendered_last_first_uppercase(auth):
    resp = auth.get("/mbrinq/results", params={"member_no": "100101"})
    assert "NAKAMURA, ALICE" in resp.text


# ---------------------------------------------------------------------------
# Member record + shares panel
# ---------------------------------------------------------------------------
def test_member_record_fields_and_iframe(auth):
    resp = auth.get("/member/100101")
    assert resp.status_code == 200
    assert "MEMBER RECORD" in resp.text
    assert "NAKAMURA, ALICE" in resp.text
    assert_iframe_has_title(resp.text, "shares-panel")


def test_member_record_not_found(auth):
    resp = auth.get("/member/999999")
    assert resp.status_code == 200
    assert "Member record not found." in resp.text


def test_restricted_member_shows_banner(auth):
    resp = auth.get("/member/100108")  # Grace Liu, RESTRICTED
    assert "Member status is RESTRICTED. Some functions are unavailable." in resp.text


def test_shares_100101_balance_and_distinct_avail_ledger(auth):
    resp = auth.get("/member/100101/shares")
    assert "12,845.50" in resp.text          # savings
    assert "412.09" in resp.text             # checking available
    assert "460.09" in resp.text             # checking ledger (distinct)


def test_shares_100102_checking_no_savings(auth):
    resp = auth.get("/member/100102/shares")
    assert "CHECKING" in resp.text
    assert "PRIMARY SAVINGS" not in resp.text


def test_shares_100111_zero_renders(auth):
    resp = auth.get("/member/100111/shares")
    assert "0.00" in resp.text


def test_shares_100110_large_number_comma_grouped(auth):
    resp = auth.get("/member/100110/shares")
    assert "74,209.99" in resp.text


def test_shares_100119_open_and_frozen(auth):
    resp = auth.get("/member/100119/shares")
    assert "OPEN" in resp.text
    assert "FROZEN" in resp.text


def test_apostrophe_name_escaped_unmangled(auth):
    resp = auth.get("/member/100113")  # Liam O'Connor
    assert "O&#39;CONNOR, LIAM" in resp.text


def test_non_ascii_name_unmangled(auth):
    resp = auth.get("/member/100115")  # Noah Bergström
    assert "BERGSTRÖM, NOAH" in resp.text


# ---------------------------------------------------------------------------
# Function-code (fn) flow
# ---------------------------------------------------------------------------
def test_fn_card_heading_and_hidden_input(auth):
    resp = auth.get("/mbrinq", params={"fn": "CARD"})
    assert "MEMBER INQUIRY - CARD MAINTENANCE" in resp.text
    assert 'name="fn"' in resp.text and 'value="CARD"' in resp.text


def test_fn_card_results_action_targets_cards(auth):
    resp = auth.get("/mbrinq/results", params={"member_no": "100101", "fn": "CARD"})
    assert 'href="/member/100101/cards"' in resp.text


# ---------------------------------------------------------------------------
# Tenant heterogeneity
# ---------------------------------------------------------------------------
def test_summit_labels(auth, monkeypatch):
    monkeypatch.setenv("TENANT", "summit")
    assert "Member ID" in auth.get("/mbrinq").text
    assert "VIEW" in auth.get("/mbrinq/results", params={"member_no": "100101"}).text
    assert "AVAIL BAL" in auth.get("/member/100101/shares").text


def test_results_column_order_differs_between_tenants():
    rb = [c["header"] for c in CONFIG["riverbend"]["results_columns"]]
    su = [c["header"] for c in CONFIG["summit"]["results_columns"]]
    assert rb.index("JOINED") < rb.index("BRANCH")   # Riverbend: JOINED then BRANCH
    assert su.index("BRANCH") < su.index("JOINED")   # Summit: BRANCH then JOINED
    assert rb.index("JOINED") != su.index("JOINED")


# ---------------------------------------------------------------------------
# AX contract (the multi-tenant guard)
# ---------------------------------------------------------------------------
def _results_headers(tenant):
    return [c["header"] for c in CONFIG[tenant]["results_columns"]]


def _shares_headers(tenant):
    return [c["header"] for c in CONFIG[tenant]["shares_columns"]]


def test_ax_contract_riverbend(auth):
    results = auth.get("/mbrinq/results", params={"member_no": "100101"}).text
    assert_table_has_caption(results, "ctl00_MainContent_grdMembers", "Member search results")
    assert_column_headers(results, "ctl00_MainContent_grdMembers", _results_headers("riverbend"))

    shares = auth.get("/member/100101/shares").text
    assert_table_has_caption(shares, "ctl00_MainContent_grdShares", "Share accounts")
    assert_column_headers(shares, "ctl00_MainContent_grdShares", _shares_headers("riverbend"))


def test_ax_contract_summit(auth, monkeypatch):
    monkeypatch.setenv("TENANT", "summit")
    results = auth.get("/mbrinq/results", params={"member_no": "100101"}).text
    assert_table_has_caption(results, "ctl00_MainContent_grdMembers", "Member search results")
    assert_column_headers(results, "ctl00_MainContent_grdMembers", _results_headers("summit"))

    shares = auth.get("/member/100101/shares").text
    assert_column_headers(shares, "ctl00_MainContent_grdShares", _shares_headers("summit"))


# ---------------------------------------------------------------------------
# Rendering invariants
# ---------------------------------------------------------------------------
INVARIANT_ROUTES = [
    "/mbrinq",
    "/mbrinq/results?member_no=100101",
    "/member/100101",
    "/member/100101/shares",
]


@pytest.mark.parametrize("route", INVARIANT_ROUTES)
def test_no_data_testid_and_labelled_inputs(auth, route):
    html = auth.get(route).text
    assert_no_data_testid(html)
    assert_visible_inputs_have_labels(html)
