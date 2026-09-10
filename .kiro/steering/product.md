# Product Context

## What this repo is

A take-home submission for interface.ai. It contains two separate programs:

1. **CoreDesk** — a *mock* legacy credit-union servicing web app. It is the target
   application, not the deliverable. It exists to be automated.
2. **The automation system** — an LLM-driven computer-use agent that discovers how
   to complete a goal in CoreDesk, records it as a versioned capability artifact,
   and replays that artifact deterministically with no LLM in the decision loop.

## The through-line

> The model discovers. The artifact becomes a reusable capability.
> Deterministic replay is how the AI agent invokes it in production.

## Why CoreDesk looks bad on purpose

interface.ai builds AI agents for banks and credit unions. They already integrate
with the major cores (Symitar, Corelation, Fiserv, Jack Henry) through APIs. This
project targets the *long tail*: vendor servicing screens with **no API at all**,
where the only way in is to drive the UI as a human operator would.

So CoreDesk must imitate enterprise software written around 2006 and patched since.
Making it modern, clean or pretty defeats the entire purpose. If a change would make
CoreDesk nicer to use or easier to automate, that change is wrong.

## Two tenants

CoreDesk 4.2 is a fictional product from a fictional vendor, "Meridian Core Systems".
Two fictional institutions run it, configured differently:

- **Riverbend Credit Union** (`riverbend`, port 8001, navy)
- **Summit Federal Credit Union** (`summit`, port 8002, maroon)

Same code, same database, different labels, button text, column headers and menu
codes. This models the real situation where hundreds of institutions run the same
vendor product configured differently.

## The four capabilities being automated

| Capability | Risk class | Notes |
|---|---|---|
| Read a member's savings balance | `safe` | Read-only. The discovery demo. |
| Lock a debit card | `guarded_write` | Reversible, so the agent may complete it. |
| Update a mailing address | `guarded_write` | Multi-field form with validation. |
| Prepare a new share account | `irreversible` | Agent reaches the review screen and **stops**. |

## Non-goals

- Real security hardening of CoreDesk. It is a stand-in, not a product.
- A real-time co-browsing operator console. Deliberately mocked.
- Desktop or native-app support. Designed at the seam, not built.
- Queues, clusters, or multi-tenant infrastructure. Explicitly not rewarded.
