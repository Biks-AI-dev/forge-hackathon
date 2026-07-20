# Demo documents the AI employee can read

Drop these into the agent chat with 📎 to show real document handling.

| File | What it is | What the bot does |
|---|---|---|
| `edc-receipt-kemang-20jul.png` | BCA EDC settlement slip (batch close), 1 branch, 1 day | Photo → OCR → Card **9.240.000** (Debit BCA + Visa + Mastercard summed), QRIS **2.940.000**, total **12.180.000** — matches the slip's own TOTAL |
| `pos-export-kemang-20jul.pdf` | POS Cloud daily sales export, same outlet/day | PDF text extracted (no OCR) → Cash 3.450.000 · QRIS 2.940.000 · Card 9.240.000 · GoFood 2.180.000 · GrabFood 1.320.000, total **19.130.000** — matches the export's TOTAL row |

Both end with the echo → reply "yes" → recorded ritual. Then ask
**"export to excel"** and the bot returns a real `.xlsx` (`/export.xlsx?sid=…`)
that opens in Excel with the same figures.

Deliberate traps in the data, all handled:
- The EDC slip says "SETTLEMENT" but is a *sales* document, not a bank statement.
- Card brands appear as `DEBIT BCA` / `CREDIT VISA` / `Card – Credit`, never as "CARD".
- The PDF's notes line contains a bank account number (`5271234567`) that must
  **not** be read as money — only separator-formatted figures count.
- Trx counts (12, 23, 34) sit next to the amounts and must not be mistaken for them.
