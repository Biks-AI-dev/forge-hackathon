# Demo script · the baba use case

Nura plays "Pak Dharma", owner of **Dapoer Nusantara** (fictional, 2 outlets, Denpasar). Painpoints mirror our reconciliation pilot. Total stage time ~4 minutes. Record one take (any take, natural speech) as the backup + dev test audio; live on stage also works.

## Beat 1 · The meeting (~90 seconds, spoken NATURALLY)

**No script to memorize. Nura knows these painpoints by heart (it is the baba story).** Talk exactly like the real Bali Banana meetings. The extractor handles free speech; the numbers live in the FILES (Beat 2), so nothing spoken needs to be precise.

Just make sure the story TOUCHES these 5 things (any words, any order):

1. An F&B business with a couple of outlets/branches
2. Money comes in many ways: cash, QRIS, GoFood, GrabFood, transfer
3. The morning pain: admin matches the bank mutasi against last night's closings, by hand
4. Roughly how long it takes (a few hours, every day)
5. What he wants: "tell me what matches, what's still coming, what to check"

If he skips fee percentages, fine: the Architect fills standard rates marked "asumsi" and the confirm screen shows them. That IS a feature moment, not a gap.

## Beat 2 · Files handed over (~15 seconds)

Pak Dharma "sends" two files (from `test-data/recon/`):
- `closing-DN1-16jul.txt` — yesterday's shift closing from the SPG
- `mutasi-BCA-17jul.csv` — this morning's bank statement

## Beat 3 · Confirm + forge (~60 seconds)

Confirm screen shows the extracted spec (channels, fee rates marked "assumed standard, confirm", outlets, persona). Tap confirm. Sandbox spins. Narrate the agent roster while it builds: Listener heard him, Analyst mapped and prioritized, Architect wrote the spec and the PRD, Builder is forging, Inspector will test before Pak Dharma sees it.

## Beat 4 · Pak Dharma tests HIS agent (~90 seconds, the wow)

THE HANDOVER, played exactly like reality: the FDE says "coba langsung, Pak" and sends the link. Nura (as the prospect) opens it on the phone and types just "halo" - nothing else. The agent's reply is the wow: it greets him by name and company, names his own painpoint back at him, and gives the role-by-role panduan (baba's real structure). Let the audience read it for 3 seconds. THEN he uses it:

1. **"Gimana kemarin?"** → the agent gives the one-line summary from `test-data/recon/EXPECTED_OUTPUT.md`: matched Rp 2.885.000 (net in Rp 2.706.990, fees Rp 178.010), GoFood Rp 1.250.000 in-transit expecting ±Rp 1.000.000 tomorrow, cash Rp 2.150.000 physical, and one Rp 50.000 credit it does not recognize.
2. **"Yang 50 ribu itu apa?"** → the agent says it will NOT guess: no closing counterpart, flagged for Mbak Sari. (This is the anti-hallucination moment; say the line "it refuses to invent an explanation, that is the product.")
3. **"Kalau GoFood nggak masuk besok?"** → stays amber with expected amount, escalates if past H+2.

Close: "Mbak Sari's two hours are now this chat. And here is the point: we did not build this bot today. It is a CLONE of our production reconciliation workflow, stamped with Pak Dharma's config by the Forge. The Forge is the product; the library is the moat."


## STAGE VERSION: the 2-minute cut (the real agenda allows 2 min, not 4)

The 4 beats above are the FULL script: use it for tonight's recording and the backup video. On stage, run the compressed cut in `ATTACK_PLAN.md` ("The 2-minute stage cut"): 20s meeting snip + confirm screen, 30s live forge, 50s Pak Dharma's two questions ending on the 50k refusal, 20s close. Rehearse timed, twice, before 16:00 SGT.

## Fallbacks

- Meeting audio fails on stage → play tonight's recording (Beat 1 is identical).
- Forge fails live → pre-forged sandbox from the same spec, kept warm; narrate honestly ("forged earlier today").
- Everything fails → screen recording (record it as soon as gate 2 passes, non-negotiable).
