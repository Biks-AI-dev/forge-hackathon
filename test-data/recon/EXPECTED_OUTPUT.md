# Expected reconciliation verdicts (the acceptance fixture)

Given `closing-DN1-16jul.txt` (omzet by channel) and `mutasi-BCA-17jul.csv` (bank credits), the forged agent's deterministic engine MUST produce exactly these verdicts. Every number below is arithmetic, not judgment; if any differs, the engine is wrong, not the fixture.

| Channel | Closing (gross) | Bank credit | Verdict | Explanation the agent gives |
|---|---|---|---|---|
| QRIS | Rp 1.430.000 | Rp 1.419.990 | ✅ MATCHED | Fee MDR 0,7% = Rp 10.010, booked as biaya |
| GrabFood | Rp 840.000 | Rp 672.000 | ✅ MATCHED | Komisi 20% = Rp 168.000, booked as biaya |
| Transfer | Rp 615.000 | Rp 615.000 | ✅ MATCHED | Exact |
| GoFood | Rp 1.250.000 | (none today) | 🟡 IN-TRANSIT | Settles H+1; expect ±Rp 1.000.000 net tomorrow (20% fee) |
| (bank only) "TRANSFER MASUK - NN" | — | Rp 50.000 | 🔴 UNEXPLAINED | No closing counterpart; ask admin, do NOT force to zero |
| CASH | Rp 2.150.000 | n/a | ℹ️ INFO | Physical cash, not bank-matched; reported for completeness |

Also: the Rp 15.000 "BIAYA ADM" debit is a bank cost, listed as info, never netted against sales.

## The three rules being demonstrated (Biks estate DNA)

1. Match on GROSS, book fees separately. Never compare net-to-gross and call the fee a "selisih".
2. Known settlement delays are 🟡 with an expected amount and date, auto-clearing when the credit lands.
3. Whatever remains is 🔴 and stays 🔴. The agent never forces the gap to zero; it asks a human. This is the anti-hallucination guarantee, in accounting form.

## One-line summary the agent should be able to give

"Dari omzet Rp 6.285.000 kemarin: omzet Rp 2.885.000 sudah cocok dengan rekening (bersih masuk Rp 2.706.990, biaya channel Rp 178.010), GoFood Rp 1.250.000 masih perjalanan (masuk besok ±Rp 1.000.000), cash Rp 2.150.000 pegang fisik, dan ada satu kredit Rp 50.000 yang tidak saya kenali, perlu dicek admin."
