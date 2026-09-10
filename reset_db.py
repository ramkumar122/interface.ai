"""Rebuild the CoreDesk database from schema + fixtures.

Usage:
    python reset_db.py

Deletes coredesk.db (or whatever COREDESK_DB points at), recreates the schema,
seeds the fixtures, then prints a summary. Idempotent: because seed data and the
password salts are fixed, running it twice produces identical data.
"""

import os
import sys

from db.connection import db_path, get_connection, init_schema
from db import queries


def rebuild(path=None):
    """Drop, create, and seed the database. Returns a summary dict."""
    target = path or db_path()
    if os.path.exists(target):
        os.remove(target)

    conn = get_connection(target)
    try:
        init_schema(conn)
        queries.seed(conn)
        conn.commit()
        summary = _summarize(conn)
    finally:
        conn.close()
    return summary


# Fixed whitelist of table names. These are not user input, and table names
# cannot be bound as SQL parameters, so interpolating from this constant list
# is safe.
_TABLES = ["staff", "member", "share", "card", "txn", "share_request", "audit_log"]


def _summarize(conn):
    counts = {
        t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
        for t in _TABLES
    }
    members = conn.execute(
        "SELECT m.member_no, m.first_name, m.last_name, m.status, "
        "(SELECT COUNT(*) FROM share s WHERE s.member_no = m.member_no) AS share_count, "
        "(SELECT COUNT(*) FROM card c WHERE c.member_no = m.member_no) AS card_count "
        "FROM member m ORDER BY m.member_no ASC"
    ).fetchall()
    return {"counts": counts, "members": members}


def _print_summary(summary, target):
    print("CoreDesk database rebuilt: %s" % target)

    print("\nRow counts")
    print("  %-14s %s" % ("TABLE", "ROWS"))
    for table in _TABLES:
        print("  %-14s %d" % (table, summary["counts"][table]))

    print("\nMembers (%d)" % len(summary["members"]))
    print("  %-10s %-22s %-11s %6s %6s" % ("MEMBER_NO", "NAME", "STATUS", "SHARES", "CARDS"))
    for r in summary["members"]:
        name = "%s %s" % (r["first_name"], r["last_name"])
        print("  %-10s %-22s %-11s %6d %6d" % (
            r["member_no"], name, r["status"], r["share_count"], r["card_count"]
        ))


def main(argv=None):
    target = db_path()
    summary = rebuild(target)
    _print_summary(summary, target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
