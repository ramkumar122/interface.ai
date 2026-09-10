"""CoreDesk data access -- the ONLY module that writes SQL.

Every statement is parameterized (never f-string interpolation of values).
Rows are converted to typed NamedTuples before leaving this module, so a raw
sqlite3.Row never leaks upward. Money stays in integer cents throughout.
"""

import hashlib
import hmac
import random
from datetime import date, datetime
from typing import NamedTuple, Optional

from db.connection import get_connection
from db.money import display_to_cents

# ---------------------------------------------------------------------------
# Password hashing (stdlib only -- no bcrypt/passlib)
# ---------------------------------------------------------------------------
_PBKDF2_ALGO = "sha256"
_PBKDF2_ITERATIONS = 200_000


def hash_password(password, salt):
    """Return a self-describing PBKDF2 hash string: algo$iters$salt$hash."""
    dk = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2_%s$%d$%s$%s" % (_PBKDF2_ALGO, _PBKDF2_ITERATIONS, salt.hex(), dk.hex())


def verify_password(password, stored):
    """Constant-time verify of a password against a stored hash string."""
    try:
        scheme, iters_s, salt_hex, hash_hex = stored.split("$")
        algo = scheme.split("_", 1)[1]
        iterations = int(iters_s)
        salt = bytes.fromhex(salt_hex)
    except (ValueError, AttributeError, IndexError):
        return False
    dk = hashlib.pbkdf2_hmac(algo, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(dk.hex(), hash_hex)


# ---------------------------------------------------------------------------
# Typed row shapes (field names match the SELECTed column names)
# ---------------------------------------------------------------------------
class StaffRow(NamedTuple):
    username: str
    display_name: str
    role: str
    branch: str


class MemberRow(NamedTuple):
    member_no: str
    first_name: str
    last_name: str
    dob: str
    status: str
    addr_line1: str
    addr_line2: Optional[str]
    city: str
    state: str
    zip: str
    phone: str
    email: Optional[str]
    joined_on: str
    branch: str


class ShareRow(NamedTuple):
    share_id: str
    member_no: str
    suffix: str
    share_type: str
    description: str
    balance_available_cents: int
    balance_ledger_cents: int
    status: str
    opened_on: str


class CardRow(NamedTuple):
    card_id: str
    member_no: str
    last4: str
    network: str
    status: str
    linked_share_id: Optional[str]
    issued_on: str


class TxnRow(NamedTuple):
    txn_id: str
    share_id: str
    posted_on: str
    description: str
    amount_cents: int
    txn_type: str


class ShareRequestRow(NamedTuple):
    request_id: str
    member_no: str
    share_type: str
    description: str
    initial_deposit_cents: int
    funding_share_id: Optional[str]
    status: str
    created_by: str
    created_at: str


def _to(row_cls, row):
    """Convert a sqlite3.Row to the given NamedTuple, or None."""
    if row is None:
        return None
    return row_cls(**{field: row[field] for field in row_cls._fields})


# ---------------------------------------------------------------------------
# Reference number generation
#
# References (CRD-#####, ADR-#####, SRQ-#####) are short tracking numbers.
# Generated once per call so the value returned to the caller is the same one
# recorded in audit_log within that transaction.
# ---------------------------------------------------------------------------
def _make_reference(prefix, digits=5):
    low = 10 ** (digits - 1)
    high = (10 ** digits) - 1
    return "%s-%d" % (prefix, random.randint(low, high))


_SUFFIX_BY_TYPE = {
    "PRIMARY_SAVINGS": "0000",
    "CHECKING": "0070",
    "MONEY_MARKET": "0110",
    "CERTIFICATE": "0120",
}


def _now():
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Authentication / staff
# ---------------------------------------------------------------------------
def authenticate(username, password):
    """Return the StaffRow if credentials are valid, else None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM staff WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return _to(StaffRow, row)
    finally:
        conn.close()


def get_staff(username):
    """Return a StaffRow for the username, or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM staff WHERE username = ?", (username,)
        ).fetchone()
        return _to(StaffRow, row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------
def find_members(member_no, last_name):
    """Search members.

    - member_no given: exact match (0 or 1 rows); member_no wins over last_name
    - last_name given: case-insensitive prefix match
    - neither: empty list
    - always ordered by member_no ascending
    """
    conn = get_connection()
    try:
        if member_no:
            rows = conn.execute(
                "SELECT * FROM member WHERE member_no = ? ORDER BY member_no ASC",
                (member_no,),
            ).fetchall()
        elif last_name:
            # SQLite LIKE is case-insensitive for ASCII by default; the trailing
            # % makes it a prefix match. The value is bound, never interpolated.
            rows = conn.execute(
                "SELECT * FROM member WHERE last_name LIKE ? ORDER BY member_no ASC",
                (last_name + "%",),
            ).fetchall()
        else:
            return []
        return [_to(MemberRow, r) for r in rows]
    finally:
        conn.close()


def get_member(member_no):
    """Return a MemberRow, or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM member WHERE member_no = ?", (member_no,)
        ).fetchone()
        return _to(MemberRow, row)
    finally:
        conn.close()


def update_member_address(member_no, line1, line2, city, state, zip, effective_date,
                          staff_username, actor):
    """Update a member's mailing address; audit it. Returns an 'ADR-#####' ref."""
    conn = get_connection()
    try:
        exists = conn.execute(
            "SELECT 1 FROM member WHERE member_no = ?", (member_no,)
        ).fetchone()
        if exists is None:
            raise ValueError("member not found: %s" % member_no)

        conn.execute(
            "UPDATE member SET addr_line1 = ?, addr_line2 = ?, city = ?, "
            "state = ?, zip = ? WHERE member_no = ?",
            (line1, line2, city, state, zip, member_no),
        )
        reference = _make_reference("ADR")
        _insert_audit(
            conn,
            staff_username=staff_username,
            actor=actor,
            action="ADDRESS_UPDATE",
            member_no=member_no,
            reference=reference,
            detail="Address updated, effective %s" % effective_date,
        )
        conn.commit()
        return reference
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Shares
# ---------------------------------------------------------------------------
def list_shares(member_no):
    """All shares for a member, ordered by suffix."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM share WHERE member_no = ? ORDER BY suffix ASC",
            (member_no,),
        ).fetchall()
        return [_to(ShareRow, r) for r in rows]
    finally:
        conn.close()


def get_primary_savings(member_no):
    """Return the OPEN primary-savings share, or None.

    Deliberately returns None when the only savings is FROZEN or CLOSED.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM share WHERE member_no = ? "
            "AND share_type = 'PRIMARY_SAVINGS' AND status = 'OPEN' "
            "ORDER BY suffix ASC LIMIT 1",
            (member_no,),
        ).fetchone()
        return _to(ShareRow, row)
    finally:
        conn.close()


def get_share(share_id):
    """Return a ShareRow, or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM share WHERE share_id = ?", (share_id,)
        ).fetchone()
        return _to(ShareRow, row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
def list_cards(member_no):
    """All cards for a member, ordered by card_id."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM card WHERE member_no = ? ORDER BY card_id ASC",
            (member_no,),
        ).fetchall()
        return [_to(CardRow, r) for r in rows]
    finally:
        conn.close()


def get_card(card_id):
    """Return a CardRow, or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM card WHERE card_id = ?", (card_id,)
        ).fetchone()
        return _to(CardRow, row)
    finally:
        conn.close()


_CARD_ACTION = {
    "LOCKED": "CARD_LOCK",
    "ACTIVE": "CARD_UNLOCK",
    "HOTLISTED": "CARD_HOTLIST",
    "EXPIRED": "CARD_EXPIRE",
}


def set_card_status(card_id, new_status, reason, notes, staff_username, actor):
    """Change a card's status and write exactly one audit row.

    Returns a 'CRD-#####' reference recorded in that audit row.
    """
    conn = get_connection()
    try:
        card = conn.execute(
            "SELECT member_no FROM card WHERE card_id = ?", (card_id,)
        ).fetchone()
        if card is None:
            raise ValueError("card not found: %s" % card_id)

        conn.execute(
            "UPDATE card SET status = ? WHERE card_id = ?", (new_status, card_id)
        )
        reference = _make_reference("CRD")
        detail = "; ".join(p for p in (reason, notes) if p)
        _insert_audit(
            conn,
            staff_username=staff_username,
            actor=actor,
            action=_CARD_ACTION.get(new_status, "CARD_STATUS"),
            member_no=card["member_no"],
            reference=reference,
            detail=detail,
        )
        conn.commit()
        return reference
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------
def list_transactions(share_id, date_from, date_to):
    """Transactions for a share, optionally bounded by inclusive date range.

    Ordered by posted_on then txn_id for deterministic output.
    """
    conn = get_connection()
    try:
        sql = "SELECT * FROM txn WHERE share_id = ?"
        params = [share_id]
        if date_from:
            sql += " AND posted_on >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND posted_on <= ?"
            params.append(date_to)
        sql += " ORDER BY posted_on ASC, txn_id ASC"
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [_to(TxnRow, r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Share-open requests
# ---------------------------------------------------------------------------
def create_share_request(member_no, share_type, description, initial_deposit_cents,
                         funding_share_id, created_by):
    """Create a DRAFT share request. Returns its 'SRQ-#####' request_id."""
    conn = get_connection()
    try:
        request_id = _make_reference("SRQ")
        conn.execute(
            "INSERT INTO share_request (request_id, member_no, share_type, "
            "description, initial_deposit_cents, funding_share_id, status, "
            "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?)",
            (request_id, member_no, share_type, description, initial_deposit_cents,
             funding_share_id, created_by, _now()),
        )
        conn.commit()
        return request_id
    finally:
        conn.close()


def get_share_request(request_id):
    """Return a ShareRequestRow, or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM share_request WHERE request_id = ?", (request_id,)
        ).fetchone()
        return _to(ShareRequestRow, row)
    finally:
        conn.close()


def commit_share_request(request_id, staff_username, actor):
    """Turn a DRAFT request into a real OPEN share. Returns the new share_id."""
    conn = get_connection()
    try:
        req = conn.execute(
            "SELECT * FROM share_request WHERE request_id = ?", (request_id,)
        ).fetchone()
        if req is None:
            raise ValueError("share request not found: %s" % request_id)
        if req["status"] != "DRAFT":
            raise ValueError("share request not in DRAFT state: %s" % request_id)

        member_no = req["member_no"]
        share_type = req["share_type"]

        # Pick a free suffix: start from the type's conventional suffix and bump
        # until UNIQUE(member_no, suffix) is satisfied.
        taken = {
            r["suffix"]
            for r in conn.execute(
                "SELECT suffix FROM share WHERE member_no = ?", (member_no,)
            ).fetchall()
        }
        n = int(_SUFFIX_BY_TYPE.get(share_type, "0100"))
        while "%04d" % n in taken:
            n += 1
        suffix = "%04d" % n

        share_id = "SHR-%s-%s" % (member_no, suffix)
        deposit = req["initial_deposit_cents"]
        opened_on = date.today().isoformat()

        conn.execute(
            "INSERT INTO share (share_id, member_no, suffix, share_type, "
            "description, balance_available_cents, balance_ledger_cents, status, "
            "opened_on) VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)",
            (share_id, member_no, suffix, share_type, req["description"],
             deposit, deposit, opened_on),
        )
        conn.execute(
            "UPDATE share_request SET status = 'COMMITTED' WHERE request_id = ?",
            (request_id,),
        )
        _insert_audit(
            conn,
            staff_username=staff_username,
            actor=actor,
            action="SHARE_OPEN",
            member_no=member_no,
            reference=share_id,
            detail="Opened %s from %s" % (share_type, request_id),
        )
        conn.commit()
        return share_id
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def _insert_audit(conn, staff_username, actor, action, member_no, reference, detail):
    """Insert an audit row on an existing connection (no commit here)."""
    conn.execute(
        "INSERT INTO audit_log (ts, staff_username, actor, action, member_no, "
        "reference, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (_now(), staff_username, actor, action, member_no, reference, detail),
    )


def write_audit(staff_username, actor, action, member_no, reference, detail):
    """Public audit writer: opens its own connection and commits."""
    conn = get_connection()
    try:
        _insert_audit(conn, staff_username, actor, action, member_no, reference, detail)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Seeding (used by reset_db)
# ---------------------------------------------------------------------------
def seed(conn):
    """Insert all fixture data on an existing connection. Caller commits."""
    from db import fixtures as fx

    for s in fx.STAFF:
        password_hash = hash_password(s["password"], bytes.fromhex(s["salt"]))
        conn.execute(
            "INSERT INTO staff (username, password_hash, display_name, role, branch) "
            "VALUES (?, ?, ?, ?, ?)",
            (s["username"], password_hash, s["display_name"], s["role"], s["branch"]),
        )

    for m in fx.MEMBERS:
        conn.execute(
            "INSERT INTO member (member_no, first_name, last_name, dob, status, "
            "addr_line1, addr_line2, city, state, zip, phone, email, joined_on, "
            "branch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (m["member_no"], m["first_name"], m["last_name"], m["dob"], m["status"],
             m["addr_line1"], m["addr_line2"], m["city"], m["state"], m["zip"],
             m["phone"], m["email"], m["joined_on"], m["branch"]),
        )

    for sh in fx.SHARES:
        conn.execute(
            "INSERT INTO share (share_id, member_no, suffix, share_type, description, "
            "balance_available_cents, balance_ledger_cents, status, opened_on) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (sh["share_id"], sh["member_no"], sh["suffix"], sh["share_type"],
             sh["description"], display_to_cents(sh["balance_available"]),
             display_to_cents(sh["balance_ledger"]), sh["status"], sh["opened_on"]),
        )

    for c in fx.CARDS:
        conn.execute(
            "INSERT INTO card (card_id, member_no, last4, network, status, "
            "linked_share_id, issued_on) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (c["card_id"], c["member_no"], c["last4"], c["network"], c["status"],
             c["linked_share_id"], c["issued_on"]),
        )

    for t in fx.TXNS:
        conn.execute(
            "INSERT INTO txn (txn_id, share_id, posted_on, description, amount_cents, "
            "txn_type) VALUES (?, ?, ?, ?, ?, ?)",
            (t["txn_id"], t["share_id"], t["posted_on"], t["description"],
             display_to_cents(t["amount"]), t["txn_type"]),
        )

    for a in fx.AUDIT_SEED:
        conn.execute(
            "INSERT INTO audit_log (ts, staff_username, actor, action, member_no, "
            "reference, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (a["ts"], a["staff_username"], a["actor"], a["action"], a["member_no"],
             a["reference"], a["detail"]),
        )
