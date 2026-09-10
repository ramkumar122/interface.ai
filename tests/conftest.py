"""Shared test fixtures for CoreDesk.

The seeded_db fixture points COREDESK_DB at a throwaway file and rebuilds it
from schema + fixtures once per session, so every test module runs against
seeded data without touching the real coredesk.db. It is autouse, so tests get
it for free.
"""

import os
import tempfile

import pytest

import reset_db


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    fd, path = tempfile.mkstemp(prefix="coredesk_test_", suffix=".db")
    os.close(fd)
    os.environ["COREDESK_DB"] = path
    reset_db.rebuild(path)
    yield path
    os.environ.pop("COREDESK_DB", None)
    try:
        os.remove(path)
    except OSError:
        pass
