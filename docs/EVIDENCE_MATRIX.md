# Evidence Matrix

The plan the evidence runs will be produced against. The runs themselves don't
exist yet; this table exists so the system is built able to demonstrate every
case. Injection column uses the `runtime-conditions` names (header
`X-CoreDesk-Inject` or `/admin/inject`).

## Capability happy/business-outcome runs

| # | Capability | Fixture | Injection | Expected result status |
|---|---|---|---|---|
| 1 | `coredesk.member.read_savings_balance` | 100101 | none | OK — `12,845.50` |
| 2 | `coredesk.member.read_savings_balance` | 100102 | none | `NO_SAVINGS_ACCOUNT` |
| 3 | `coredesk.member.read_savings_balance` | 999999 | none | `MEMBER_NOT_FOUND` |
| 4 | `coredesk.member.read_savings_balance` | 100106/100107 (Miller) | none | `AMBIGUOUS_MATCH` |
| 5 | `coredesk.member.read_savings_balance` | 100119 | none | OK — returns OPEN savings, not the FROZEN checking |
| 6 | `coredesk.card.lock` | 100101 | none | OK — `CARD_LOCK`, `CRD-` ref |
| 7 | `coredesk.card.lock` | 100103 | none | `CARD_ALREADY_LOCKED` (no audit) |
| 8 | `coredesk.card.lock` | 100109 | none | `NO_CARD_ON_FILE` |
| 9 | `coredesk.card.lock` | 100112 | none | `MULTIPLE_CARDS_AMBIGUOUS` |
| 10 | `coredesk.card.lock` | 100113 | none | `CARD_EXPIRED` (immutable) |
| 11 | `coredesk.card.lock` | 100108 | none | `PERMISSION_DENIED` (RESTRICTED) |
| 12 | `coredesk.address.update` | 100101 | none | OK — `ADDRESS_UPDATE`, `ADR-` ref |
| 13 | `coredesk.address.update` | 100101 (bad ZIP) | none | `VALIDATION_ERROR` |
| 14 | `coredesk.share.prepare_open` | 100101 | none | `STOPPED_AT_REVIEW` (agent halts; no `SHARE_OPEN`) |
| 15 | `coredesk.share.prepare_open` | 100104 | none | `MEMBER_INACTIVE` |
| 16 | `coredesk.share.prepare_open` | 100105 | none | `DUPLICATE_SHARE_TYPE` |

## Error-taxonomy runs (injection-driven)

Run against a happy-path capability (e.g. #1 on 100101) with the injection active.

| # | Injection | Expected classification |
|---|---|---|
| 17 | `session_expired` | Recoverable → **escalate to human** |
| 18 | `maintenance_notice` | Recoverable → **auto-dismiss** (max 1 attempt) |
| 19 | `slow` | Recoverable → **wait / retry** |
| 20 | `app_error` | **Hard failure** (`ERR-7731`) |
| 21 | `db_timeout` | **Retry once, then hard failure** |
| 22 | `readonly_role` | Business outcome `PERMISSION_DENIED` |

Notes: `/admin/inject` is off the agent allowlist (excluded by `/admin/` prefix);
the agent can never disable a condition it is being tested against.
