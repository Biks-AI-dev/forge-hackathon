# Attack Plan · Daytona Hackathon

Hard constraints that shape everything: **hacking runs 11:30–16:30 SGT (5 hours, lunch inside it)** and **the stage demo is 2 minutes**. The plan is built backward from those two numbers.

Team topology: **Nura + Tracy in the room (Singapore)** · **Ghaly (Dev A) + Reva (Dev B) building remotely (Indonesia, SGT−1)** · **Adith async from Melbourne (SGT+2)**. Daytona is cloud, so remote building works; the room only needs a laptop, a phone, and the sponsor keys.

Time key: SGT / WIB / MEL.

## Roles, one line each

| Who | Role | Owns |
|---|---|---|
| Nura | The client & the pitch | Beat-1 recording, Pak Dharma roleplay, 2-min demo delivery, sponsor relationships |
| Tracy | In-room orchestrator | Gate calls, relaying sponsor keys to devs, running the demo machine, timeboxing, the backup recording |
| Ghaly (Dev A) | Provisioner + recon engine | spec → Daytona sandbox → URL; the deterministic `decide()` (he knows the baba pattern) |
| Reva (Dev B) | Sandbox agent + channel | agent server + Kimi + chat page; Daytona setup (his SANDBOX_SETUP notes → PR into this repo tonight) |
| Adith | Async architect | Reviews at every gate via WA; unblocks on protocol/spec questions |

## Tonight, before sleep (checklist)

- [ ] Everyone: `git pull` on forge-hackathon; read `PRD.md`, `DEMO_SCRIPT.md`, `test-data/recon/EXPECTED_OUTPUT.md`
- [ ] **Nura: record Beat 1** of DEMO_SCRIPT.md on his phone, one take, numbers EXACTLY as written; drop the file in the team WA group. This is the day's most important artifact: extractor fixture + stage fallback in one.
- [ ] Reva: PR his Daytona setup .md into this repo as `SANDBOX_SETUP.md`
- [ ] Ghaly: skim `bali-banana-recon` engine (he knows it) and `EXPECTED_OUTPUT.md`; the decide() is a ~150-line simplification of what he already built
- [ ] Tracy: read this file + DEMO_SCRIPT.md twice; she is the one who says "gate passed" or "cut scope"

## The day, backward from the demo

| SGT | WIB | MEL | Milestone (gate = Tracy calls it in WA) |
|---|---|---|---|
| 10:00 | 09:00 | 12:00 | Kickoff. Tracy opens a video call/screen-share with Ghaly & Reva that stays on ALL DAY |
| 10:30 | 09:30 | 12:30 | Workshop: Tracy+Nura collect Daytona/Kimi/Nosana keys → paste to devs immediately. Devs replay the workshop steps remotely via SANDBOX_SETUP.md |
| 11:30 | 10:30 | 13:30 | Hacking begins. Ghaly: provisioner + decide(). Reva: agent server + chat page |
| **13:00** | 12:00 | 15:00 | **GATE 1: hand-written Dapoer Nusantara spec → live sandbox chat URL.** Not passed by 13:30 → drop Meeting Mode, demo starts from the confirm screen |
| **14:00** | 13:00 | 16:00 | **GATE 2 (the one that matters): fixtures in → EXPECTED_OUTPUT verdicts out, digit for digit.** QRIS/Grab/Transfer matched with fees 10.010/168.000/0 · GoFood amber ±1.000.000 · the 50.000 credit red, never explained away. Not passed by 14:30 → all four people on this single problem |
| **15:00** | 14:00 | 17:00 | **GATE 3: Nura's Beat-1 recording → transcript → extracted spec → confirm → forge, end to end.** Behind schedule? CLI instead of upload page; the story "extracted earlier today" still works on stage |
| **15:30** | 14:30 | 17:30 | **FEATURE FREEZE.** Tracy records the full backup screen video. Pre-forge a warm sandbox as the stage spare. Nura rehearses the 2-min cut twice, timed |
| 16:30 | 15:30 | 18:30 | Live demo, 2 minutes (script below) |

Adith async check-ins from Melbourne at each gate time (14:00 / 16:00 / 17:00 MEL): Tracy posts gate status + blockers in WA, Adith replies within the hour.

## The 2-minute stage cut (the 4-beat script compressed)

- **0:00–0:20** Nura: "This morning I met a restaurant owner. Here's 10 seconds of that meeting." Play a snip of the recording, then show the extracted spec on the confirm screen: "Our agents listened, mapped his channels, his fees, his pain."
- **0:20–0:50** Tap Forge, live. Sandbox spins on the projector. One line while it builds: "Listener heard him, Architect wrote the spec, Builder is forging his agent in its own isolated machine, Inspector tests it before he sees it."
- **0:50–1:40** Phone in hand, as Pak Dharma: "Gimana kemarin?" → the verdict summary with real numbers. Then the money moment: "Yang 50 ribu itu apa?" → the agent refuses to guess and flags it for the admin. Say it: **"It caught a 50,000-rupiah discrepancy and refused to invent an explanation. That refusal is the product."**
- **1:40–2:00** Close: "The intake runs in production today. This config is our production format. 70 million SEA businesses run on WhatsApp and Excel; this is how they hire their first AI employee: by talking."

Fallbacks, in order: warm pre-forged sandbox → backup screen video. Both must exist by 15:45, no exceptions.

## Scope discipline for a 5-hour window

Build ONLY: provisioner, sandbox agent with deterministic decide(), chat page, extraction from the recording. **Already cut for time** (do not resurrect unless all gates pass early): the Analyst's 2×2 matrix, the Inspector as a separate agent (Tracy manually runs the EXPECTED_OUTPUT checks instead), auto-PRD, upload page polish, Oxylabs, 3-sandbox moment, "Forge so far".

## Risks with owners

| Risk | Mitigation | Owner |
|---|---|---|
| Kimi key arrives late / model flaky | Agent runs on any OpenAI-compatible endpoint; env-swap only. Build against what works, swap at 15:00 | Reva |
| Venue wifi vs remote devs | Everything lives in the cloud (repo + Daytona); the room only opens URLs. Tracy can run every step from her laptop alone | Tracy |
| Gate 2 arithmetic wrong | decide() is pure functions; test locally against fixtures BEFORE wiring to the sandbox | Ghaly |
| 2-min overrun | Rehearse timed, twice. Cut beat 1 first if over, never beat 3 (the 50k moment) | Nura |
| Demo-time sandbox death | Warm spare + backup video from 15:45 | Tracy |
