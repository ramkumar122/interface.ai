"""Data-layer tests for CoreDesk.

Runs against a throwaway database seeded from fixtures. COREDESK_DB is pointed
at a temp file before the data layer opens any connection, so the real
coredesk.db is never touched.
"""

import pytest

from db import queries
from db.money import cents_to_display, display_to_cents

# The seeded temp DB is provided by the shared, autouse fixture in conftest.py.


# ---------------------------------------------------------------------------
# authenticate / staff
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "username,role",
    [("mreyes", "MSR"), ("jtran", "TELLER_RO"), ("dpark", "SUPERVISOR")],
)
def test_authenticate_succeeds_for_all_users(username, role):
    staff = queries.authenticate(username, "demo1234")
    assert staff is not None
    assert staff.username == username
    assert staff.role == role


def test_authenticate_wrong_password():
    assert queries.authenticate("mreyes", "wrong-pass") is None


def test_authenticate_unknown_user():
    assert queries.authenticate("nobody", "demo1234") is None


def test_staff_row_hides_password_hash():
    staff = queries.authenticate("mreyes", "demo1234")
    assert not hasattr(staff, "password_hash")


# ---------------------------------------------------------------------------
# find_members
# ---------------------------------------------------------------------------
def test_find_members_unknown_member_no():
    assert queries.find_members("999999", None) == []


def test_find_members_last_name_returns_both_millers_in_order():
    rows = queries.find_members(None, "miller")
    assert [r.member_no for r in rows] == ["100106", "100107"]


def test_find_members_is_case_insensitive():
    rows = queries.find_members(None, "MILLER")
    assert [r.member_no for r in rows] == ["100106", "100107"]


def test_find_members_neither_arg_is_empty():
    assert queries.find_members(None, None) == []


def test_find_members_member_no_wins_over_last_name():
    rows = queries.find_members("100101", "miller")
    assert [r.member_no for r in rows] == ["100101"]


def test_find_members_apostrophe_name_round_trips():
    rows = queries.find_members(None, "o'connor")
    assert len(rows) == 1
    assert rows[0].member_no == "100113"
    assert rows[0].last_name == "O'Connor"


def test_find_members_non_ascii_name_round_trips():
    rows = queries.find_members(None, "berg")
    assert len(rows) == 1
    assert rows[0].member_no == "100115"
    assert rows[0].last_name == "Bergström"
    assert rows[0].first_name == "Noah"


# ---------------------------------------------------------------------------
# shares
# ---------------------------------------------------------------------------
def test_get_primary_savings_none_when_no_savings():
    assert queries.get_primary_savings("100102") is None


def test_get_primary_savings_balance():
    savings = queries.get_primary_savings("100101")
    assert savings is not None
    assert savings.balance_available_cents == 1284550


def test_get_primary_savings_skips_frozen_and_returns_open_savings():
    savings = queries.get_primary_savings("100119")
    assert savings is not None
    assert savings.share_id == "SHR-100119-0000"
    assert savings.share_type == "PRIMARY_SAVINGS"
    assert savings.status == "OPEN"


def test_list_shares_ordered_by_suffix():
    shares = queries.list_shares("100112")
    assert [s.suffix for s in shares] == ["0000", "0110"]


# ---------------------------------------------------------------------------
# cards
# ---------------------------------------------------------------------------
def test_list_cards_empty_when_none():
    assert queries.list_cards("100109") == []


def test_list_cards_ambiguous_has_two():
    assert len(queries.list_cards("100112")) == 2


def test_get_card_locked():
    card = queries.get_card("CRD-100103-1")
    assert card is not None
    assert card.status == "LOCKED"


def test_set_card_status_locks_and_audits():
    # Use member 100120's card so this mutation does not disturb other cases.
    card_id = "CRD-100120-1"
    assert queries.get_card(card_id).status == "ACTIVE"

    reference = queries.set_card_status(
        card_id, "LOCKED", reason="Customer request", notes="phone",
        staff_username="mreyes", actor="HUMAN",
    )
    assert reference.startswith("CRD-")
    assert queries.get_card(card_id).status == "LOCKED"

    # Exactly one audit row carries this reference.
    conn = queries.get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE reference = ?", (reference,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


# ---------------------------------------------------------------------------
# transactions
# ---------------------------------------------------------------------------
def test_list_transactions_for_checking():
    txns = queries.list_transactions("SHR-100101-0070", None, None)
    assert len(txns) == 10
    # ordered ascending by posted_on
    dates = [t.posted_on for t in txns]
    assert dates == sorted(dates)


def test_list_transactions_date_bounded():
    txns = queries.list_transactions("SHR-100101-0070", "2026-07-01", "2026-07-31")
    assert all("2026-07-01" <= t.posted_on <= "2026-07-31" for t in txns)
    assert len(txns) == 4


# ---------------------------------------------------------------------------
# share requests
# ---------------------------------------------------------------------------
def test_share_request_create_get_commit():
    request_id = queries.create_share_request(
        member_no="100109", share_type="CHECKING", description="Free Checking",
        initial_deposit_cents=5000, funding_share_id="SHR-100109-0000",
        created_by="mreyes",
    )
    assert request_id.startswith("SRQ-")

    req = queries.get_share_request(request_id)
    assert req is not None
    assert req.status == "DRAFT"
    assert req.initial_deposit_cents == 5000

    new_share_id = queries.commit_share_request(request_id, "mreyes", "HUMAN")
    assert new_share_id == "SHR-100109-0070"

    assert queries.get_share_request(request_id).status == "COMMITTED"
    opened = queries.get_share(new_share_id)
    assert opened.status == "OPEN"
    assert opened.balance_available_cents == 5000


# ---------------------------------------------------------------------------
# money helpers
# ---------------------------------------------------------------------------
def test_cents_to_display_basic():
    assert cents_to_display(0) == "0.00"
    assert cents_to_display(7420999) == "74,209.99"
    assert cents_to_display(1284550) == "12,845.50"
    assert cents_to_display(-5420) == "-54.20"


def test_money_round_trip():
    for n in [0, 1, 99, 100, 999, 1000, 1284550, 7420999, 100000000, -5420, -1]:
        assert display_to_cents(cents_to_display(n)) == n


def test_cents_to_display_rejects_float():
    with pytest.raises(TypeError):
        cents_to_display(12.5)
