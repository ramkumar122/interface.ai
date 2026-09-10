"""Runtime-condition injection tests."""

import pytest
from fastapi.testclient import TestClient

import coredesk.inject as inject
import reset_db
from coredesk.app import app
from coredesk.inject import clear_injection


def _hdr(name):
    return {"X-CoreDesk-Inject": name}


@pytest.fixture(autouse=True)
def _fresh():
    reset_db.rebuild()
    clear_injection()
    yield
    clear_injection()


@pytest.fixture(scope="module")
def auth():
    c = TestClient(app)
    c.post("/signon", data={"userid": "mreyes", "password": "demo1234"})
    return c


# ---------------------------------------------------------------------------
# Idle = invisible
# ---------------------------------------------------------------------------
def test_idle_middleware_is_invisible():
    resp = TestClient(app).get("/")
    assert resp.status_code == 200
    assert "CORE SIGN ON" in resp.text
    for marker in ("ERR-7731", 'role="dialog"', "Unable to retrieve records"):
        assert marker not in resp.text


# ---------------------------------------------------------------------------
# Header-driven conditions
# ---------------------------------------------------------------------------
def test_app_error_500_page():
    resp = TestClient(app).get("/", headers=_hdr("app_error"))
    assert resp.status_code == 500
    assert "CoreDesk Application Error" in resp.text
    assert "Reference ERR-7731" in resp.text


def test_maintenance_dialog_injected():
    resp = TestClient(app).get("/", headers=_hdr("maintenance_notice"))
    assert 'role="dialog"' in resp.text
    assert 'aria-label="Scheduled maintenance"' in resp.text
    assert "Scheduled maintenance Saturday 02:00-06:00" in resp.text
    assert "Close" in resp.text


def test_db_timeout_message_injected():
    resp = TestClient(app).get("/", headers=_hdr("db_timeout"))
    assert "Unable to retrieve records. Please retry." in resp.text


def test_session_expired_redirects():
    noredir = TestClient(app, follow_redirects=False)
    resp = noredir.get("/", headers=_hdr("session_expired"))
    assert resp.status_code == 302
    assert resp.headers["location"] == "/?expired=1"
    # the sign-on page shows the expired notice
    assert "Your session has expired." in TestClient(app).get("/?expired=1").text


def test_readonly_role_forces_teller(auth):
    resp = auth.post(
        "/member/100101/cards/CRD-100101-1/maint",
        data={"action": "LOCK", "reason": "Member request", "notes": ""},
        headers=_hdr("readonly_role"),
    )
    assert "Your role does not permit this function." in resp.text


def test_slow_uses_patched_sleep(monkeypatch):
    calls = []
    monkeypatch.setattr(inject.time, "sleep", lambda s: calls.append(s))
    TestClient(app).get("/", headers=_hdr("slow"))
    assert calls == [inject.SLOW_SECONDS]


# ---------------------------------------------------------------------------
# /admin/inject flag fires once then clears
# ---------------------------------------------------------------------------
def test_admin_inject_one_shot():
    client = TestClient(app)
    client.post("/admin/inject", data={"op": "apply", "name": "app_error", "count": "1"})
    first = client.get("/")
    assert first.status_code == 500 and "ERR-7731" in first.text
    second = client.get("/")
    assert second.status_code == 200 and "ERR-7731" not in second.text


def test_admin_page_not_injected():
    # /admin/* is exempt even when a flag is set.
    client = TestClient(app)
    client.post("/admin/inject", data={"op": "apply", "name": "app_error", "count": "1"})
    resp = client.get("/admin/inject")
    assert resp.status_code == 200
    assert "RUNTIME CONDITION INJECTION" in resp.text
    clear_injection()
