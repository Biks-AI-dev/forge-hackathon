# Attack Plan · Daytona Hackathon

**What we are building, one sentence:** the Forge — a meeting with a prospect becomes their own AI employee in minutes; the LINK goes to the prospect, they type "halo", and the agent already knows them and guides them, exactly like production baba greets its client.

Hard constraints: **hacking runs 11:30–16:30 SGT (5 hours, lunch inside)** and **the stage demo is 2 minutes**. Everything below is built backward from those.

Team: **Nura + Tracy in the room (Singapore)** · **Ghaly (Dev A) + Reva (Dev B) remote (Indonesia, SGT−1)** · **Adith async (Melbourne, SGT+2)**. Daytona is cloud; the room only needs a laptop, a phone, and the sponsor keys.

## The demo we are building toward (read demo-storyboard.html — it IS the definition of done)

1. Meeting: prospect describes company → painpoint → what to solve (Nura roleplays; any story works)
2. Forge: routes the pain to the production library (recon | sales), clones the matched workflow, stamps the prospect's config
3. Handover: **the link is sent to the PROSPECT's phone**
4. **THE HALO MOMENT: prospect types "halo" → the agent greets them by name, names their painpoint unprompted, and delivers the role-by-role panduan (production baba's exact structure: tugasku / cara pakai per role / echo→"ya"→tercatat ✅ / status 🟢🟡🔴 / ajak coba)** — then it works
5. Close: try in sandbox → pay when it solves the pain → only then WhatsApp

## Roles

| Who | Owns |
|---|---|
| Nura | Beat-1 meeting recording (tonight), the roleplay, the 2-min delivery, sponsor relationships |
| Tracy | Gate calls + scope cuts, relaying sponsor keys, running the demo machine, backup recording, timeboxing |
| Ghaly (Dev A) | Provisioner (spec → Daytona → URL) + the recon engine port (he built the production one) |
| Reva (Dev B) | Sandbox agent template + Kimi + halo greeting + chat page; Daytona setup (his notes → `SANDBOX_SETUP.md`) |
| Adith | Async review at every gate via WA, replies within the hour |

## Tonight, before sleep

- [ ] Everyone: `git pull`, read `PRD.md`, `DEMO_SCRIPT.md`, open `demo-storyboard.html` in a browser
- [ ] **Nura: record Beat 1** (the meeting monologue in DEMO_SCRIPT.md), one take, numbers exactly as written, drop in WA
- [ ] Reva: PR the Daytona setup notes as `SANDBOX_SETUP.md`
- [ ] Ghaly: skim the production recon engine + `test-data/recon/EXPECTED_OUTPUT.md`
- [ ] Tracy: read this file twice; tomorrow she calls gates and cuts scope

## Gates (SGT / WIB / MEL) — Tracy calls each in WA

| SGT | WIB | MEL | Gate |
|---|---|---|---|
| 10:00 | 09:00 | 12:00 | Kickoff. Tracy opens the all-day video call with Ghaly & Reva |
| 10:30 | 09:30 | 12:30 | Workshop. Keys collected → pasted to devs immediately. Devs replay via SANDBOX_SETUP.md |
| 11:30 | 10:30 | 13:30 | Hacking. Ghaly: provisioner + recon port. Reva: agent template + halo greeting + chat page |
| **13:00** | 12:00 | 15:00 | **G1: hand-written recon spec → Daytona sandbox → live chat URL.** Late by 30 min → Meeting Mode becomes CLI, nothing else changes |
| **14:00** | 13:00 | 16:00 | **G2, the day's center: THE HALO TEST + the fixtures.** Fresh browser, type only "halo" → greeting knows the name, the painpoint, the roles (generic greeting = FAIL). Then closing + mutasi in → `EXPECTED_OUTPUT.md` verdicts out, digit for digit, ending in the 50k refusal. Late by 30 min → all four devs+Tracy swarm this |
| **15:00** | 14:00 | 17:00 | **G3: full pipeline + router.** Nura's recording → spec → ROUTE → forge, end to end; sales template live so the router demonstrably chooses. Behind? Ship recon only, brief Nura to tell the recon story, say the router line verbally |
| **15:30** | 14:30 | 17:30 | **FREEZE.** Backup screen video (Tracy). Warm spare sandbox. Nura rehearses the 2-min cut twice, timed |
| 16:30 | 15:30 | 18:30 | Stage. 2 minutes |

Adith checks WA at 15:00 / 16:00 / 17:00 MEL; Tracy posts gate status + blockers.

## The 2-minute stage cut

- **0:00–0:20** Nura: "This morning I met a restaurant owner. Two outlets. Every morning his admin loses three hours matching the bank against last night's closings. Here's ten seconds of that meeting." (play snip)
- **0:20–0:45** "Our Forge listened, recognized this as the reconciliation workflow we already run in production, and cloned it for him." Tap FORGE live; sandbox spins on the projector.
- **0:45–1:40** "Then we did what we always do: we sent HIM the link." Phone on screen. Type "halo". **Pause 5 seconds so the room reads the greeting that already knows him.** Then one worked exchange: closing + mutasi in → verdicts out → "yang 50 ribu itu apa?" → the refusal. Say: "It caught a fifty-thousand-rupiah discrepancy and refused to guess. That is the product."
- **1:40–2:00** "He tries it in its own sandbox. When it solves his pain, he pays. Only then do we put it on WhatsApp. If he'd described a different problem, you'd have met a different employee. The Forge is the product; the library is the moat."

Fallbacks in order: warm pre-forged sandbox → backup video. Both exist by 15:45, no exceptions.

## Scope

Build only: provisioner · sandbox agent template (halo greeting + understand→decide→speak) · recon engine port · sales template · router · chat page · meeting-recording → spec extraction. Cut without discussion if time bites, in this order: upload-page polish (CLI is fine) → sales template (brief Nura) → Nosana/Oxylabs → everything else already parked in the PRD.

## Risks

| Risk | Mitigation | Owner |
|---|---|---|
| Kimi key late/flaky | Any OpenAI-compatible endpoint; env swap only | Reva |
| Venue wifi vs remote devs | Everything cloud; Tracy can run every step alone from her laptop | Tracy |
| Halo greeting comes out generic | It is templated from the spec (name, painpoint, roles interpolated), not freestyle LLM prose; the LLM only voices it | Reva |
| Verdict arithmetic wrong | decide() is pure functions, tested against fixtures locally before wiring | Ghaly |
| 2-min overrun | Rehearse timed twice; if over, cut the forge-spin wait, never the halo pause or the 50k refusal | Nura |
| Sandbox dies on stage | Warm spare + backup video | Tracy |
