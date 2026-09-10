# Fixture Contract

The seed data is a contract, not sample data. Every member exists to trigger a
named condition that a later evidence run depends on. **Never change a member's
shape to make a test pass.** If a test fails against these fixtures, the code is
wrong, not the fixture.

## Members

| member_no | Name | Status | Shares | Cards | Exists to trigger |
|---|---|---|---|---|---|
| 100101 | Alice Nakamura | ACTIVE | 0000 SAVINGS 12,845.50; 0070 CHECKING avail 412.09 / ledger 460.09 | VISA_DEBIT 4417 ACTIVE | Happy path, all four capabilities |
| 100102 | Ben Ortiz | ACTIVE | 0070 CHECKING only | VISA_DEBIT 8802 ACTIVE | `NO_SAVINGS_ACCOUNT` |
| 100103 | Carla Reyes | ACTIVE | 0000 SAVINGS 640.00 | VISA_DEBIT 3391 **LOCKED** | `CARD_ALREADY_LOCKED` |
| 100104 | David Kim | **INACTIVE** | 0000 SAVINGS 15.00 | none | `MEMBER_INACTIVE` |
| 100105 | Elena Novak | ACTIVE | 0000 SAVINGS 3,100.00 | VISA_DEBIT 7745 ACTIVE | `DUPLICATE_SHARE_TYPE` |
| 100106 | **Frank Miller** | ACTIVE | 0000 SAVINGS 220.10 | VISA_DEBIT 1120 ACTIVE | Ambiguous name search |
| 100107 | **Frank Miller** | ACTIVE | 0000 SAVINGS 9,410.75 | none | The duplicate — different balance on purpose |
| 100108 | Grace Liu | **RESTRICTED** | 0000 SAVINGS 55.20 | VISA_DEBIT 6634 ACTIVE | `PERMISSION_DENIED` |
| 100109 | Henry Patel | ACTIVE | 0000 SAVINGS 1,800.00 | **none** | `NO_CARD_ON_FILE` |
| 100110 | Ingrid Sorensen | ACTIVE | 0000 SAVINGS 74,209.99 | VISA_DEBIT 5528 ACTIVE | Replay with different params; comma formatting |
| 100111 | Jorge Medina | ACTIVE | 0000 SAVINGS **0.00** | VISA_DEBIT 2210 ACTIVE | Zero must render "0.00", never blank |
| 100112 | Kavya Raman | ACTIVE | 0000 SAVINGS 501.25; 0110 MONEY_MARKET 20,000.00 | **two** cards: 9087 debit, 4451 credit | `MULTIPLE_CARDS_AMBIGUOUS` |
| 100113 | Liam O'Connor | ACTIVE | 0000 SAVINGS 312.00 | VISA_DEBIT 3345 EXPIRED | Apostrophe: SQL + HTML escaping |
| 100114 | Mei Zhang | ACTIVE | 0000 SAVINGS 8,750.00; 0070 CHECKING 1,020.00 | VISA_DEBIT 7781 ACTIVE | Ordinary |
| 100115 | Noah Bergström | ACTIVE | 0000 SAVINGS 2,405.60 | VISA_DEBIT 6690 ACTIVE | Non-ASCII name |
| 100116 | Olivia Fontaine | ACTIVE | 0000 SAVINGS 999.99 | VISA_DEBIT 1194 ACTIVE | Ordinary |
| 100117 | Pavel Novikov | ACTIVE | 0000 SAVINGS 45,000.00; 0120 CERTIFICATE 25,000.00 | VISA_DEBIT 8823 ACTIVE | Multiple share types |
| 100118 | Quinn Adeyemi | ACTIVE | 0000 SAVINGS 1,111.11 | VISA_DEBIT 5017 ACTIVE | Ordinary |
| 100119 | Rosa Delgado | ACTIVE | 0000 SAVINGS 67.40; 0070 CHECKING **FROZEN** 300.00 | VISA_DEBIT 3902 ACTIVE | Frozen share must not count as open |
| 100120 | Sam Whitfield | ACTIVE | 0000 SAVINGS 5,600.00 | VISA_DEBIT 2264 ACTIVE | Ordinary |

**999999 must not exist** — that is the `MEMBER_NOT_FOUND` case.

## Staff

| username | password | display | role | branch |
|---|---|---|---|---|
| mreyes | demo1234 | M. REYES | MSR | 001 |
| jtran | demo1234 | J. TRAN | TELLER_RO | 004 |
| dpark | demo1234 | D. PARK | SUPERVISOR | 001 |

## The two fixtures that carry the most weight

**The duplicate Frank Millers (100106 / 100107).** Searching by last name returns
two rows with different balances. Any capability that silently picks row one
returns a plausible but wrong answer. This forces the capability contract to take
a member number, and forces name search to return `AMBIGUOUS_MATCH` explicitly.

**Rosa Delgado's frozen checking (100119).** `get_primary_savings` must return the
OPEN savings share and must never fall through to the FROZEN one. A frozen share
that reads as available is exactly the class of silent wrong answer this whole
system is designed to prevent.

## Runtime condition injections

Driven by an `X-CoreDesk-Inject` header or the `/admin/inject` toggle page.
`/admin/inject` is **off the agent's allowlist** — the agent must never be able to
disable a condition it is being tested against.

| Injection | Behaviour | Expected classification |
|---|---|---|
| `session_expired` | 302 to `/` with "Your session has expired." | Recoverable → **escalate** |
| `maintenance_notice` | Prepend a `role="dialog"` banner with a Close button | Recoverable → auto-dismiss, max 1 attempt |
| `slow` | Sleep 8s | Recoverable → wait/retry |
| `app_error` | 500 page, "CoreDesk Application Error — Reference ERR-7731" | **Hard failure** |
| `db_timeout` | Grid renders "Unable to retrieve records. Please retry." | Retry once, then hard failure |
| `readonly_role` | Force TELLER_RO; write buttons disabled with explanation | **Business outcome** `PERMISSION_DENIED` |
