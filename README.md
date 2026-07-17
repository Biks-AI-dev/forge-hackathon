# Biks Forge

A client meeting goes in. A working AI agent comes out, running in its own isolated sandbox, testable at a link 90 seconds later.

Built in one day at the Daytona × Kimi × Nosana hackathon (Singapore) by the Biks team. Biks builds AI agents that run daily operations (orders, invoices, payments) inside WhatsApp for Southeast Asian businesses.

## The flow

```
meeting audio + client files ──▶ transcribe ──▶ ForgeSpec JSON ──▶ Daytona sandbox ──▶ agent (Kimi brain) ──▶ chat URL
                                                       └──▶ auto-PRD draft
```

Two front doors, one factory: Biks' production WhatsApp intake bot produces the same ForgeSpec self-serve; this repo adds Meeting Mode (record the client meeting, upload, confirm, forge) and the Forge itself.

## Read first

`PRD.md` — the full technical spec: components, the ForgeSpec contract, guardrails-in-code, day plan, acceptance criteria.

## Rules

- Keys in `.env` (see `.env.example`), never in git.
- Guardrails are code, not prompts: totals recomputed deterministically, payments never confirmed by the model, off-catalogue requests escalate to the owner.
- `test-data/Order Juni 2026.xlsx` is fabricated demo data for the "Sari's Catering" persona.
