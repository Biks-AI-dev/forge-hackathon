# PRD · Biks Forge (hackathon build)

**Audience:** the 2 devs joining tomorrow + any agent session helping them. Assumes zero prior Biks context. Read this top to bottom, then skim the two files listed in "Read next".

---

## 1. Context in 60 seconds

Biks builds AI agents that run business operations (orders, invoices, payments) inside WhatsApp for Southeast Asian SMEs. **Already live in production:** a WhatsApp intake bot. A prospect messages it, it interviews them one question at a time, reads their Excel/PDF files, researches their business on the web, and produces a structured spec of their business plus a blueprint PDF and a static mockup page. It runs on our VPS as a systemd service; you will not touch it.

**The gap, part 1:** after the spec exists, nothing runnable exists until a human buys a SIM and pairs a WhatsApp number. The client must commit before ever experiencing their agent.

**The gap, part 2 (the one our CEO lives daily):** most real deals don't start in the WhatsApp bot. They start in a meeting. Nura sits with the client, takes notes, collects files, then manually turns it all into a PRD for engineering. The client leaves that meeting with nothing but promises. Reference points: foaster.ai takes ~12 days from interviews to a roadmap *document*; pit.com delivers via consultation. Nobody puts a working product in the client's hands during the first conversation. We will.

**Tomorrow we build the Forge with two front doors:** a spec goes in (from the WhatsApp bot OR from a recorded client meeting), a real agent comes out, running in an isolated Daytona sandbox, testable at a web link 90 seconds later. SIM comes after the client says yes, not before.

## 2. What we build (and only this)

```
Door A (live today):  WhatsApp intake ─┐
                                       ├─▶ ForgeSpec JSON ─▶ Provisioner ─▶ Daytona sandbox ─▶ agent (Kimi) ─▶ chat URL
Door B (we build):    meeting audio ───┘                                                    └─▶ auto-PRD draft
```

Four components:

1. **Meeting Mode (Door B)** — an upload page: meeting audio file + the client's files (Excel/PDF) + optional notes. Pipeline: transcribe the audio (Nosana whisper job; local whisper fallback) → one Kimi call extracts the ForgeSpec from the transcript + files → show the spec on a confirm screen (editable JSON is fine) → forge on confirm. This is the primary stage demo AND the tool our CEO uses at every client meeting next week.
2. **Provisioner** — a small service: `POST /forge` with a ForgeSpec, creates a Daytona sandbox, injects the agent template + spec, starts the server, returns `{ chat_url, sandbox_id }`. Runs anywhere (laptop is fine for the demo).
3. **Sandbox agent** — a single Node (or Python) server that lives INSIDE the sandbox: loads the ForgeSpec, serves `POST /chat` backed by Kimi, serves the chat page at `/`. Guardrails are code, not prompt (section 5).
4. **Chat page** — one static HTML file, mobile-friendly, WhatsApp-ish bubbles, Biks teal `#0F766E`. No framework needed.

Plus one cheap, high-value byproduct: **auto-PRD** — the same ForgeSpec rendered through a markdown template into a build-ready PRD draft (one brain call, or even pure templating). This automates the document our CEO currently writes by hand after every meeting, so the tool pays for itself even when a deal needs a custom build instead of a forged agent.

Live audio streaming is NOT in scope; upload a finished recording. **Stretch with the best effort-to-wow ratio: the "Forge so far" button.** Mid-meeting, upload the audio recorded up to that point and forge; the client plays with the draft agent while the conversation continues; re-forge at the end with the full transcript. The provisioner already replaces the sandbox for the same business slug, so this is the existing path called twice, not new infrastructure. On stage it reads as building-during-the-call (the viral Todd Saunders demo), without streaming ASR. Other stretch: Oxylabs pre-research on the client's business. Explicitly out of scope: WhatsApp inside sandboxes, billing, auth, dashboards, multi-tenant registry, running our full production agent framework inside the sandbox.

## 3. The contract: ForgeSpec

The intake bot already emits a 26-field blueprint JSON. For the Forge we use a trimmed, agent-shaped spec. A mapper from blueprint → ForgeSpec is ~30 lines and can be written last; for most of the day, work from the hand-written sample below.

```json
{
  "business_name": "Sari's Catering",
  "industry": "catering",
  "language": "en",
  "tone": "warm, brief, professional",
  "catalogue": [
    { "name": "Nasi Box Ayam Bakar", "unit_price": 35000 },
    { "name": "Tumpeng Mini",        "unit_price": 150000 }
  ],
  "currency": "IDR",
  "workflows": ["take_order", "check_payment", "daily_recap"],
  "owner_name": "Bu Sari",
  "guardrails": [
    "never invent a price — catalogue only",
    "never confirm an unverified payment",
    "escalate anything unusual to the owner"
  ]
}
```

Test data: `test-data/Order Juni 2026.xlsx` in this repo is a realistic (fabricated) 366-row order log for this exact persona.

## 4. Component specs

### 4.1 Provisioner

- Input validation: reject a spec missing `business_name` or a non-empty `catalogue`.
- Daytona via the official SDK (TypeScript or Python). Exact calls confirmed at the morning workshop; the shape is: create sandbox → upload/write agent files → start server process → get the preview URL for the server port.
- Slug the business name for the URL. Keep a tiny in-memory map `{slug → sandbox_id}` so re-forging the same business replaces its sandbox.
- Target: request → returned chat URL in under 90 s. Measure it; the number is a demo line.

### 4.2 Sandbox agent

- One file if possible. Reads `spec.json` from disk.
- System prompt assembled FROM the spec (persona, tone, catalogue as a price list, workflows). Keep it under a page.
- Kimi is the brain: OpenAI-compatible chat completions endpoint (key + exact base URL from the sponsor booth; put both in env, never hardcode).
- Conversation state in memory per browser session id. No DB.

### 4.3 Guardrails, in code (this is the judged differentiator, do not skip)

1. **Prices:** when the model mentions a catalogue item, the server recomputes `qty × unit_price` from the spec and overwrites any model-claimed total before sending. (Production already does this pattern in `deploy/standalone-bot/index.mjs`, function `validateMockupCfg` — copy the approach.)
2. **Payments:** the agent never outputs "payment confirmed". Server-side filter: if a user claims payment, the reply path appends "I've noted your transfer — Bu Sari will verify and confirm." Always.
3. **Escalation:** off-catalogue requests, discounts, complaints → a fixed "let me check with the owner" reply plus an `escalations` log line.

### 4.4 Chat page

- Bubbles, typing indicator, business name in the header bar. One HTML file, inline CSS/JS, fetch to `/chat`. Skip auth for the demo; sandbox URLs are unguessable enough for a weekend.

## 5. Day plan and split (2 devs + Adith)

| When | Dev A | Dev B | Adith |
|---|---|---|---|
| Workshop | Daytona SDK hello-world: sandbox → server → preview URL. Everyone does this once. | | |
| Morning | Provisioner: spec → sandbox → URL | Sandbox agent + Kimi + guardrails | Chat page, ForgeSpec samples, keys from sponsor booths |
| **Midday gate 1** | **Bu Sari spec (hand-written JSON) → live chat link, end to end. Nothing else matters until this passes.** | | |
| Afternoon | Meeting Mode: upload → transcribe → spec extract → confirm screen | Guardrail polish; latency measurement; auto-PRD template | Record the fake "client meeting" audio for the demo; pitch; backup screen recording |
| **Gate 2** | **Meeting recording → confirmed spec → forged agent, end to end. This is the stage demo.** | | |
| If time | 3-sandbox parallel moment | Oxylabs pre-research beat | Pitch rehearsal twice |

## 6. Acceptance criteria

- [ ] `POST /forge` with the Bu Sari spec returns a working chat URL in < 90 s (measure, record the number)
- [ ] A ~2-minute meeting recording + one Excel produces a correct, confirmable ForgeSpec (business name, ≥2 catalogue items with right prices)
- [ ] The auto-PRD draft renders from the same spec
- [ ] The agent quotes ONLY catalogue prices; an order of 2 Tumpeng Mini totals exactly Rp 300.000 in the reply
- [ ] "I've transferred the money" never yields a confirmation, always the owner-verification line
- [ ] An off-menu request yields the escalation line
- [ ] Three sandboxes for three different specs run simultaneously (stretch)
- [ ] Backup screen recording of the full arc exists before final pitches

## 7. Rules (non-negotiable)

1. **Do not touch `deploy/standalone-bot/` or anything on the VPS.** The intake bot is production. You consume its spec format; you never edit it.
2. **Keys live in `.env` files, never in git.** This repo's history is clean; keep it that way.
3. **Build everything inside THIS repo**, self-contained. It has clean history and can be flipped public for submission; the Biks private repo never can.
4. Customer-facing text never names vendors or the stack. The phrase, if needed: "agentic infrastructure with per-client isolation."

## 8. Read next

1. `forge-idea.md`, `deploy/standalone-bot/index.mjs` (`validateMockupCfg` = the guardrail pattern to copy), and `DEMO.md` — all in the Biks private repo; ask Adith for access if you don't have it

## 9. Env you will need tomorrow

```
DAYTONA_API_KEY=      # sponsor booth / workshop
KIMI_API_KEY=         # sponsor booth; note the base URL they give you
KIMI_BASE_URL=
NOSANA_...=           # stretch only
OXYLABS_...=          # stretch only
```
