"""SQLite connection helper for CoreDesk.

One place to open connections so every caller gets the same behavior:
  - rows come back as sqlite3.Row (name-addressable, converted to typed rows
    before leaving queries.py)
  - foreign key enforcement is ON (off by default in sqlite3)

The database file lives at the repo root as coredesk.db. Both tenants share
this single file; tenant differences are presentation only. Tests point at a
throwaway file via the COREDESK_DB environment variable.
"""

import os
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)

DEFAULT_DB_PATH = os.path.join(_ROOT, "coredesk.db")
SCHEMA_PATH = os.path.join(_HERE, "schema.sql")


def db_path():
    """Resolve the active database file path.

    Honors COREDESK_DB so tests (and alternate deployments) can redirect
    the whole data layer at a different file without code changes.
    """
    return os.environ.get("COREDESK_DB", DEFAULT_DB_PATH)


def get_connection(path=None):
    """Open a configured sqlite3 connection."""
    conn = sqlite3.connect(path or db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn):
    """Create all tables and indexes by executing schema.sql."""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        conn.executescript(fh.read())
