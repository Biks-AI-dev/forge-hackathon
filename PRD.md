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

## 2b. The agent roster and the Biks Method

The pipeline is presented (and built) as five specialist agents, not one blob. Each is one focused brain call with its own system prompt and a named deliverable, chained by the orchestrator. This is honest multi-agent: distinct roles, distinct artifacts, one assembly line.

| Agent | Step of the Biks Method | Deliverable |
|---|---|---|
| The Listener | **Listen** — transcribe the meeting / parse the chat, extract facts from files | Fact sheet (JSON) |
| The Analyst | **Map · Quantify · Prioritize** — current-state workflow map, hours lost per workflow, impact × effort matrix | Diagnostic: workflow map + Automation Priority Matrix |
| The Architect | **Build (spec)** — turn the diagnosis into build documents | PRD draft + ForgeSpec |
| The Builder | **Build (run)** — provision the sandbox, assemble the agent | The live agent at a chat URL |
| The Inspector | QA — converse with the forged agent, verify prices, payment guardrail, escalation | Test report (pass/fail per acceptance check) |

The Biks Method (say it exactly this way to clients and judges): **Listen → Map → Quantify → Prioritize → Build.** The Prioritize artifact is a 2×2 impact-versus-effort matrix over the client's workflows; it justifies which agent gets built first and reads like a consulting deliverable, which is the point. The Inspector runs before the client ever sees the link: an agent that QAs the agent is our guardrail identity, demoed.

Build order note: the Listener, Architect, and Builder are the midday-gate path (they existed in section 2 under other names). The Analyst's matrix and the Inspector are afternoon work, high pitch value, low risk, each one brain call plus a template.

## 3. The contract: ForgeSpec v2 (aligned to production)

**Read `WORKFLOW_LIBRARY.md` in this folder first** — it maps the seven production workflows the Forge clones and the config seam of each.

**WHAT THE DEMO SHOWS: THE FORGE ROUTES, THEN CLONES.** Nura roleplays a NEW prospect describing ANY business pain - he may tell a reconciliation story (like our real baba client), a customer-service/order story (like Sentuh Rasa), or improvise. The pipeline must survive improvisation because the routing is the product:

1. **Listen** - extract the painpoints from the meeting/chat (this is what our production intake bot already does; reuse its routing logic - see `biks-intake/SKILL.md` in the private repo and the intake-routing map in WORKFLOW_LIBRARY.md).
2. **Route** - the Analyst maps the painpoint to a workflow in the library: `recon` (baba pattern) or `sales` (Sentuh Rasa/Rosalie pattern) for tomorrow; the architecture must make adding a third template obviously trivial.
3. **Clone + configure** - stamp the prospect's config (channels/fees/outlets for recon; catalogue/prices/store for sales) onto the matched workflow template. Templates are trimmed ports of the production patterns, never built from scratch.
4. **Forge + hand over** - Daytona sandbox, chat URL, and the forged agent's FIRST message is a personalized "how to use me" onboarding (copy the baba pattern - baba greets new groups with exactly this). It is an AI employee that starts working immediately: takes the daily closing, reads the mutasi, or takes orders - whatever it was forged as.

**Both workflow templates are required** so the router demonstrably chooses. The recon fixtures in `test-data/recon/` are a TEST AID for that path (they verify the clone computes correctly), not the demo's spine. If the day runs short and only one template ships, Nura must be told which story to tell on stage - that is a fallback, not the plan.

The pitch line: "Whatever problem he had described, the Forge would have picked the matching workflow from our production library and cloned it for him. The Forge is the product; the library is the moat."

**Daytona's role is the business model, not stage dressing:** every agent Biks forges is tested by the customer in its sandbox. Customer confirms it solves the pain, pays the fee, and ONLY THEN do we buy a WhatsApp number and deploy (Cloud API config on our existing WABA). Try, then pay, then deploy.

The ForgeSpec carries a `workflow` discriminator. The recon variant, sample for the demo persona (see `DEMO_SCRIPT.md` and `test-data/recon/`):

```json
{
  "workflow": "recon",
  "persona": { "agent_name": "Nusa", "language": "id", "tone": "tenang, jelas, tidak menyalahkan", "owner_name": "Pak Dharma", "admin_name": "Mbak Sari" },
  "business": { "name": "Dapoer Nusantara", "outlets": ["DN1", "DN2"], "bank": "BCA" },
  "channels": [
    { "name": "CASH",     "hits_bank": false },
    { "name": "QRIS",     "fee_rate": 0.007, "settle_days": 1, "assumed": true },
    { "name": "GOFOOD",   "fee_rate": 0.20,  "settle_days": 2, "assumed": true },
    { "name": "GRABFOOD", "fee_rate": 0.20,  "settle_days": 1, "assumed": true },
    { "name": "TRANSFER", "fee_rate": 0.0,   "settle_days": 0 }
  ],
  "policy": { "currency": "IDR", "guardrails": [
    "match on GROSS, book fees separately - never call a fee a selisih",
    "known settlement delays are amber with expected amount and date",
    "whatever remains is red: never force the gap to zero, ask the admin"
  ] }
}
```

Fields marked `"assumed": true` came from the Analyst's standard rates and MUST be shown on the confirm screen for the founder to accept or edit.

**The promotion story (still literal):** the recon variant promotes into the production Hermes+MCP reconciliation pattern (same SOUL/AGENTS + bot.env seam as the live pilot); the sales variant emits the production platform's real `products.json` schema. Promotion to WhatsApp is a Cloud API phone-number config on the existing WABA, not a SIM purchase.


<details><summary>Secondary path: the Sales & Order variant (build only if time allows)</summary>

```json
{
  "persona": {
    "agent_name": "Sari",
    "language": "id",
    "tone": "warm, brief, professional",
    "owner_name": "Bu Sari"
  },
  "products": {
    "store": { "name": "Sari's Catering", "location": "Kebayoran, Jakarta Selatan",
               "hours": "08.00-17.00 WIB", "wa_number": "", "kurir": "internal" },
    "categories": [
      { "name": "Nasi Box", "variants": [
        { "id": "NB-AYB", "name": "Nasi Box Ayam Bakar", "price": 35000, "aliases": ["ayam bakar"] },
        { "id": "TP-MIN", "name": "Tumpeng Mini", "price": 150000, "aliases": ["tumpeng"] }
      ] }
    ]
  },
  "policy": {
    "currency": "IDR",
    "payment": "transfer-with-proof",
    "guardrails": [
      "never invent a price — catalogue only",
      "never confirm an unverified payment — owner verifies",
      "off-catalogue or unusual requests escalate to the owner"
    ]
  }
}
```

</details>

**Why the sales-variant schema matters (promotion story detail):** production onboarding today is 8 manual steps (`ROSALIE_MIGRATION_PLAN.md` in the platform repo): copy the app skeleton, write products.json, write the persona, tests, fill .env, CI, Caddy, dashboard. The Forge's Architect auto-generates steps 2, 3, and 5 (products.json, persona text, prefilled .env template). Promotion to real WhatsApp is a **Cloud API phone-number config on the existing WABA** — the production norm across four Biks deployments — not a SIM purchase. Sandbox test drive → client yes → founder runs the remaining mechanical steps with the generated files.

Test data: `sessions/62812xxxx7431-dapur-bu-sari/` in this repo has a realistic 366-row order Excel and a finished intake spec for this exact persona.

## 4. Component specs

### 4.1 Provisioner

- Input validation: reject a spec missing `business_name` or a non-empty `catalogue`.
- Daytona via the official SDK (TypeScript or Python). Exact calls confirmed at the morning workshop; the shape is: create sandbox → upload/write agent files → start server process → get the preview URL for the server port.
- Slug the business name for the URL. Keep a tiny in-memory map `{slug → sandbox_id}` so re-forging the same business replaces its sandbox.
- Target: request → returned chat URL in under 90 s. Measure it; the number is a demo line.

### 4.2 Sandbox agent

- One file if possible. Reads `spec.json` from disk.
- Architecture is the estate-wide Biks pattern, **understand → decide → speak** (see WORKFLOW_LIBRARY.md DNA): Kimi parses the message/files into structured rows (understand), deterministic code decides (below), Kimi phrases the code-drafted reply in the persona's voice (speak). If the voice step drops or changes a number, send the code-drafted text instead (kopi's token-survival trick, simplified).
- **The recon decide() - not written from scratch: it is a ~150-line PORT of the production reconciliation engine (Dev A knows it inside out), as pure functions:** normalize channel names (aliases: "gofood"/"go food"/"gojek" → GOFOOD); parse closing text → gross per channel; parse mutasi CSV → credits with descriptions; classify each credit to a channel by description keywords; match on GROSS: credit ≈ gross − round(fee_rate × gross) → MATCHED with fee booked separately; unmatched closing channel with settle_days > 0 → IN-TRANSIT with expected net amount and date; unmatched bank credit or residual → RED, never forced to zero; CASH (hits_bank false) and bank DB rows → info. The acceptance fixture in `test-data/recon/EXPECTED_OUTPUT.md` defines every verdict and number this must produce.
- System prompt assembled FROM the spec (persona + store + price list). Keep it under a page.
- Kimi via OpenAI-compatible chat completions (key + base URL from the sponsor booth; env, never hardcode).
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
| **Midday gate 1** | **Dapoer Nusantara recon spec (hand-written JSON) → live chat link → correct verdicts on the fixtures. Nothing else matters until this passes.** | | |
| Afternoon | Meeting Mode: upload → transcribe → spec extract → confirm screen | Guardrail polish; latency measurement; auto-PRD template | Record Beat 1 of DEMO_SCRIPT.md as the meeting fixture; pitch; backup screen recording |
| **Gate 2** | **Meeting recording → confirmed spec → forged agent, end to end. This is the stage demo.** | | |
| If time | 3-sandbox parallel moment | Oxylabs pre-research beat | Pitch rehearsal twice |

## 6. Acceptance criteria

- [ ] `POST /forge` with the Dapoer Nusantara recon spec returns a working chat URL in < 90 s (measure, record the number)
- [ ] The Beat-1 meeting recording (DEMO_SCRIPT.md) produces a confirmable recon ForgeSpec: 2 outlets, 5 channels, assumed fee rates flagged for confirmation
- [ ] Feeding `closing-DN1-16jul.txt` + `mutasi-BCA-17jul.csv` yields EXACTLY the verdicts in `test-data/recon/EXPECTED_OUTPUT.md` (QRIS/Grab/Transfer matched with fees 10.010/168.000/0, GoFood in-transit expecting ±1.000.000, the 50.000 credit RED)
- [ ] "Yang 50 ribu itu apa?" NEVER yields an invented explanation, always the ask-the-admin line
- [ ] **Router**: a recon-pain description routes to `recon`; an order-chaos description routes to `sales` - from the meeting text alone, no hints
- [ ] **Sales template**: forged sales agent quotes only catalogue prices, recomputes totals in code, never confirms an unverified payment
- [ ] **Onboarding message**: every forged agent's first message is a personalized "how to use me" (the baba greeting pattern), in the client's language
- [ ] The auto-PRD draft renders from the same spec
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
