# The Biks Workflow Library (as actually deployed)

Compiled 17 Jul 2026 from every repo in Biks-AI-dev. This is what the Forge clones. Each workflow is production code where a new client is a config change, not a rebuild. The onboarding bot's four routing categories map to these.

## The seven workflows

| # | Workflow | Proven at | Stack / channel | The clone seam (what changes per client) |
|---|---|---|---|---|
| 1 | **Sales & Order** — catalog Q&A → cart → address → shipping rates → invoice → QRIS → payment-proof OCR → admin approve → booked | Rosalie (Cheese Studio), Sentuh Rasa (risol) | Python FastAPI + DeepSeek tools, Redis/Postgres, **WhatsApp Cloud API**, Biteship | `products.json` (catalog + store identity) + `_SYSTEM_BASE` persona string + `.env` |
| 2 | **Reconciliation** — omzet vs bank mutasi, fee/MDR decomposition, in-transit clearing, render to the client's own sheet | reconciliation bot (F&B group pilot) | Hermes + MCP + SQLite → Google Sheets, Baileys | `bot.env` + outlet map + column maps + SOUL/AGENTS md |
| 3 | **Stock / inventory** — stage → confirm → commit movements, FIFO order fulfillment, BQ tracking, sheet render | stock bot (construction supplier) | Hermes + MCP + SQLite → Sheets, Baileys | `catalog-data.json` (177 SKUs) + `bot.env` + SOUL.md |
| 4 | **Loyalty** — join FSM, points math, receipt OCR, redemption holds, staff flows | Kopi Konnichiwa | Bun + SQLite, **WhatsApp Cloud API**, LLM-voiced deterministic state machine | SQLite `config` table (earn rate, rewards, staff, FAQ) — hot reload, no redeploy |
| 5 | **Finance AP/AR** — invoice extraction, transfer matching, books, e-mail + reminders | finance agent (SG entity) | Claude Code agent + Express gateway, **WhatsApp Cloud API**, AgentOS | `CLAUDE.md` (persona + entity + IDs) + `.env` |
| 6 | **Reimbursement → journal** — expense export → routed GL journal → Accurate import | legal-firm group (3 entities) | Static browser app + Python stdlib backend | one `ENTITY_OVERRIDES` config block per entity |
| 7 | **Personal exec agent** — OCR to sheet, briefs, reminders, memory | RAPI (3 VIP tenants) | Hermes per-tenant instances, Baileys, env-locked shims | SOUL.md + AGENTS.md + tenant env |

Intake-bot routing map: Customer Service → #1 · Finance Assistant → #2/#5/#6 · Project Management → #3 (nearest) · RAPI → #7.

## The invariant DNA (same in every repo — the Forge must preserve it)

1. **LLM understands and speaks; code decides.** Every write (money, points, stock, orders) is deterministic. kopi even validates that the LLM's rephrasing preserves every number token verbatim.
2. **Persona/domain split**: SOUL (who the bot is) vs AGENTS/config (facts, IDs, rules). In biks-forge it's `_SYSTEM_BASE` + `products.json`.
3. **Truth in a durable store, client's sheet is a render.** Never restructure the client's template.
4. **Stage → confirm → commit.** Nothing books without an explicit yes from the right person.
5. **Green/amber/red; only exceptions reach humans.** Never force a gap to zero.
6. **Channel truth: the official WhatsApp Cloud API is the production norm** (rosalie, sentuh-rasa, kopi, airwallex — one WABA can carry many phone numbers with per-number webhook isolation). Baileys/SIM survives only for Hermes personal/group bots. **Promotion of a forged agent to WhatsApp = a Cloud API phone number config, not a SIM purchase.**

## What this means for the Forge demo

The forged sandbox agent is a **mini Workflow #1** (Sales & Order), because it's the flagship with two live clients and every SME understands ordering. Critically: the ForgeSpec's catalog section uses **the real `products.json` schema from biks-forge**, and the persona section maps to `_SYSTEM_BASE`. So the demo's closing line is literal: *"this exact config file drops into our production platform"* — the same file format that runs Rosalie today. The manual 8-step onboarding in `ROSALIE_MIGRATION_PLAN.md` keeps steps 1, 6, 7, 8 (copy skeleton, CI, Caddy, dashboard) as founder work; the Forge's Architect auto-produces steps 2, 3, 5 (products.json, persona, prefilled .env).
