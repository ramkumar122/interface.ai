"""CoreDesk tenant configuration.

The same codebase serves two credit unions. The TENANT environment variable
selects which one, resolved per call via get_cfg() so nothing caches a stale
reference. Everything tenant-specific lives here as a plain dict of dicts --
templates read these values and hardcode nothing.
"""

import os

# ---------------------------------------------------------------------------
# Per-tenant configuration
#
# results_columns / shares_columns are ordered lists of {header, field[, money]}.
# The header text and its ORDER are tenant-visible and differ on purpose (Summit
# reorders BRANCH before JOINED, and renames the shares balance headers), which
# is what forces a locator to read a cell by column-header name, not position.
# ---------------------------------------------------------------------------
CONFIG = {
    "riverbend": {
        "port": 8001,
        "institution_name": "Riverbend Credit Union",
        "product_banner": "CoreDesk 4.2",
        "accent_color": "#1B3A5C",  # navy
        "label_member_no": "Member Number",
        "label_shares": "Shares",
        "btn_continue": "Continue",
        "link_select": "SEL",
        "menu_codes": ["MBRINQ", "CRDMNT", "ADRCHG", "SHROPN", "TXNHST", "SIGNOFF"],
        "results_columns": [
            {"header": "MBR NO", "field": "member_no"},
            {"header": "NAME", "field": "name"},
            {"header": "STATUS", "field": "status"},
            {"header": "JOINED", "field": "joined"},
            {"header": "BRANCH", "field": "branch"},
        ],
        "shares_columns": [
            {"header": "SUFFIX", "field": "suffix"},
            {"header": "TYPE", "field": "type"},
            {"header": "DESCRIPTION", "field": "description"},
            {"header": "AVAILABLE", "field": "available", "money": True},
            {"header": "LEDGER", "field": "ledger", "money": True},
            {"header": "STATUS", "field": "status"},
        ],
    },
    "summit": {
        "port": 8002,
        "institution_name": "Summit Federal Credit Union",
        "product_banner": "CoreDesk 4.2",
        "accent_color": "#6B1F2E",  # maroon
        "label_member_no": "Member ID",
        "label_shares": "Savings Accounts",
        "btn_continue": "Next",
        "link_select": "VIEW",
        "menu_codes": ["INQ01", "CRD01", "ADR01", "SHR01", "TXN01", "EXIT"],
        "results_columns": [
            {"header": "MBR NO", "field": "member_no"},
            {"header": "NAME", "field": "name"},
            {"header": "STATUS", "field": "status"},
            {"header": "BRANCH", "field": "branch"},
            {"header": "JOINED", "field": "joined"},
        ],
        "shares_columns": [
            {"header": "ACCT", "field": "suffix"},
            {"header": "TYPE", "field": "type"},
            {"header": "DESCRIPTION", "field": "description"},
            {"header": "AVAIL BAL", "field": "available", "money": True},
            {"header": "LEDGER BAL", "field": "ledger", "money": True},
            {"header": "STATUS", "field": "status"},
        ],
    },
}

# ---------------------------------------------------------------------------
# Menu functions
#
# The menu codes differ per tenant but the underlying functions are identical,
# so MENU_FUNCTIONS lists them positionally (Nth code -> Nth function). The
# member-centric functions carry a canonical `fn` token and a `member_action`
# suffix: selecting them lands on Member Inquiry (member selection first), and
# the results action link then targets that sub-function for the chosen member.
# ---------------------------------------------------------------------------
MENU_FUNCTIONS = [
    {"description": "Member Inquiry", "route": "/mbrinq", "fn": None, "member_action": None},
    {"description": "Card Maintenance", "route": "/mbrinq?fn=CARD", "fn": "CARD", "member_action": "cards"},
    {"description": "Address Change", "route": "/mbrinq?fn=ADDR", "fn": "ADDR", "member_action": "address"},
    {"description": "Open Share Account", "route": "/mbrinq?fn=SHARE_NEW", "fn": "SHARE_NEW", "member_action": "shares/new"},
    {"description": "Transaction History", "route": "/mbrinq?fn=TXN", "fn": "TXN", "member_action": "transactions"},
    {"description": "Sign Off", "route": "/signoff", "fn": None, "member_action": None},
]


def get_tenant_key():
    """Resolve the active tenant key from the environment (default riverbend)."""
    key = os.environ.get("TENANT", "riverbend")
    return key if key in CONFIG else "riverbend"


def get_cfg():
    """Return the active tenant's config dict, resolved per call."""
    return CONFIG[get_tenant_key()]


def get_tenant(tenant_key):
    """Return the config dict for a specific tenant, defaulting to riverbend."""
    return CONFIG.get(tenant_key, CONFIG["riverbend"])


def menu_items_for(tenant_key):
    """Build the ordered menu rows (code + description + route) for a tenant."""
    cfg = get_tenant(tenant_key)
    items = []
    for code, fn in zip(cfg["menu_codes"], MENU_FUNCTIONS):
        items.append(
            {
                "code": code,
                "description": fn["description"],
                "route": fn["route"],
            }
        )
    return items


def route_for_code(tenant_key, code):
    """Resolve a fast-path function code to a route (case-insensitive, trimmed).

    Returns None if the code is not part of this tenant's menu.
    """
    if code is None:
        return None
    normalized = code.strip().upper()
    if not normalized:
        return None
    for item in menu_items_for(tenant_key):
        if item["code"].upper() == normalized:
            return item["route"]
    return None


def fn_function(token):
    """Return the MENU_FUNCTIONS entry for a canonical fn token, or None.

    Absent or unrecognized tokens return None so callers treat them as "no
    function context" rather than erroring.
    """
    if not token:
        return None
    for fn in MENU_FUNCTIONS:
        if fn["fn"] == token:
            return fn
    return None
