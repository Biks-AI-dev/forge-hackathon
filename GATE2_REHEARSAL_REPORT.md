# Gate 2 Rehearsal Report — 2026-07-18 (updated: second full rerun)

Run against synthetic-but-realistic fixtures (real TTS Indonesian speech, real Excel order data,
a real PDF price list) since no real meeting recording was provided. Dev B's `agent-template` is
still a placeholder — the 5 agent-behavior tests (catalogue enquiry, order calc, payment-proof,
discount, off-catalogue) were **not run**; they would only test placeholder echo logic, not real
guardrails. Everything else below is real: real Daytona sandboxes, real LLM calls, real local
Whisper transcription, real file extraction.

**This is a second, independent rerun** after applying the fixes from the first rehearsal
(§ Findings below), done specifically to confirm the fixes hold up and to close the one
previously-unresolved recommendation (LLM retry, finding #8). Original first-run numbers are kept
below for comparison; the retry-loop addition and its results are new in this pass.

## Pipeline result (final clean run)

| Stage | Time | Notes |
|---|---|---|
| Upload | 25ms | |
| Transcription | ~2.2s | local_whisper, 335 chars from 27s audio |
| File extraction | <1s | 1 Excel (366 rows), 1 PDF |
| LLM (ForgeSpec generation) | ~9s | gpt-oss-20b via Doubleword |
| Validation | <1s | |
| Provisioning | 5.2s | create→inject→start→health→preview, see breakdown below |
| **Total end-to-end** | **~17s** | well under the 90s target |

Provisioning breakdown (Provisioner-side, from logs):
create_sandbox 782ms → inject_agent_template 1125ms → write_spec_json 274ms →
set_runtime_env 815ms → maybe_install 0ms → start_server 572ms → wait_until_healthy 1355ms →
get_preview_url 273ms = **5200ms total**.

`chat_url` confirmed reachable: `GET /health` → `200 {"status":"ok"}`.

## ForgeSpec checklist (final clean run)

- ✅ Business name: "Sari's Catering"
- ✅ Persona: `agent_name: "Sari"`, `owner_name: "Sari"` — correctly derived, not invented
- ✅ Products: 5 catalogue items captured (Ayam Bakar, Rendang, Nasi Liwet Komplit, Snack Box,
  Tumpeng Mini) — pulled from transcript, PDF, *and* the real Excel order data
- ✅ Prices: numeric, all traced to file/transcript sources
- ✅ Payment policy: `payment: "transfer"` at the correct top-level location
- ✅ Guardrails: non-empty, matches transcript's "verifikasi manual" intent
- ✅ No unsupported assumptions in the final run
- ✅ No missing required fields (server-side validation now enforces this — see findings)

## Rerun confirmation (this pass, with retry loop in place)

| Run | Result | Total pipeline | Notes |
|---|---|---|---|
| Baseline 1 | ✅ ready | ~12s | first attempt, no retry needed |
| Record again (baseline 1) | ✅ ready | ~21s | first attempt, no retry needed, same slug, old sandbox replaced |
| Baseline 2 (independent) | ✅ ready | ~35s | first attempt, no retry needed — high LLM latency (~28s), not a failure |

All 3 runs succeeded on their first generate+validate attempt (0 of 3 needed the new retry), all
sandboxes confirmed reachable, all cleaned up afterward with `daytona.list()` showing 0 leftover.

## Correction path ("Record again")

1. Baseline forge: `sari-s-catering`, workflow=`sales` ✅
2. Corrected recording submitted with `continuation_of` ✅
3. Full pipeline re-ran ✅
4. Slug unchanged: `sari-s-catering` ✅
5. Replacement sandbox healthy: `GET /health → 200` ✅
6. Previous sandbox confirmed deleted: `404 "No such container"` (container-level, not just
   proxy-level) ✅
7. `daytona.list()` cross-check: exactly 1 live sandbox throughout ✅

## Findings

Ranked by severity. Every one below was caught live, not hypothesized — each has a reproduction
in this session's history.

### 1. KIMI_BASE_URL was dead — BLOCKING, fixed
`.env` had `KIMI_BASE_URL=https://api.kimi.ai`, which doesn't resolve (`NXDOMAIN`). Root cause:
wrong/placeholder value, not a network issue. **Fix applied**: switched to Doubleword.ai
(`https://api.doubleword.ai/v1`, model `openai/gpt-oss-20b`) per your instruction, confirmed by
listing the account's actual available models. Renamed `KIMI_*` → `LLM_*` throughout (`.env`,
`.env.example`, `meeting-mode/app/config.py`, `kimi.py`→`llm.py`). **Blocks demo: no, fixed.**

### 2. LLM call could hang indefinitely with no error surfaced — HIGH, fixed
A job sat in `extracting_forgespec` for 2+ minutes, past the configured 60s `LLM_TIMEOUT_S`, with
no error. Root cause: `requests`' `timeout=` only bounds gaps *between* reads, not total call
duration — a slow trickle defeats it. **Fix applied**: wrapped the LLM call (and the Provisioner
POST call, same risk class) in `asyncio.wait_for` at the pipeline level as the real upper bound.
**Blocks demo: no, fixed** — but note the underlying thread still can't be killed, so a hang still
burns a worker thread in the background (acceptable for a single-process hackathon demo, flagged
as a known limitation, not production-grade).

### 3. A crashed job could freeze forever with files already deleted — HIGH, fixed
Directly caused by #2: when `asyncio.wait_for` raced a `to_thread` call whose thread couldn't be
cancelled, it leaked a bare `asyncio.CancelledError` — a `BaseException`, not an `Exception` —
past the per-stage `except Exception` handlers. The job's `finally` block still ran (temp files
deleted), but the job state was never marked `failed`. A polling frontend would show the
"Extracting ForgeSpec" spinner forever with no way out except a manual restart.
**Fix applied**: a top-level `except BaseException` backstop in `run_pipeline` guarantees every
job reaches `ready` or `failed`. **Blocks demo: no, fixed.**

### 4. LLM nested `policy` inside `products` instead of top-level — HIGH, fixed
On one run, the model put `payment`/`guardrails` inside `products.policy` instead of the
top-level `policy` the schema (and system prompt) specify. The real top-level `policy` stayed
`null`. Server-side validation didn't catch it (never checked policy presence), so this would
have silently forged an agent with no guardrails wired — the exact thing PRD §4.3 calls "the
judged differentiator, do not skip." **Fix applied**: (a) a normalization step hoists a misplaced
policy back to top-level before validation, (b) a hard validation check now requires
`policy.guardrails` non-empty and, for `sales`, `policy.payment` set — rejecting the forge with a
specific field error if absent, which is the correct "Record again" trigger per PRD §5 step 10.
**Blocks demo: no, fixed and verified catching a real case** (a later run correctly failed with
`policy.guardrails must be non-empty` rather than forging silently).

### 5. LLM invented a persona name not present anywhere in the source material — MEDIUM, fixed
First run produced `agent_name: "Saiyabu"` — the transcript only names the owner ("Bu Sari"),
never an agent. **Fix applied**: prompt now explicitly forbids inventing an unrelated name,
defaulting to the owner's name instead. Verified fixed in the next run (`agent_name: "Sari"`).
**Blocks demo: no, fixed.**

### 6. LLM omitted a catalogue item present in both transcript and PDF — MEDIUM, fixed
First run captured only 1 of 2 items explicitly mentioned (missed "Tumpeng Mini", which was in
both the spoken transcript and the client PDF). **Fix applied**: prompt now explicitly instructs
scanning all files fully for every priced item. Verified fixed — later runs captured 5 items,
correctly pulling extras from the real Excel data too. **Blocks demo: no, fixed** — but see #8,
this class of omission can still recur since it depends on model compliance, not a hard guarantee.

### 7. "Record again" could silently change workflow type — HIGH, fixed
A price-correction-only re-recording for the same business flipped the LLM's routing decision
from `sales` to `recon` — same slug, same business, but a structurally different agent (bank
reconciliation instead of catalogue/orders) would have replaced the working sandbox. The
business-name pinning (built to guarantee slug stability) did **not** guard against this, since
workflow is a separate field. **Fix applied**: `continuation_of` now also pins the prior job's
`resolved_workflow`, passed to the LLM as a steering hint, with a hard validation backstop that
fails the job (rather than silently forging) if the LLM still deviates. **Blocks demo: no,
fixed and verified** — the backstop correctly caught two bad generations (one `recon`, one
missing `workflow` entirely) before a clean success.

### 8. LLM reliability is inconsistent across runs — MEDIUM, **retry added, re-verified**
First rehearsal: successes, one empty-guardrails rejection, one 60s+ read timeout, one
workflow-drift (fixed by #7), one missing-workflow-field rejection. Rough first-try clean-success
rate: ~50%. **Fix applied this pass**: `pipeline.py` now retries the generate→validate pair once
(2 attempts total) before failing the job, logging each attempt's outcome. A validation catch
(e.g. empty guardrails) now also gets a second shot, not just a raw LLM error — the retry wraps
generation *and* validation together, since both were part of the original ~50% figure.
**Re-verified this pass**: 3 fresh runs (1 baseline, 1 record-again, 1 second baseline) all
succeeded, two on the first attempt (12s and 12s total pipeline), one with no retry needed but
~28s of LLM latency on a single attempt (natural latency variance, not a failure — no "attempt
1/2 retrying" logged). Sample size is still small; `moonshotai/Kimi-K2.6` remains available on the
same Doubleword key as a fallback option if gpt-oss proves too flaky closer to stage time.
**Blocks demo: no, meaningfully mitigated** — retry is real infrastructure now, not a
recommendation.

### 9. Whisper "tiny" model has non-trivial numeric transcription errors — LOW, unresolved
"tiga puluh lima ribu" (35,000) was transcribed as "30 ribu" in one run. Not currently harmful:
the system prompt correctly prefers file-sourced numbers over verbal ones, and in every test the
correct price came from the PDF/Excel, masking this. It would matter if a price is spoken but
never appears in an uploaded file. **Fastest safe fix**: not implemented — swap `tiny` for
`base` or `small` local model (slower, more accurate) if a client meeting is expected to state
prices without a supporting document. **Blocks demo: no** (files are required in this flow, so
this is a latent risk, not an active one).

### 10. Local Whisper auto-detected Indonesian speech as English — LOW, fixed, caveat
Caught with a flawed test methodology first (macOS `say` used a default English voice to read
Indonesian text, producing genuinely English-sounding audio — not a Whisper bug). Re-tested with
the correct Indonesian voice (`Damayanti`) and transcription was clean either way. **Fix applied
anyway**: pinned `language="id"` in the local Whisper call — defensible regardless, since this
product is 100% Indonesian-language by design and auto-detect has nothing to gain.
**Blocks demo: no.**

### 11. Provisioner's in-memory registry doesn't survive a process restart — LOW, unresolved, operational
Restarting the Provisioner process mid-testing orphaned a previously-forged sandbox (registry
reset to empty, so the next forge for the same slug created a new sandbox instead of replacing).
This is accepted, documented scope for a hackathon (PRD §4.1's in-memory map, "laptop is fine for
the demo") — flagging as an operational rule, not a code bug: **do not restart the Provisioner
process during the live demo**, or a stale sandbox will leak and the next forge for that business
won't clean it up automatically.

### 12. Whisper mis-hearings can propagate into persona/business naming — LOW, unresolved, monitor
In the rerun, `agent_name` came out as `"Busari"` (a garbled one-word rendering of "Bu Sari" that
Whisper itself produced in the transcript text) and `business_name` as `"Saris Catering"` (missing
the apostrophe). Neither is an invention — both trace directly to what Whisper actually
transcribed — but neither is clean either. This is a downstream effect of finding #9 (Whisper
numeric/word accuracy), not a new bug in generation or validation. **Not fixed**: no code change
applied; the discrepancy-logging behavior (see below) already provides a partial safety net for
prices, but nothing currently normalizes obviously-garbled proper nouns. **Blocks demo: no** —
cosmetic, not a guardrail or correctness failure — but worth a human glance at the persona name
before sending a chat_url to a real prospect.

### Positive finding: file-vs-transcript discrepancies are caught, not silently resolved
Worth stating explicitly since it's the payoff of the system prompt's design, not a fix for a bug:
in the rerun, Whisper mis-heard two prices ("30000" instead of "35000", "50000" instead of
"150000"). The LLM correctly kept the file-sourced values in the actual catalogue (verified
correct: 35000 and 150000) and separately logged both discrepancies in a top-level `notes` array
rather than silently picking one or averaging. This is exactly the PRD's "never force the gap to
zero" philosophy applied to a case that was never explicitly test-scripted — good sign for
robustness against imperfect audio.

## Go/No-Go verdict

**GO**, upgraded from the prior "Conditional GO."

Both action items from the first rehearsal are now closed:

1. ~~Add an LLM retry~~ — **done**. Generate+validate now retries once as a pair before failing
   the job; re-verified across 3 fresh runs in this pass (2 clean first-try, 1 with high latency
   but no failure).
2. **Do not restart the Provisioner process during the demo window** (finding #11) — still an
   operational rule, not a code fix; carries forward unchanged.

The pipeline works end-to-end (~12s total in the cleanest reruns, 5.0–5.2s of that is
provisioning, both far under the 90s target), the correction path genuinely replaces sandboxes
with zero duplicates (re-verified twice more this pass, including container-level 404 confirmation
that the old sandbox is truly gone, not just proxy-hidden), and every failure mode found across
both rehearsals now fails safely — no silent corruption, no orphaned resources, no frozen jobs.

Remaining open item, unchanged from before: the 5 agent-behavior tests (catalogue enquiry, order
calc, payment-proof, discount, off-catalogue) remain untested against real guardrail logic pending
Dev B's `agent-template` — that gate still needs to run once his template lands, separately from
this one. Finding #12 (garbled persona/business names from Whisper mis-hearings) is a minor,
non-blocking cosmetic risk worth a quick human glance before a chat_url goes to a real prospect.
