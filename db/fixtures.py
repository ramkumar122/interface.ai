"""Seed data for CoreDesk, as readable Python literals.

Money is written here as decimal strings (e.g. "12,845.50"); the seeder
converts them to integer cents via db.money.display_to_cents. Storing them
as strings keeps this file diffable and mirrors the source-of-truth table in
the task spec, while the actual column stays INTEGER CENTS.

Every member below is shaped to trigger one named test condition. Member
999999 deliberately does NOT exist (that is the MEMBER_NOT_FOUND case).

DO NOT log the canary string that appears in AUDIT_SEED.
"""

# Each staff row carries a fixed per-user salt (hex) so seeding is fully
# deterministic and idempotent: two runs produce byte-identical hashes.
# All three passwords are "demo1234".
STAFF = [
    {"username": "mreyes", "password": "demo1234", "salt": "a1b2c3d4e5f6a7b8",
     "display_name": "M. REYES", "role": "MSR", "branch": "001"},
    {"username": "jtran", "password": "demo1234", "salt": "1122334455667788",
     "display_name": "J. TRAN", "role": "TELLER_RO", "branch": "004"},
    {"username": "dpark", "password": "demo1234", "salt": "99aabbccddeeff00",
     "display_name": "D. PARK", "role": "SUPERVISOR", "branch": "001"},
]

MEMBERS = [
    {"member_no": "100101", "first_name": "Alice", "last_name": "Nakamura",
     "dob": "1985-06-15", "status": "ACTIVE",
     "addr_line1": "742 Evergreen Ter", "addr_line2": None, "city": "Springfield",
     "state": "OR", "zip": "97403", "phone": "541-555-0142",
     "email": "alice.nakamura@example.com", "joined_on": "2015-03-12", "branch": "001"},

    {"member_no": "100102", "first_name": "Ben", "last_name": "Ortiz",
     "dob": "1990-11-02", "status": "ACTIVE",
     "addr_line1": "88 Birch Ln", "addr_line2": None, "city": "Eugene",
     "state": "OR", "zip": "97401", "phone": "541-555-0188",
     "email": "ben.ortiz@example.com", "joined_on": "2018-07-01", "branch": "001"},

    {"member_no": "100103", "first_name": "Carla", "last_name": "Reyes",
     "dob": "1978-03-21", "status": "ACTIVE",
     "addr_line1": "1200 Willamette St", "addr_line2": "Apt 4", "city": "Eugene",
     "state": "OR", "zip": "97401", "phone": "541-555-0103",
     "email": "carla.reyes@example.com", "joined_on": "2016-11-23", "branch": "004"},

    {"member_no": "100104", "first_name": "David", "last_name": "Kim",
     "dob": "1965-09-30", "status": "INACTIVE",
     "addr_line1": "5 Oak Ave", "addr_line2": None, "city": "Salem",
     "state": "OR", "zip": "97301", "phone": "503-555-0104",
     "email": None, "joined_on": "2012-01-09", "branch": "001"},

    {"member_no": "100105", "first_name": "Elena", "last_name": "Novak",
     "dob": "1992-01-17", "status": "ACTIVE",
     "addr_line1": "3391 Cedar Ct", "addr_line2": None, "city": "Portland",
     "state": "OR", "zip": "97205", "phone": "503-555-0105",
     "email": "elena.novak@example.com", "joined_on": "2019-05-14", "branch": "001"},

    {"member_no": "100106", "first_name": "Frank", "last_name": "Miller",
     "dob": "1983-07-08", "status": "ACTIVE",
     "addr_line1": "210 Pine St", "addr_line2": None, "city": "Bend",
     "state": "OR", "zip": "97701", "phone": "541-555-0106",
     "email": "frank.miller@example.com", "joined_on": "2014-02-28", "branch": "004"},

    {"member_no": "100107", "first_name": "Frank", "last_name": "Miller",
     "dob": "1979-12-25", "status": "ACTIVE",
     "addr_line1": "77 Maple Dr", "addr_line2": "Unit 12", "city": "Corvallis",
     "state": "OR", "zip": "97330", "phone": "541-555-0107",
     "email": "f.miller2@example.com", "joined_on": "2017-09-30", "branch": "001"},

    {"member_no": "100108", "first_name": "Grace", "last_name": "Liu",
     "dob": "1988-04-14", "status": "RESTRICTED",
     "addr_line1": "909 Spruce Way", "addr_line2": None, "city": "Medford",
     "state": "OR", "zip": "97501", "phone": "541-555-0108",
     "email": "grace.liu@example.com", "joined_on": "2013-06-18", "branch": "004"},

    {"member_no": "100109", "first_name": "Henry", "last_name": "Patel",
     "dob": "1995-10-03", "status": "ACTIVE",
     "addr_line1": "44 Elm St", "addr_line2": None, "city": "Hillsboro",
     "state": "OR", "zip": "97124", "phone": "503-555-0109",
     "email": "henry.patel@example.com", "joined_on": "2020-10-05", "branch": "001"},

    {"member_no": "100110", "first_name": "Ingrid", "last_name": "Sorensen",
     "dob": "1970-02-11", "status": "ACTIVE",
     "addr_line1": "8800 Fjord Rd", "addr_line2": None, "city": "Astoria",
     "state": "OR", "zip": "97103", "phone": "503-555-0110",
     "email": "ingrid.sorensen@example.com", "joined_on": "2011-04-22", "branch": "004"},

    {"member_no": "100111", "first_name": "Jorge", "last_name": "Medina",
     "dob": "2000-08-29", "status": "ACTIVE",
     "addr_line1": "12 Sunset Blvd", "addr_line2": None, "city": "Gresham",
     "state": "OR", "zip": "97030", "phone": "503-555-0111",
     "email": "jorge.medina@example.com", "joined_on": "2021-01-15", "branch": "001"},

    {"member_no": "100112", "first_name": "Kavya", "last_name": "Raman",
     "dob": "1987-05-06", "status": "ACTIVE",
     "addr_line1": "560 Riverside Dr", "addr_line2": "Ste 300", "city": "Beaverton",
     "state": "OR", "zip": "97005", "phone": "503-555-0112",
     "email": "kavya.raman@example.com", "joined_on": "2016-08-08", "branch": "001"},

    {"member_no": "100113", "first_name": "Liam", "last_name": "O'Connor",
     "dob": "1991-06-19", "status": "ACTIVE",
     "addr_line1": "33 Shamrock Ln", "addr_line2": None, "city": "Tigard",
     "state": "OR", "zip": "97223", "phone": "503-555-0113",
     "email": "liam.oconnor@example.com", "joined_on": "2018-03-17", "branch": "004"},

    {"member_no": "100114", "first_name": "Mei", "last_name": "Zhang",
     "dob": "1982-09-09", "status": "ACTIVE",
     "addr_line1": "1450 Lotus St", "addr_line2": None, "city": "Lake Oswego",
     "state": "OR", "zip": "97034", "phone": "503-555-0114",
     "email": "mei.zhang@example.com", "joined_on": "2015-12-01", "branch": "001"},

    {"member_no": "100115", "first_name": "Noah", "last_name": "Bergström",
     "dob": "1996-03-28", "status": "ACTIVE",
     "addr_line1": "70 Nordic Ave", "addr_line2": None, "city": "Keizer",
     "state": "OR", "zip": "97303", "phone": "503-555-0115",
     "email": "noah.bergstrom@example.com", "joined_on": "2019-11-11", "branch": "001"},

    {"member_no": "100116", "first_name": "Olivia", "last_name": "Fontaine",
     "dob": "1975-11-16", "status": "ACTIVE",
     "addr_line1": "22 Rue Belle", "addr_line2": None, "city": "Ashland",
     "state": "OR", "zip": "97520", "phone": "541-555-0116",
     "email": "olivia.fontaine@example.com", "joined_on": "2017-06-25", "branch": "004"},

    {"member_no": "100117", "first_name": "Pavel", "last_name": "Novikov",
     "dob": "1969-07-22", "status": "ACTIVE",
     "addr_line1": "1900 Volga St", "addr_line2": None, "city": "Redmond",
     "state": "OR", "zip": "97756", "phone": "541-555-0117",
     "email": "pavel.novikov@example.com", "joined_on": "2013-09-14", "branch": "001"},

    {"member_no": "100118", "first_name": "Quinn", "last_name": "Adeyemi",
     "dob": "1993-12-05", "status": "ACTIVE",
     "addr_line1": "8 Unity Cir", "addr_line2": None, "city": "Albany",
     "state": "OR", "zip": "97321", "phone": "541-555-0118",
     "email": "quinn.adeyemi@example.com", "joined_on": "2022-02-20", "branch": "001"},

    {"member_no": "100119", "first_name": "Rosa", "last_name": "Delgado",
     "dob": "1986-04-01", "status": "ACTIVE",
     "addr_line1": "455 Sol Ave", "addr_line2": None, "city": "Woodburn",
     "state": "OR", "zip": "97071", "phone": "503-555-0119",
     "email": "rosa.delgado@example.com", "joined_on": "2014-07-07", "branch": "004"},

    {"member_no": "100120", "first_name": "Sam", "last_name": "Whitfield",
     "dob": "1990-10-10", "status": "ACTIVE",
     "addr_line1": "600 Harvest Rd", "addr_line2": None, "city": "Newberg",
     "state": "OR", "zip": "97132", "phone": "503-555-0120",
     "email": "sam.whitfield@example.com", "joined_on": "2016-05-19", "branch": "001"},
]

# balance_available / balance_ledger are decimal strings -> converted to cents.
SHARES = [
    # 100101 - happy path: savings avail==ledger; checking avail != ledger.
    {"share_id": "SHR-100101-0000", "member_no": "100101", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "12,845.50", "balance_ledger": "12,845.50",
     "status": "OPEN", "opened_on": "2015-03-12"},
    {"share_id": "SHR-100101-0070", "member_no": "100101", "suffix": "0070",
     "share_type": "CHECKING", "description": "Free Checking",
     "balance_available": "412.09", "balance_ledger": "460.09",
     "status": "OPEN", "opened_on": "2016-01-10"},

    # 100102 - NO_SAVINGS_ACCOUNT: checking only.
    {"share_id": "SHR-100102-0070", "member_no": "100102", "suffix": "0070",
     "share_type": "CHECKING", "description": "Free Checking",
     "balance_available": "1,203.44", "balance_ledger": "1,203.44",
     "status": "OPEN", "opened_on": "2018-07-01"},

    # 100103 - CARD_ALREADY_LOCKED.
    {"share_id": "SHR-100103-0000", "member_no": "100103", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "640.00", "balance_ledger": "640.00",
     "status": "OPEN", "opened_on": "2016-11-23"},

    # 100104 - MEMBER_INACTIVE.
    {"share_id": "SHR-100104-0000", "member_no": "100104", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "15.00", "balance_ledger": "15.00",
     "status": "OPEN", "opened_on": "2012-01-09"},

    # 100105 - DUPLICATE_SHARE_TYPE (already has a savings).
    {"share_id": "SHR-100105-0000", "member_no": "100105", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "3,100.00", "balance_ledger": "3,100.00",
     "status": "OPEN", "opened_on": "2019-05-14"},

    # 100106 / 100107 - ambiguous last-name search (two Frank Millers).
    {"share_id": "SHR-100106-0000", "member_no": "100106", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "220.10", "balance_ledger": "220.10",
     "status": "OPEN", "opened_on": "2014-02-28"},
    {"share_id": "SHR-100107-0000", "member_no": "100107", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "9,410.75", "balance_ledger": "9,410.75",
     "status": "OPEN", "opened_on": "2017-09-30"},

    # 100108 - PERMISSION_DENIED (member RESTRICTED).
    {"share_id": "SHR-100108-0000", "member_no": "100108", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "55.20", "balance_ledger": "55.20",
     "status": "OPEN", "opened_on": "2013-06-18"},

    # 100109 - NO_CARD_ON_FILE.
    {"share_id": "SHR-100109-0000", "member_no": "100109", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "1,800.00", "balance_ledger": "1,800.00",
     "status": "OPEN", "opened_on": "2020-10-05"},

    # 100110 - large number for comma formatting.
    {"share_id": "SHR-100110-0000", "member_no": "100110", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "74,209.99", "balance_ledger": "74,209.99",
     "status": "OPEN", "opened_on": "2011-04-22"},

    # 100111 - zero balance must render "0.00".
    {"share_id": "SHR-100111-0000", "member_no": "100111", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "0.00", "balance_ledger": "0.00",
     "status": "OPEN", "opened_on": "2021-01-15"},

    # 100112 - MULTIPLE_CARDS_AMBIGUOUS; also a money-market suffix.
    {"share_id": "SHR-100112-0000", "member_no": "100112", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "501.25", "balance_ledger": "501.25",
     "status": "OPEN", "opened_on": "2016-08-08"},
    {"share_id": "SHR-100112-0110", "member_no": "100112", "suffix": "0110",
     "share_type": "MONEY_MARKET", "description": "Money Market",
     "balance_available": "20,000.00", "balance_ledger": "20,000.00",
     "status": "OPEN", "opened_on": "2019-02-20"},

    # 100113 - apostrophe in name; expired card.
    {"share_id": "SHR-100113-0000", "member_no": "100113", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "312.00", "balance_ledger": "312.00",
     "status": "OPEN", "opened_on": "2018-03-17"},

    # 100114 - ordinary, savings + checking.
    {"share_id": "SHR-100114-0000", "member_no": "100114", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "8,750.00", "balance_ledger": "8,750.00",
     "status": "OPEN", "opened_on": "2015-12-01"},
    {"share_id": "SHR-100114-0070", "member_no": "100114", "suffix": "0070",
     "share_type": "CHECKING", "description": "Free Checking",
     "balance_available": "1,020.00", "balance_ledger": "1,020.00",
     "status": "OPEN", "opened_on": "2017-05-05"},

    # 100115 - non-ASCII name.
    {"share_id": "SHR-100115-0000", "member_no": "100115", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "2,405.60", "balance_ledger": "2,405.60",
     "status": "OPEN", "opened_on": "2019-11-11"},

    # 100116 - ordinary.
    {"share_id": "SHR-100116-0000", "member_no": "100116", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "999.99", "balance_ledger": "999.99",
     "status": "OPEN", "opened_on": "2017-06-25"},

    # 100117 - multiple share types (savings + certificate).
    {"share_id": "SHR-100117-0000", "member_no": "100117", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "45,000.00", "balance_ledger": "45,000.00",
     "status": "OPEN", "opened_on": "2013-09-14"},
    {"share_id": "SHR-100117-0120", "member_no": "100117", "suffix": "0120",
     "share_type": "CERTIFICATE", "description": "Share Certificate",
     "balance_available": "25,000.00", "balance_ledger": "25,000.00",
     "status": "OPEN", "opened_on": "2020-03-01"},

    # 100118 - ordinary.
    {"share_id": "SHR-100118-0000", "member_no": "100118", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "1,111.11", "balance_ledger": "1,111.11",
     "status": "OPEN", "opened_on": "2022-02-20"},

    # 100119 - FROZEN checking must not be treated as open.
    {"share_id": "SHR-100119-0000", "member_no": "100119", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "67.40", "balance_ledger": "67.40",
     "status": "OPEN", "opened_on": "2014-07-07"},
    {"share_id": "SHR-100119-0070", "member_no": "100119", "suffix": "0070",
     "share_type": "CHECKING", "description": "Free Checking",
     "balance_available": "300.00", "balance_ledger": "300.00",
     "status": "FROZEN", "opened_on": "2015-08-08"},

    # 100120 - ordinary.
    {"share_id": "SHR-100120-0000", "member_no": "100120", "suffix": "0000",
     "share_type": "PRIMARY_SAVINGS", "description": "Regular Share",
     "balance_available": "5,600.00", "balance_ledger": "5,600.00",
     "status": "OPEN", "opened_on": "2016-05-19"},
]

CARDS = [
    {"card_id": "CRD-100101-1", "member_no": "100101", "last4": "4417",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100101-0070", "issued_on": "2015-04-01"},

    {"card_id": "CRD-100102-1", "member_no": "100102", "last4": "8802",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100102-0070", "issued_on": "2018-07-15"},

    {"card_id": "CRD-100103-1", "member_no": "100103", "last4": "3391",
     "network": "VISA_DEBIT", "status": "LOCKED",
     "linked_share_id": "SHR-100103-0000", "issued_on": "2017-01-05"},

    # 100104 - none

    {"card_id": "CRD-100105-1", "member_no": "100105", "last4": "7745",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100105-0000", "issued_on": "2019-06-01"},

    {"card_id": "CRD-100106-1", "member_no": "100106", "last4": "1120",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100106-0000", "issued_on": "2014-03-15"},

    # 100107 - none

    {"card_id": "CRD-100108-1", "member_no": "100108", "last4": "6634",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100108-0000", "issued_on": "2013-07-01"},

    # 100109 - none

    {"card_id": "CRD-100110-1", "member_no": "100110", "last4": "5528",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100110-0000", "issued_on": "2011-05-01"},

    {"card_id": "CRD-100111-1", "member_no": "100111", "last4": "2210",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100111-0000", "issued_on": "2021-02-01"},

    # 100112 - MULTIPLE_CARDS_AMBIGUOUS (debit + credit).
    {"card_id": "CRD-100112-1", "member_no": "100112", "last4": "9087",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100112-0000", "issued_on": "2016-09-01"},
    {"card_id": "CRD-100112-2", "member_no": "100112", "last4": "4451",
     "network": "VISA_CREDIT", "status": "ACTIVE",
     "linked_share_id": None, "issued_on": "2018-03-01"},

    # 100113 - expired card.
    {"card_id": "CRD-100113-1", "member_no": "100113", "last4": "3345",
     "network": "VISA_DEBIT", "status": "EXPIRED",
     "linked_share_id": "SHR-100113-0000", "issued_on": "2018-04-01"},

    {"card_id": "CRD-100114-1", "member_no": "100114", "last4": "7781",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100114-0070", "issued_on": "2015-12-15"},

    {"card_id": "CRD-100115-1", "member_no": "100115", "last4": "6690",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100115-0000", "issued_on": "2019-12-01"},

    {"card_id": "CRD-100116-1", "member_no": "100116", "last4": "1194",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100116-0000", "issued_on": "2017-07-01"},

    {"card_id": "CRD-100117-1", "member_no": "100117", "last4": "8823",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100117-0000", "issued_on": "2013-10-01"},

    {"card_id": "CRD-100118-1", "member_no": "100118", "last4": "5017",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100118-0000", "issued_on": "2022-03-01"},

    {"card_id": "CRD-100119-1", "member_no": "100119", "last4": "3902",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100119-0000", "issued_on": "2014-08-01"},

    {"card_id": "CRD-100120-1", "member_no": "100120", "last4": "2264",
     "network": "VISA_DEBIT", "status": "ACTIVE",
     "linked_share_id": "SHR-100120-0000", "issued_on": "2016-06-01"},
]

# amount is a decimal string; a leading '-' marks a debit -> negative cents.
TXNS = [
    # --- 100101 checking (SHR-100101-0070): 10 rows over the last 90 days ---
    {"txn_id": "TXN-00001", "share_id": "SHR-100101-0070", "posted_on": "2026-06-15",
     "description": "ACH DEPOSIT - PAYROLL", "amount": "2,450.00", "txn_type": "ACH"},
    {"txn_id": "TXN-00002", "share_id": "SHR-100101-0070", "posted_on": "2026-06-18",
     "description": "POS PURCHASE - CORNER MARKET", "amount": "-54.20", "txn_type": "POS"},
    {"txn_id": "TXN-00003", "share_id": "SHR-100101-0070", "posted_on": "2026-06-25",
     "description": "POS PURCHASE - GAS N GO", "amount": "-38.75", "txn_type": "POS"},
    {"txn_id": "TXN-00004", "share_id": "SHR-100101-0070", "posted_on": "2026-07-01",
     "description": "ATM WITHDRAWAL - BRANCH 001", "amount": "-200.00", "txn_type": "WITHDRAWAL"},
    {"txn_id": "TXN-00005", "share_id": "SHR-100101-0070", "posted_on": "2026-07-02",
     "description": "ACH DEPOSIT - PAYROLL", "amount": "2,450.00", "txn_type": "ACH"},
    {"txn_id": "TXN-00006", "share_id": "SHR-100101-0070", "posted_on": "2026-07-15",
     "description": "POS PURCHASE - BOOKSTORE", "amount": "-22.99", "txn_type": "POS"},
    {"txn_id": "TXN-00007", "share_id": "SHR-100101-0070", "posted_on": "2026-07-28",
     "description": "MONTHLY DIVIDEND", "amount": "1.85", "txn_type": "DIVIDEND"},
    {"txn_id": "TXN-00008", "share_id": "SHR-100101-0070", "posted_on": "2026-08-01",
     "description": "ACH DEPOSIT - PAYROLL", "amount": "2,450.00", "txn_type": "ACH"},
    {"txn_id": "TXN-00009", "share_id": "SHR-100101-0070", "posted_on": "2026-08-12",
     "description": "POS PURCHASE - CORNER MARKET", "amount": "-61.40", "txn_type": "POS"},
    {"txn_id": "TXN-00010", "share_id": "SHR-100101-0070", "posted_on": "2026-08-20",
     "description": "TRANSFER TO SAVINGS", "amount": "-300.00", "txn_type": "TRANSFER"},

    # --- 100110 savings (SHR-100110-0000): 5 rows ---
    {"txn_id": "TXN-00011", "share_id": "SHR-100110-0000", "posted_on": "2026-06-20",
     "description": "MONTHLY DIVIDEND", "amount": "52.10", "txn_type": "DIVIDEND"},
    {"txn_id": "TXN-00012", "share_id": "SHR-100110-0000", "posted_on": "2026-07-05",
     "description": "ACH DEPOSIT - PAYROLL", "amount": "3,000.00", "txn_type": "ACH"},
    {"txn_id": "TXN-00013", "share_id": "SHR-100110-0000", "posted_on": "2026-07-20",
     "description": "MONTHLY DIVIDEND", "amount": "55.00", "txn_type": "DIVIDEND"},
    {"txn_id": "TXN-00014", "share_id": "SHR-100110-0000", "posted_on": "2026-08-10",
     "description": "ATM WITHDRAWAL - BRANCH 001", "amount": "-400.00", "txn_type": "WITHDRAWAL"},
    {"txn_id": "TXN-00015", "share_id": "SHR-100110-0000", "posted_on": "2026-08-25",
     "description": "ACH DEPOSIT - PAYROLL", "amount": "3,000.00", "txn_type": "ACH"},
]

# One audit row carries the redaction canary. This string must never be logged.
AUDIT_SEED = [
    {"ts": "2024-01-01T00:00:00", "staff_username": "mreyes", "actor": "HUMAN",
     "action": "SEED_MARKER", "member_no": "100101", "reference": None,
     "detail": "CANARY-A7F3-DONOTLOG"},
]
