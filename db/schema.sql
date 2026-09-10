-- CoreDesk schema (Task 2).
-- Dates are TEXT in YYYY-MM-DD form. All money columns are INTEGER CENTS.
-- Foreign keys are enforced by the connection (PRAGMA foreign_keys = ON).

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Staff / sign-on
-- ---------------------------------------------------------------------------
CREATE TABLE staff (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    display_name  TEXT NOT NULL,                 -- e.g. "M. REYES"
    role          TEXT NOT NULL CHECK(role IN ('MSR','TELLER_RO','SUPERVISOR')),
    branch        TEXT NOT NULL                  -- '001'
);

-- ---------------------------------------------------------------------------
-- Members
-- ---------------------------------------------------------------------------
CREATE TABLE member (
    member_no   TEXT PRIMARY KEY,                -- 6 digits, TEXT to keep leading zeros
    first_name  TEXT NOT NULL,
    last_name   TEXT NOT NULL,
    dob         TEXT NOT NULL,
    status      TEXT NOT NULL CHECK(status IN ('ACTIVE','INACTIVE','RESTRICTED')),
    addr_line1  TEXT NOT NULL,
    addr_line2  TEXT,
    city        TEXT NOT NULL,
    state       TEXT NOT NULL,                   -- 2 letter
    zip         TEXT NOT NULL,
    phone       TEXT NOT NULL,
    email       TEXT,
    joined_on   TEXT NOT NULL,
    branch      TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Shares (accounts)
-- ---------------------------------------------------------------------------
CREATE TABLE share (
    share_id                TEXT PRIMARY KEY,    -- 'SHR-100101-0000'
    member_no               TEXT NOT NULL REFERENCES member(member_no),
    suffix                  TEXT NOT NULL,       -- '0000', '0070', '0110'
    share_type              TEXT NOT NULL CHECK(share_type IN
                              ('PRIMARY_SAVINGS','CHECKING','MONEY_MARKET','CERTIFICATE')),
    description             TEXT NOT NULL,       -- 'Regular Share', 'Free Checking'
    balance_available_cents INTEGER NOT NULL,
    balance_ledger_cents    INTEGER NOT NULL,
    status                  TEXT NOT NULL CHECK(status IN ('OPEN','CLOSED','FROZEN')),
    opened_on               TEXT NOT NULL,
    UNIQUE(member_no, suffix)
);

-- ---------------------------------------------------------------------------
-- Cards
-- ---------------------------------------------------------------------------
CREATE TABLE card (
    card_id         TEXT PRIMARY KEY,            -- 'CRD-100101-1'
    member_no       TEXT NOT NULL REFERENCES member(member_no),
    last4           TEXT NOT NULL,               -- ONLY the last 4. No full PAN anywhere.
    network         TEXT NOT NULL CHECK(network IN ('VISA_DEBIT','VISA_CREDIT')),
    status          TEXT NOT NULL CHECK(status IN ('ACTIVE','LOCKED','HOTLISTED','EXPIRED')),
    linked_share_id TEXT REFERENCES share(share_id),
    issued_on       TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Transactions
-- ---------------------------------------------------------------------------
CREATE TABLE txn (
    txn_id      TEXT PRIMARY KEY,
    share_id    TEXT NOT NULL REFERENCES share(share_id),
    posted_on   TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,               -- negative = debit
    txn_type    TEXT NOT NULL CHECK(txn_type IN
                  ('DEPOSIT','WITHDRAWAL','TRANSFER','FEE','DIVIDEND','POS','ACH'))
);

-- ---------------------------------------------------------------------------
-- Share-open draft requests
-- ---------------------------------------------------------------------------
CREATE TABLE share_request (
    request_id            TEXT PRIMARY KEY,      -- 'SRQ-40119'
    member_no             TEXT NOT NULL REFERENCES member(member_no),
    share_type            TEXT NOT NULL,
    description           TEXT NOT NULL,
    initial_deposit_cents INTEGER NOT NULL,
    funding_share_id      TEXT REFERENCES share(share_id),
    status                TEXT NOT NULL CHECK(status IN ('DRAFT','COMMITTED','CANCELLED')),
    created_by            TEXT NOT NULL,
    created_at            TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Audit log
-- ---------------------------------------------------------------------------
CREATE TABLE audit_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TEXT NOT NULL,
    staff_username TEXT NOT NULL,
    actor          TEXT NOT NULL CHECK(actor IN ('HUMAN','AUTOMATION')),
    action         TEXT NOT NULL,                -- 'CARD_LOCK','ADDRESS_UPDATE','SHARE_OPEN'
    member_no      TEXT,
    reference      TEXT,                         -- 'CRD-88213'
    detail         TEXT                          -- short, already redacted by the caller
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX idx_member_last_name ON member(last_name);
CREATE INDEX idx_share_member_no  ON share(member_no);
CREATE INDEX idx_card_member_no   ON card(member_no);
CREATE INDEX idx_txn_share_posted ON txn(share_id, posted_on);
