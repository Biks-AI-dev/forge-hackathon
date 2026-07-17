# Demo script · the baba use case

Nura plays "Pak Dharma", owner of **Dapoer Nusantara** (fictional, 2 outlets, Denpasar). Painpoints mirror our reconciliation pilot. Total stage time ~4 minutes. Record the meeting part ONCE tonight as the backup fixture; do it live on stage if nerves allow.

## Beat 1 · The meeting (~90 seconds, recorded or live)

Pak Dharma talks, the FDE (Adith or dev) just listens and nods. Script, natural Bahasa, keep the numbers EXACTLY as written because the extractor and fixtures depend on them:

> "Jadi saya punya warung makan, Dapoer Nusantara, dua outlet di Denpasar. Ramai sih, sehari bisa enam jutaan satu outlet. Tapi masalah saya bukan jualan, masalah saya itu tiap pagi. Admin saya, Mbak Sari, itu tiap pagi buka mutasi BCA, terus cocokin satu-satu sama closing-an SPG semalam. Dua sampai tiga jam tiap hari, mas.
>
> Uang masuknya kan macem-macem. Ada cash, ada QRIS, ada GoFood, GrabFood, ada yang transfer langsung. Yang bikin pusing, angkanya nggak pernah sama persis. QRIS itu kepotong dikit, yang online-online itu potongannya gede, dua puluh persen kayaknya. Terus GoFood itu masuknya bukan besoknya langsung, kadang lusa. Jadi tiap hari pasti ada selisih, dan Mbak Sari harus nebak-nebak ini selisih normal apa ada yang hilang.
>
> Bulan lalu sempet ada beda tujuh ratus ribu, dua hari baru ketemu, ternyata settlement telat sama ada biaya admin. Capek, mas. Saya cuma pengen tiap pagi ada yang bilang: ini semua cocok, ini masih di jalan, yang ini tolong dicek."

Why these lines: they hand the Listener every spec field: business name, 2 outlets, channels (cash/QRIS/GoFood/GrabFood/transfer), fee ballparks (small for QRIS, "20%" for online), settlement delay (GoFood H+1/H+2), the admin's name, the hours lost (2-3/day), and the desired outcome in his words (matched / in-transit / check-this). The Analyst turns those into the matrix; the Architect into the spec.

## Beat 2 · Files handed over (~15 seconds)

Pak Dharma "sends" two files (from `test-data/recon/`):
- `closing-DN1-16jul.txt` — yesterday's shift closing from the SPG
- `mutasi-BCA-17jul.csv` — this morning's bank statement

## Beat 3 · Confirm + forge (~60 seconds)

Confirm screen shows the extracted spec (channels, fee rates marked "assumed standard, confirm", outlets, persona). Tap confirm. Sandbox spins. Narrate the agent roster while it builds: Listener heard him, Analyst mapped and prioritized, Architect wrote the spec and the PRD, Builder is forging, Inspector will test before Pak Dharma sees it.

## Beat 4 · Pak Dharma tests HIS agent (~90 seconds, the wow)

On the phone, in the sandbox chat, he asks exactly:

1. **"Gimana kemarin?"** → the agent gives the one-line summary from `test-data/recon/EXPECTED_OUTPUT.md`: matched Rp 2.885.000 (net in Rp 2.706.990, fees Rp 178.010), GoFood Rp 1.250.000 in-transit expecting ±Rp 1.000.000 tomorrow, cash Rp 2.150.000 physical, and one Rp 50.000 credit it does not recognize.
2. **"Yang 50 ribu itu apa?"** → the agent says it will NOT guess: no closing counterpart, flagged for Mbak Sari. (This is the anti-hallucination moment; say the line "it refuses to invent an explanation, that is the product.")
3. **"Kalau GoFood nggak masuk besok?"** → stays amber with expected amount, escalates if past H+2.

Close: "Mbak Sari's two hours are now this chat. And this config file is the same format running our production clients today."

## Fallbacks

- Meeting audio fails on stage → play tonight's recording (Beat 1 is identical).
- Forge fails live → pre-forged sandbox from the same spec, kept warm; narrate honestly ("forged earlier today").
- Everything fails → screen recording (record it as soon as gate 2 passes, non-negotiable).
