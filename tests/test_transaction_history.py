"""Transaction History tests (read-only: ordering, parentheses, paging)."""

import re

import pytest
from fastapi.testclient import TestClient

import reset_db
from coredesk.app import app
from helpers import assert_no_data_testid, assert_visible_inputs_have_labels

CHK = "SHR-100101-0070"  # 100101 checking, 12 seeded rows
WIDE = {"from": "01/01/2026", "to": "12/31/2026"}


@pytest.fixture(autouse=True)
def _fresh():
    reset_db.rebuild()
    yield


@pytest.fixture(scope="module")
def auth():
    c = TestClient(app)
    c.post("/signon", data={"userid": "mreyes", "password": "demo1234"})
    return c


def _amount_cells(html):
    # <td align="right"> whose content starts with a digit or '(' is a money cell
    # (the form's label cells are align="right"><label..., which won't match).
    return re.findall(r'align="right">[(\d]', html)


def test_orders_most_recent_first(auth):
    html = auth.get("/member/100101/transactions", params={**WIDE, "share": CHK}).text
    assert html.index("09/02/2026") < html.index("06/25/2026")


def test_empty_range_message(auth):
    html = auth.get(
        "/member/100101/transactions",
        params={"share": CHK, "from": "01/01/2020", "to": "12/31/2020"},
    ).text
    assert "No transactions found for the selected period." in html


def test_reversed_dates_validation(auth):
    html = auth.get(
        "/member/100101/transactions",
        params={"share": CHK, "from": "12/31/2026", "to": "01/01/2026"},
    ).text
    assert "From date must be on or before To date." in html


def test_debit_parenthesised_credit_plain(auth):
    html = auth.get("/member/100101/transactions", params={**WIDE, "share": CHK}).text
    assert "(31.15)" in html          # debit in parentheses
    assert "2,450.00" in html         # credit plain
    assert "-31.15" not in html       # never a bare minus form


def test_paging(auth):
    p1 = auth.get(
        "/member/100101/transactions", params={**WIDE, "share": CHK, "offset": "0"}
    ).text
    assert len(_amount_cells(p1)) == 10
    assert 'id="ctl00_MainContent_lnkNext"' in p1
    assert 'id="ctl00_MainContent_lnkPrev"' not in p1
    assert "06/15/2026" not in p1     # oldest row not on page 1

    p2 = auth.get(
        "/member/100101/transactions", params={**WIDE, "share": CHK, "offset": "10"}
    ).text
    assert len(_amount_cells(p2)) == 2
    assert 'id="ctl00_MainContent_lnkPrev"' in p2
    assert 'id="ctl00_MainContent_lnkNext"' not in p2
    assert "06/15/2026" in p2         # oldest rows on page 2
    assert "09/02/2026" not in p2


def test_invariants(auth):
    html = auth.get("/member/100101/transactions", params={**WIDE, "share": CHK}).text
    assert_no_data_testid(html)
    assert_visible_inputs_have_labels(html)
