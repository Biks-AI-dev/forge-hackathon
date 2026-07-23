"""Biks Forge sandbox agent — the real one (PRD 4.2/4.3/4.4).

Contract (forge.manifest.json): python3 server.py · PORT env · GET /health ·
GET / (chat page) · POST /chat {session_id, message} → {reply}. spec.json in cwd.

Architecture = the Biks estate pattern: UNDERSTAND (regex first, cheap) →
DECIDE (pure deterministic code owns every number) → SPEAK (Kimi rephrases
the code-drafted reply IF KIMI_BASE_URL+KIMI_API_KEY exist AND every digit
survives verbatim; otherwise the draft is sent as-is, so the agent works
with zero keys).

The HALO greeting (PRD acceptance: "the halo test") is templated from
spec.json — name, painpoint, roles interpolated — never freestyle prose.
"""
import base64
import io
import json
import os
import re
import zipfile
import zlib
import urllib.request
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer

PORT = int(os.environ.get("PORT", "8000"))
KIMI_KEY = os.environ.get("KIMI_API_KEY") or ""
KIMI_URL = (os.environ.get("KIMI_BASE_URL") or "").rstrip("/")
KIMI_MODEL = os.environ.get("KIMI_MODEL", "kimi-k2-0905-preview")
KIMI_VISION_MODEL = os.environ.get("KIMI_VISION_MODEL", "")

# ---------------------------------------------------------------- spec loading
try:
    with open("spec.json", "r", encoding="utf-8") as f:
        SPEC = json.load(f)
except FileNotFoundError:
    SPEC = {}

def _get(d, *path, default=None):
    for k in path:
        if not isinstance(d, dict):
            return default
        d = d.get(k)
    return d if d is not None else default

BUSINESS = (SPEC.get("business_name")
            or _get(SPEC, "business", "name")
            or _get(SPEC, "products", "store", "name")
            or "bisnis Anda")
PERSONA = SPEC.get("persona") or {}
AGENT = PERSONA.get("agent_name") or (BUSINESS.split()[0] + " AI")
OWNER = PERSONA.get("owner_name") or "Owner"
ADMIN = PERSONA.get("admin_name") or "admin"
LANG = (PERSONA.get("language") or "id").lower()
CHANNELS = SPEC.get("channels") or []
CATEGORIES = _get(SPEC, "products", "categories", default=[]) or []
POLICY = SPEC.get("policy") or {}
WORKFLOW = SPEC.get("workflow") or ("recon" if CHANNELS else "sales")
# "chat" (default): the WhatsApp/Teams-style conversation page. "app": an
# app-shell UI — live dashboard + embedded assistant — for clients who asked
# for a dashboard/app instead of a chatbot. Same brain either way.
UI_MODE = (SPEC.get("ui_mode") or "chat").lower()
# Did this client ask for Excel? Then every recorded closing is written to a
# sheet immediately — they should never have to ask for it.
# Set in code by the Architect (llm.js validateForgeSpec) from what the client
# actually said; the regex is a fallback for hand-written specs.
WANTS_EXCEL = bool(SPEC.get("wants_excel")) or bool(
    re.search(r"excel|spreadsheet|xls|google sheet|spread sheet",
              json.dumps(SPEC, ensure_ascii=False), re.I))

# painpoint: the extractor may store it under several names (schema is extra=allow)
_pp = SPEC.get("pain_points")
if isinstance(_pp, list) and _pp:
    _pp = _pp[0]
PAIN = (SPEC.get("painpoint") or SPEC.get("pain_point") or _pp
        or SPEC.get("meeting_summary") or "")
if not PAIN:
    PAIN = (f"tiap pagi {ADMIN} harus cocokin mutasi bank sama closing outlet satu-satu, berjam-jam"
            if WORKFLOW == "recon" else
            "pesanan masuk lewat chat dan semuanya dicatat manual satu-satu")
PAIN = str(PAIN).strip().rstrip(".")

# flat catalogue: name/alias → (name, price)
CATALOG = []
for cat in CATEGORIES:
    for v in (cat.get("variants") or []):
        names = [v.get("name", "")] + (v.get("aliases") or [])
        CATALOG.append({"names": [n.lower() for n in names if n],
                        "name": v.get("name", "?"), "price": float(v.get("price") or 0)})

def rp(n):
    return "Rp " + f"{int(round(n)):,}".replace(",", ".")

# ---------------------------------------------------------------- i18n
# Every user-facing string exists in both languages. The deterministic draft
# must ALREADY be in the client's language: speak() (the LLM voice pass) is
# optional — skipped without keys, and rejected whenever a digit changes — so
# relying on it to translate leaked Bahasa into English deployments.
_STR = {
    "recorded_excel": {
        "id": "Tercatat ✅ Langsung kutulis ke Excel juga: {link}\n"
              "Kalau mau kucocokkan sama uang masuk, kirim mutasi banknya 👇",
        "en": "Recorded ✅ I've written it straight into Excel: {link}\n"
              "Send the bank statement whenever you want me to match it against money received 👇"},
    "recon_done_excel": {
        "id": "\nSudah kuperbarui juga di Excel: {link}",
        "en": "\nI've updated the Excel file too: {link}"},
    "recorded_send_mutasi": {
        "id": "Tercatat ✅ Sekarang kirim mutasi banknya ya 👇",
        "en": "Recorded ✅ Now send the bank statement 👇"},
    "echo_confirm": {
        "id": "Aku echo dulu ya 👇\n{lines}\nTotal omzet {total}. Benar? Balas \"ya\" 👍",
        "en": "Let me echo that back 👇\n{lines}\nTotal sales {total}. Correct? Reply \"yes\" 👍"},
    "mutasi_received": {
        "id": "Mutasi kuterima ({n} baris). Sekarang kirim closing-nya ya 👇",
        "en": "Bank statement received ({n} lines). Now send the closing 👇"},
    "refuse_to_guess": {
        "id": "Jujur aku nggak tahu — dan aku nggak mau nebak. Kredit {amt} itu tidak cocok "
              "dengan closing manapun. Sudah kutandai 🔴 untuk dicek {admin} ya.",
        "en": "Honestly I don't know — and I won't guess. The {amt} credit doesn't match any "
              "closing. I've flagged it 🔴 for {admin} to check."},
    "payment_guardrail": {
        "id": "Kalau soal konfirmasi pembayaran, itu wewenang {admin} ya 🙏 Kucatat dulu, "
              "{admin} yang verifikasi — aku tidak pernah mengonfirmasi sendiri.",
        "en": "Confirming a payment is {admin}'s call 🙏 I'll record it, {admin} verifies — "
              "I never confirm one myself."},
    "recon_idle": {
        "id": "Siap 🙌 Kirim closing per channel atau paste mutasi banknya, nanti kucocokkan. "
              "Ketik \"panduan\" kalau mau lihat cara pakai lagi.",
        "en": "Ready 🙌 Send the closing per channel, or paste the bank statement — I'll match "
              "them. Type \"guide\" to see how to use me again."},
    "recon_header": {"id": "Hasil rekonsiliasi:", "en": "Reconciliation result:"},
    "fee_total": {"id": "\nTotal biaya channel tercatat: {fee}.",
                  "en": "\nTotal channel fees booked: {fee}."},
    "recon_tail": {
        "id": "\nYang 🔴 kutandai untuk {admin} — aku tidak akan menebak penjelasannya.",
        "en": "\nThe 🔴 items are flagged for {admin} — I won't invent an explanation."},
    "v_matched": {"id": "🟢 {name} {gross} → masuk {expect}{fee}",
                  "en": "🟢 {name} {gross} → received {expect}{fee}"},
    "v_fee": {"id": " (biaya {fee})", "en": " (fee {fee})"},
    "v_exact": {"id": " — persis", "en": " — exact"},
    "v_transit": {"id": "🟡 {name} {gross} belum masuk — normal H+{days}, kutunggu ±{expect}",
                  "en": "🟡 {name} {gross} not in yet — normal on D+{days}, expecting ±{expect}"},
    "v_missing": {"id": "🔴 {name} {gross} belum ketemu di mutasi — perlu dicek",
                  "en": "🔴 {name} {gross} not found in the statement — needs a check"},
    "v_orphan_credit": {"id": "🔴 Kredit {amt} \"{desc}\" tidak cocok dengan closing manapun",
                        "en": "🔴 Credit {amt} \"{desc}\" matches no closing"},
    "v_debit": {"id": "ℹ️ Debit {amt} \"{desc}\" — biaya bank, bukan penjualan",
                "en": "ℹ️ Debit {amt} \"{desc}\" — bank charge, not a sale"},
    "unlabelled_credit": {"id": "kredit tanpa keterangan", "en": "credit with no description"},
    "sales_payment_proof": {
        "id": "Bukti transfernya kucatat ya 🙏 {owner} verifikasi dulu, baru pesananmu kukunci. "
              "Kukabari begitu terkonfirmasi ✅",
        "en": "I've noted your transfer proof 🙏 {owner} verifies first, then I lock your order in. "
              "I'll let you know once it's confirmed ✅"},
    "sales_line_total": {"id": "{qty} × {item} = {total}. Kirim ke mana? 📍",
                         "en": "{qty} × {item} = {total}. Where should we deliver? 📍"},
    "sales_discount": {"id": "Untuk harga khusus aku harus tanya {owner} dulu ya 🙏 Kuteruskan sekarang.",
                       "en": "Special pricing is {owner}'s call 🙏 Passing it on now."},
    "sales_menu": {"id": "Ini menunya 👇\n{menu}\nMau pesan yang mana?",
                   "en": "Here's the menu 👇\n{menu}\nWhat would you like?"},
    "sales_offmenu": {
        "id": "Hmm, itu di luar daftar menuku — kuteruskan ke {owner} ya 🙏 Sementara itu, "
              "ketik \"menu\" untuk lihat pilihan.",
        "en": "Hmm, that's off my menu — passing it to {owner} 🙏 Meanwhile, type \"menu\" "
              "to see the options."},
    "sales_idle": {"id": "Mau pesan apa? 😊 Ketik \"menu\" untuk lihat pilihan.",
                   "en": "What would you like to order? 😊 Type \"menu\" to see the options."},
    "no_text_in_file": {
        "id": "File-nya kebaca tapi kosong 🙏 Kalau itu hasil scan, kirim sebagai foto saja ya.",
        "en": "I opened the file but found no text 🙏 If it's a scan, send it as a photo instead."},
    "excel_ready": {
        "id": "Sudah aku rapikan ke Excel 👇 {link} — tinggal buka, angkanya sama persis dengan yang di atas.",
        "en": "I've put it into Excel for you 👇 {link} — open it, the figures match exactly what's above."},
    "no_vision": {"id": "Aku belum bisa baca foto di sini 🙏 Ketik angkanya sebagai teks ya.",
                  "en": "I can't read photos here yet 🙏 Please type the numbers as text."},
    "glitch": {"id": "Maaf, ada kendala kecil di sisiku 🙏 Coba kirim ulang ya. ({err})",
               "en": "Sorry, small glitch on my side 🙏 Please send that again. ({err})"},
    "ui_placeholder": {"id": "Ketik pesan…", "en": "Type a message…"},
    "ui_typing": {"id": "mengetik…", "en": "typing…"},
    "ui_reading_photo": {"id": "membaca foto", "en": "reading the photo"},
    "ui_reading_doc": {"id": "membaca dokumen", "en": "reading the document"},
    "ui_offline": {"id": "⚠️ koneksi terputus, coba lagi", "en": "⚠️ connection lost, try again"},
    "ui_attach": {"id": "Kirim foto closing / mutasi / file", "en": "Send a closing / statement photo or file"},
}

def T(key, **kw):
    return _STR[key]["en" if LANG == "en" else "id"].format(**kw)


# ---------------------------------------------------------------- halo greeting
def halo_greeting_en():
    if WORKFLOW == "recon":
        chans = ", ".join(c["name"].title() for c in CHANNELS) or "all channels"
        fee_notes = []
        for c in CHANNELS:
            if c.get("fee_rate"):
                pct = f"{c['fee_rate']*100:g}%"
                tag = " (assumed, correct me)" if c.get("assumed") else ""
                fee_notes.append(f"{c['name'].title()} {pct}{tag}")
        fee_line = ("Channel fees I book: " + ", ".join(fee_notes) + ".\n") if fee_notes else ""
        return (
            f"Hi {OWNER}! 🙌 I'm {AGENT} — the reconciliation assistant for {BUSINESS}.\n"
            f"I know the pain: \"{PAIN}.\" That's my job now: read the closings & the bank statement, "
            f"match sales ↔️ money in, and only flag what truly needs a human.\n\n"
            f"How to use me:\n"
            f"👩‍🍳 Staff — send the closing at end of shift (per channel: {chans}). "
            f"I read it → echo the numbers → you reply \"yes\" → recorded ✅\n"
            f"💼 {ADMIN.title()} — send the bank statement each morning (paste is fine). "
            f"I match everything, including channel fees and late settlements.\n"
            f"{fee_line}"
            f"Status legend: 🟢 matched · 🟡 in transit · 🔴 needs a check\n\n"
            f"Want to try? Send yesterday's closing 👇"
        )
    menu = "\n".join(f"• {c['name']} — {rp(c['price'])}" for c in CATALOG[:6]) or "• (menu coming)"
    return (
        f"Hi! 🙌 I'm {AGENT}, the order assistant for {BUSINESS}.\n"
        f"I know the pain: \"{PAIN}.\" From now on: send orders as usual — I record them in full, "
        f"compute totals, and track payments. {OWNER} gives the final confirmation.\n\n"
        f"Menu:\n{menu}\n\n"
        f"My standing rules: prices only from the list, I never confirm a payment "
        f"without {OWNER}'s verification, anything off-menu goes to {OWNER}.\n\n"
        f"What would you like? 😊"
    )

def halo_greeting():
    if LANG == "en":
        return halo_greeting_en()
    if WORKFLOW == "recon":
        chans = ", ".join(c["name"].title() for c in CHANNELS) or "semua channel"
        fee_notes = []
        for c in CHANNELS:
            if c.get("fee_rate"):
                pct = f"{c['fee_rate']*100:g}%"
                tag = " (asumsi, bisa dikoreksi)" if c.get("assumed") else ""
                fee_notes.append(f"{c['name'].title()} {pct}{tag}")
        fee_line = ("Biaya channel yang kupakai: " + ", ".join(fee_notes) + ".\n") if fee_notes else ""
        return (
            f"Halo {OWNER}! 🙌 Aku {AGENT} — teman rekonsiliasi {BUSINESS}.\n"
            f"Aku tahu masalahnya: \"{PAIN}.\" Mulai sekarang itu tugasku: baca closing & mutasi, "
            f"cocokin omset ↔️ uang masuk di bank, dan cuma lapor kalau ada yang beneran perlu dicek.\n\n"
            f"Cara pakai:\n"
            f"👩‍🍳 SPG — kirim closing tiap akhir shift (ketik ringkasannya, per channel: {chans}). "
            f"Aku baca → aku echo angkanya → balas \"ya\" → tercatat ✅\n"
            f"💼 {ADMIN.title()} — kirim mutasi bank tiap pagi (paste saja isinya). "
            f"Aku cocokkan, termasuk biaya channel dan settlement yang telat.\n"
            f"{fee_line}"
            f"Arti status: 🟢 cocok · 🟡 masih di perjalanan · 🔴 perlu dicek\n\n"
            f"Mau coba sekarang? Kirim closing kemarin 👇"
        )
    # sales
    menu = "\n".join(f"• {c['name']} — {rp(c['price'])}" for c in CATALOG[:6]) or "• (menu menyusul)"
    return (
        f"Halo! 🙌 Aku {AGENT}, asisten pesanan {BUSINESS}.\n"
        f"Aku tahu masalahnya: \"{PAIN}.\" Mulai sekarang: kirim pesanan seperti biasa — aku catat lengkap, "
        f"hitung total, dan cek pembayaran. {OWNER} yang konfirmasi akhir.\n\n"
        f"Menu:\n{menu}\n\n"
        f"Aturan yang selalu kujaga: harga hanya dari daftar, pembayaran tidak pernah "
        f"kukonfirmasi tanpa verifikasi {OWNER}, hal di luar menu kuteruskan ke {OWNER}.\n\n"
        f"Mau pesan apa? 😊"
    )

# ---------------------------------------------------------------- understanding
GREET_RE = re.compile(r"^\s*(halo+|hai|hi|hello+|hallo+|pagi|siang|sore|malam|tes|test|p)\s*[!.?]*\s*$", re.I)
GUIDE_RE = re.compile(r"cara\s*pakai|panduan|gimana\s*pakai|how\s*to|bantuan|help", re.I)
YES_RE = re.compile(r"^\s*(ya|yes|iya|yup|ok|oke|betul|benar|sip|correct)\s*[!.]*\s*$", re.I)
PAY_RE = re.compile(r"transfer|bayar|bukti|sudah\s*kirim|udah\s*tf|lunas|paid", re.I)
NUM_RE = re.compile(r"([0-9][0-9.,]{2,})")

def parse_amount(s):
    s = s.strip()
    s = re.sub(r"[.,]\d{1,2}$", "", s)      # drop decimal tail: 1,419,990.00 → 1,419,990
    s = s.replace(".", "").replace(",", "")
    try:
        return int(s)
    except ValueError:
        return 0

# Substring aliases: real documents say "DEBIT BCA", "CREDIT VISA", "Card –
# Credit" — never the bare channel name the spec uses.
# Longest / most specific first — "GRABFOOD" must win over "GRAB", and the
# literal channel names must be present, not just their nicknames.
CHANNEL_ALIASES = [
    ("GRABFOOD", "GRABFOOD"), ("GRAB FOOD", "GRABFOOD"), ("GRAB", "GRABFOOD"),
    ("GOFOOD", "GOFOOD"), ("GO FOOD", "GOFOOD"), ("GO-FOOD", "GOFOOD"), ("GOJEK", "GOFOOD"),
    ("QRIS", "QRIS"), ("QR ", "QRIS"),
    ("CASH", "CASH"), ("TUNAI", "CASH"),
    ("TRANSFER", "TRANSFER"), ("TF ", "TRANSFER"),
    ("MASTERCARD", "CARD"), ("MASTER", "CARD"), ("DEBIT", "CARD"), ("CREDIT", "CARD"),
    ("KREDIT", "CARD"), ("VISA", "CARD"), ("KARTU", "CARD"), ("CARD", "CARD"),
]
MONEY_RE = re.compile(r"\d{1,3}(?:[.,]\d{3})+")
SKIP_ROWS = ("TOTAL", "SUBTOTAL", "NET SETTLED", "MDR", "FEE", "MID", "TID", "BATCH",
             "REFUND", "GRAND", "SUMMARY")

def parse_channel_lines(text):
    """Closing / EDC slip / POS export lines -> {CHANNEL: amount}.
    Same channel on several lines (Debit BCA + Credit Visa) is summed."""
    out = {}
    known = {c["name"].upper() for c in CHANNELS} | {"CASH", "QRIS", "GOFOOD", "GRABFOOD", "TRANSFER", "CARD"}
    for line in text.splitlines():
        up = line.upper()
        # the label sits before the numbers; totals/fees are not channels
        label = re.split(r"[0-9]", up)[0]
        if any(sk in label for sk in SKIP_ROWS):
            continue
        # A channel row is a short label + figures. Prose ("Card settlements
        # land T+1 at BCA a/c 5271234567...") must never be read as a row —
        # that account number was being booked as nine billion rupiah.
        if len(label.split()) > 4:
            continue
        # Money is written with thousand separators (4.250.000); a bare digit
        # run is a trx count, an account or a reference number, not an amount.
        nums = MONEY_RE.findall(line)
        if not nums:
            continue
        chan = next((c for token, c in CHANNEL_ALIASES if token in label and c in known), None)
        if not chan:
            continue
        out[chan] = out.get(chan, 0) + max(parse_amount(n) for n in nums)
    return out

def parse_mutasi(text):
    """bank CSV/paste → [(description, amount, is_credit)]"""
    rows = []
    for line in text.splitlines():
        u = line.upper()
        if not NUM_RE.search(line):
            continue
        if "TANGGAL" in u and "KETERANGAN" in u:
            continue  # header row
        # csv-aware split: respect quoted cells so "1,419,990.00" stays one cell
        cells = [c.strip().strip('"') for c in re.findall(r'"[^"]*"|[^,]+', line)]
        marker = next((i for i, c in enumerate(cells) if c in ("CR", "DB")), None)
        if marker is not None and marker > 0:
            amount = parse_amount(cells[marker - 1])          # BCA shape: amount,DB/CR,balance
            is_credit = cells[marker] == "CR"
        else:
            nums = [parse_amount(x) for x in NUM_RE.findall(line)]
            nums = [n for n in nums if n >= 1000]
            if not nums:
                continue
            amount = nums[-2] if len(nums) >= 2 else nums[0]
            is_credit = " CR " in u or u.rstrip().endswith("CR")
        if amount < 1000:
            continue
        desc = ""
        for c in cells:
            letters = re.sub(r"[^A-Za-z\- ]", "", c).strip()
            if len(letters) > len(desc):
                desc = letters
        rows.append((desc[:60], amount, is_credit))
    return rows

# ---------------------------------------------------------------- recon decide()
def reconcile(closing, credits):
    verdicts, used = [], set()
    kw = {"QRIS": ["QRIS"], "GOFOOD": ["GOFOOD", "GOJEK", "GO-FOOD"],
          "GRABFOOD": ["GRAB"], "TRANSFER": ["TRSF", "TRANSFER", "E-BANKING"]}
    total_fee = 0
    reds = []
    for c in CHANNELS:
        name = c["name"].upper()
        gross = closing.get(name)
        if gross is None:
            continue
        if c.get("hits_bank") is False or name == "CASH":
            verdicts.append(f"ℹ️ {name.title()} {rp(gross)} — uang fisik, tidak lewat bank")
            continue
        fee = round(gross * (c.get("fee_rate") or 0))
        expect = gross - fee
        hit = None
        # pass 1: keyword + amount, pass 2: amount only
        for want_kw in (True, False):
            if hit is not None:
                break
            for i, (desc, amt, is_cr) in enumerate(credits):
                if i in used or not is_cr:
                    continue
                desc_ok = any(k in desc.upper() for k in kw.get(name, [name]))
                if abs(amt - expect) <= 2 and (desc_ok or not want_kw):
                    hit = i
                    break
        if hit is not None:
            used.add(hit)
            total_fee += fee
            fee_txt = T("v_fee", fee=rp(fee)) if fee else T("v_exact")
            verdicts.append(T("v_matched", name=name.title(), gross=rp(gross), expect=rp(expect), fee=fee_txt))
        elif (c.get("settle_days") or 0) > 0:
            verdicts.append(T("v_transit", name=name.title(), gross=rp(gross),
                              days=c["settle_days"], expect=rp(expect)))
        else:
            verdicts.append(T("v_missing", name=name.title(), gross=rp(gross)))
    for i, (desc, amt, is_cr) in enumerate(credits):
        if i in used:
            continue
        if is_cr:
            reds.append((desc.strip() or T("unlabelled_credit"), amt))
            verdicts.append(T("v_orphan_credit", amt=rp(amt), desc=desc.strip()))
        else:
            verdicts.append(T("v_debit", amt=rp(amt), desc=desc.strip()))
    return verdicts, total_fee, reds

# ---------------------------------------------------------------- sales decide()
def find_item(text):
    t = text.lower()
    best = None
    for it in CATALOG:
        for n in it["names"]:
            if n and n in t and (best is None or len(n) > best[0]):
                best = (len(n), it)
    return best[1] if best else None

def parse_qty(text):
    m = re.search(r"\b(\d{1,3})\s*(x|pcs|porsi|buah|box)?\b", text.lower())
    return int(m.group(1)) if m else 1

# ---------------------------------------------------------------- speak (Kimi voice, token-safe)
def speak(draft):
    if not (KIMI_KEY and KIMI_URL):
        return draft
    try:
        req = urllib.request.Request(
            KIMI_URL + "/chat/completions",
            data=json.dumps({
                "model": KIMI_MODEL, "temperature": 0.3, "max_tokens": 700,
                "messages": [
                    {"role": "system",
                     "content": f"Kamu {AGENT}, asisten {BUSINESS}. Tulis ulang pesan berikut agar hangat dan natural "
                                f"dalam bahasa {'Indonesia' if LANG == 'id' else 'English'}, TANPA mengubah satu angka, "
                                f"emoji status, atau fakta pun. Balas HANYA pesannya."},
                    {"role": "user", "content": draft}],
            }).encode(),
            headers={"Authorization": f"Bearer {KIMI_KEY}", "Content-Type": "application/json",
                     "User-Agent": "biks-forge-agent/1.0"})
        with urllib.request.urlopen(req, timeout=12) as r:
            out = json.load(r)["choices"][0]["message"]["content"].strip()
        need = re.findall(r"\d[\d.,]*", draft)
        if out and all(tok in out for tok in need):
            return out
    except Exception:
        pass
    return draft

# ------------------------------------------------------- free-form LLM (adaptive)
# The deterministic paths above own every calculation, the confirm ritual and
# the payment guardrail — those run FIRST and never reach the model. This is
# the fallback for everything the workflow templates don't cover (invoices,
# "what can you do", client-specific asks), so one forged employee adapts to
# the customer instead of dead-ending in a canned line.
#
# Money safety is kept by construction: the model is given the code-computed
# FACTS and told it may state a figure ONLY if it appears there. A reply that
# introduces an unknown number is discarded and the deterministic line is used.
def llm_freeform(s, text, fallback):
    if not (KIMI_KEY and KIMI_URL):
        return fallback

    facts = []
    if s.get("closing"):
        facts.append("Closing recorded: " + ", ".join(f"{k.title()} {rp(v)}" for k, v in s["closing"].items()))
        facts.append("Closing total: " + rp(sum(s["closing"].values())))
    if s.get("verdicts"):
        facts.append("Latest reconciliation:\n" + "\n".join(s["verdicts"]))
    if CATALOG:
        facts.append("Price list: " + ", ".join(f"{c['name']} {rp(c['price'])}" for c in CATALOG))
    if CHANNELS:
        facts.append("Payment channels: " + ", ".join(
            f"{c['name'].title()}" + (f" (fee {c['fee_rate'] * 100:g}%)" if c.get("fee_rate") else "")
            for c in CHANNELS))
    facts_block = "\n".join(facts) or "(no figures recorded in this conversation yet)"

    # NOTE: keep every f-string expression on ONE line — multi-line expressions
    # inside f-strings are Python 3.12+ only and the deploy target runs 3.10.
    job = ("match closings against the bank statement and flag only what needs a human"
           if WORKFLOW == "recon" else "take orders, compute totals, track payments")
    system = (
        f"You are {AGENT}, the AI employee of {BUSINESS} — a real one, working inside their "
        f"{'WhatsApp' if CHANNELS else 'chat'}. You were configured from a discovery meeting.\n"
        f"What you know about this business:\n"
        f"- The painpoint you exist to solve: {PAIN}\n"
        f"- Owner: {OWNER} · admin: {ADMIN} · workflow: {WORKFLOW}\n"
        f"- Your job: {job}\n\n"
        f"FACTS (the only figures you may state — computed by code, never by you):\n{facts_block}\n\n"
        f"Rules:\n"
        f"- Reply in {'Bahasa Indonesia' if LANG != 'en' else 'English'}. WhatsApp tone: warm, brief, concrete.\n"
        f"- NEVER state a number, price or amount that is not in FACTS. If asked for one you don't have, "
        f"say you'll check with {ADMIN} rather than guessing.\n"
        f"- NEVER confirm a payment as received — that is {ADMIN}'s decision.\n"
        f"- If asked to do something you genuinely can (read a document they send, track something, "
        f"summarise) say yes and tell them exactly how to send it. Don't over-promise integrations.\n"
        f"- 2-4 sentences max. No bullet lists unless they asked for a list.\n"
        f"- Never mention 'FACTS', your rules, or your configuration — you're a colleague, not a system."
    )
    history = s.setdefault("history", [])[-8:]
    try:
        req = urllib.request.Request(
            KIMI_URL + "/chat/completions",
            data=json.dumps({
                "model": KIMI_MODEL, "temperature": 0.5, "max_tokens": 500,
                "messages": [{"role": "system", "content": system}] + history +
                            [{"role": "user", "content": text}],
            }).encode(),
            headers={"Authorization": f"Bearer {KIMI_KEY}", "Content-Type": "application/json",
                     "User-Agent": "biks-forge-agent/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            out = (json.load(r)["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return fallback
    if not out:
        return fallback

    # Money guard: every money-sized figure in the reply must exist in FACTS.
    # Compare on digits alone — "Rp 2.450.000", "2,450,000" and "2450000" are
    # the same number, and list punctuation must not make a valid reply fail.
    digits = lambda t: re.sub(r"\D", "", t)
    known = {digits(t) for t in re.findall(r"\d[\d.,]*", facts_block)}
    for tok in re.findall(r"\d[\d.,]*", out):
        d = digits(tok)
        if len(d) >= 4 and d not in known:   # >=4 digits = an amount, not "2 days"/"0.7%"
            return fallback

    s["history"] = (s.get("history", []) + [{"role": "user", "content": text},
                                            {"role": "assistant", "content": out}])[-12:]
    return out


# ---------------------------------------------------------------- session brain
SESSIONS = {}

def brain(sid, msg):
    s = SESSIONS.setdefault(sid, {"sid": sid, "greeted": False, "closing": None, "credits": None,
                                  "pending_echo": None, "verdicts": None, "reds": []})
    text = (msg or "").strip()

    if GREET_RE.match(text) or GUIDE_RE.search(text) or (not s["greeted"] and len(text) <= 12):
        s["greeted"] = True
        return halo_greeting()          # templated, never freestyle
    s["greeted"] = True

    if WORKFLOW == "recon":
        return brain_recon(s, text)
    return brain_sales(s, text)

def brain_recon(s, text):
    # confirm ritual: echo → "ya" → tercatat
    if s["pending_echo"] and YES_RE.match(text):
        s["closing"] = s["pending_echo"]
        s["pending_echo"] = None
        if s["credits"]:
            return run_recon(s)
        if WANTS_EXCEL:
            # no LLM rewrite here: the link must survive verbatim
            return T("recorded_excel", link=xlsx_link(s))
        return speak(T("recorded_send_mutasi"))

    up = text.upper()
    # An EDC settlement slip / POS export is the SALES side (a closing), even
    # though it says "SETTLEMENT" — check it first or it looks like a statement.
    looks_edc = any(k in up for k in
                    ("EDC", "BATCH CLOSE", "MERCHANT COPY", "MID ", "TID ", "TRACE NO",
                     "POS EXPORT", "SALES REPORT", "DAILY SALES", "PAYMENT METHOD"))
    looks_mutasi = (not looks_edc) and any(k in up for k in
                    ("MUTASI", "SALDO", "KETERANGAN", "REKENING KORAN", "ACCOUNT STATEMENT",
                     "DISBURSE", ",CR", ",DB", "\"CR\"", "\"DB\""))
    ch = parse_channel_lines(text)

    if ch and len(ch) >= 2 and not looks_mutasi:
        s["pending_echo"] = ch
        lines = " · ".join(f"{k.title()} {rp(v)}" for k, v in ch.items())
        total = rp(sum(ch.values()))
        return speak(T("echo_confirm", lines=lines, total=total))

    mut = parse_mutasi(text) if looks_mutasi else []
    if mut:
        s["credits"] = mut
        if s["closing"]:
            return run_recon(s)
        return speak(T("mutasi_received", n=len(mut)))

    # questions about a red item — REFUSE TO GUESS (the money moment)
    if s["reds"] and re.search(r"\b\d|itu apa|apa itu|kenapa|dari mana", text.lower()):
        d, amt = s["reds"][0]
        return speak(T("refuse_to_guess", amt=rp(amt), admin=ADMIN))

    if s["verdicts"] and re.search(r"gimana|hasil|selisih|cocok|status|kemarin", text.lower()):
        return speak(summary(s))

    # payment guardrail holds in EVERY workflow: never confirm before the human verifies
    if re.search(r"konfirm|sudah\s*(bayar|transfer|tf)|bukti|lunas|paid", text.lower()):
        return speak(T("payment_guardrail", admin=ADMIN))

    if re.search(r"excel|xls|spreadsheet|spreadsheet|unduh|download|export", text.lower()):
        if s.get("closing"):
            return T("excel_ready", link=f"/export.xlsx?sid={s.get('sid','web')}")
        return llm_freeform(s, text, speak(T("recon_idle")))

    return llm_freeform(s, text, speak(T("recon_idle")))

def run_recon(s):
    verdicts, fee, reds = reconcile(s["closing"], s["credits"])
    s["verdicts"], s["reds"] = verdicts, reds
    out = speak(summary(s, fee))
    if WANTS_EXCEL:
        out += T("recon_done_excel", link=xlsx_link(s))
    return out

def xlsx_link(s):
    return f"/export.xlsx?sid={s.get('sid', 'web')}"

def summary(s, fee=None):
    body = "\n".join(s["verdicts"])
    fee_line = T("fee_total", fee=rp(fee)) if fee else ""
    tail = T("recon_tail", admin=ADMIN)
    return f"{T('recon_header')}\n{body}{fee_line}{tail}"

def brain_sales(s, text):
    if PAY_RE.search(text):     # NEVER confirm an unverified payment
        return speak(T("sales_payment_proof", owner=OWNER))
    it = find_item(text)
    if it:
        qty = parse_qty(text)
        total = qty * it["price"]           # computed in code, never by the model
        return speak(T("sales_line_total", qty=qty, item=it["name"], total=rp(total)))
    if re.search(r"diskon|kurang|nego", text.lower()):
        return speak(T("sales_discount", owner=OWNER))
    if re.search(r"menu|harga|list|apa aja", text.lower()):
        menu = "\n".join(f"• {c['name']} — {rp(c['price'])}" for c in CATALOG)
        return speak(T("sales_menu", menu=menu))
    if len(text) > 2:
        return llm_freeform(s, text, speak(T("sales_offmenu", owner=OWNER)))
    return llm_freeform(s, text, speak(T("sales_idle")))

# ---------------------------------------------------------------- chat page
PAGE = """<!DOCTYPE html><html lang="__LANG__"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__AGENT__ · __BIZ__</title><style>
*{box-sizing:border-box;margin:0}
html,body{height:100dvh}
body{font-family:'Segoe UI',system-ui,sans-serif;display:flex;flex-direction:column;overflow:hidden}
#shell{flex:1;display:flex;min-height:0}
.main{flex:1;display:flex;flex-direction:column;min-width:0}
#log{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:8px}
.b{max-width:86%;padding:8px 12px;border-radius:10px;font-size:14px;line-height:1.45;white-space:pre-wrap}
.me{align-self:flex-end}.bot{align-self:flex-start}
.b img{max-width:100%;border-radius:8px;display:block;margin-bottom:4px}
.typing{font-size:12px;align-self:flex-start;padding:0 4px}
form{display:flex;gap:8px;align-items:center}
#fi{display:none}
svg{width:20px;height:20px;stroke:currentColor;stroke-width:2;fill:none;stroke-linecap:round;stroke-linejoin:round}

/* ---- demo skin switcher (obviously a control, not part of either UI) ---- */
#skinbar{position:fixed;top:9px;right:14px;z-index:50;display:flex;align-items:center;gap:4px;
  background:rgba(20,20,20,.6);backdrop-filter:blur(6px);padding:4px 5px 4px 10px;border-radius:20px;
  box-shadow:0 2px 10px rgba(0,0,0,.35);font-size:12px;color:#fff}
#skinbar span{opacity:.65;margin-right:2px}
#skinbar button{border:none;background:transparent;color:#fff;opacity:.55;padding:5px 12px;border-radius:14px;cursor:pointer;font-size:12px;font-weight:600}
#skinbar button.on{background:#fff;color:#151515;opacity:1}

/* ===================== WHATSAPP SKIN ===================== */
body[data-skin="wa"]{background:#EFEAE2}
body[data-skin="wa"] .tm-rail,body[data-skin="wa"] .tm-list,body[data-skin="wa"] .tm-head,body[data-skin="wa"] .tm-only{display:none!important}
body[data-skin="wa"] .wa-head{background:#0F766E;color:#fff;padding:12px 16px}
body[data-skin="wa"] .wa-head b{font-size:15px;display:block}
body[data-skin="wa"] .wa-head small{opacity:.85;font-size:11.5px}
body[data-skin="wa"] #log{padding:14px 12px;background:#EFEAE2}
body[data-skin="wa"] .b{box-shadow:0 1px 1px rgba(0,0,0,.08)}
body[data-skin="wa"] .me{background:#D9FDD3;border-top-right-radius:3px}
body[data-skin="wa"] .bot{background:#fff;border-top-left-radius:3px}
body[data-skin="wa"] .typing{color:#888}
body[data-skin="wa"] form{padding:10px 12px;background:#F0F2F5}
body[data-skin="wa"] #m{flex:1;border:none;border-radius:20px;padding:11px 16px;font-size:14px;outline:none}
body[data-skin="wa"] #send{border:none;background:#0F766E;color:#fff;width:44px;height:44px;border-radius:50%;font-size:17px;cursor:pointer}
body[data-skin="wa"] #att{background:#fff;color:#54656F;border:1px solid #ddd;width:44px;height:44px;border-radius:50%;cursor:pointer;font-size:17px}

/* ===================== MICROSOFT TEAMS SKIN ===================== */
body[data-skin="teams"]{background:#1f1f1f;color:#e6e6e6}
body[data-skin="teams"] .wa-head{display:none}
body[data-skin="teams"] .tm-ava{width:32px;height:32px;flex:0 0 32px;border-radius:8px;background:linear-gradient(135deg,#4f52c9,#7b83eb);
  display:flex;align-items:center;justify-content:center;color:#fff;font-size:15px}
/* left app rail */
body[data-skin="teams"] .tm-rail{display:flex;flex-direction:column;align-items:center;gap:2px;width:60px;background:#2b2b2b;padding:8px 0;border-right:1px solid #000}
body[data-skin="teams"] .tm-railbtn{position:relative;width:44px;height:44px;border:none;background:transparent;color:#c3c3c3;border-radius:6px;
  display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;cursor:pointer;font-size:9px}
body[data-skin="teams"] .tm-railbtn:hover{background:#383838}
body[data-skin="teams"] .tm-railbtn.on{color:#fff}
body[data-skin="teams"] .tm-railbtn.on::before{content:"";position:absolute;left:-8px;top:11px;bottom:11px;width:3px;border-radius:3px;background:#7b83eb}
/* chat list */
body[data-skin="teams"] .tm-list{display:flex;flex-direction:column;width:290px;background:#1b1a1a;border-right:1px solid #000;min-width:0}
body[data-skin="teams"] .tm-list-head{padding:14px 14px 8px;display:flex;align-items:center;justify-content:space-between}
body[data-skin="teams"] .tm-list-head b{font-size:19px}
body[data-skin="teams"] .tm-chips{display:flex;gap:6px;padding:0 14px 10px}
body[data-skin="teams"] .tm-chip{font-size:12px;color:#d0d0d0;background:#2d2c2c;border:1px solid #3a3a3a;border-radius:14px;padding:3px 11px}
body[data-skin="teams"] .tm-sec{font-size:11px;color:#8a8a8a;padding:6px 14px 2px;text-transform:uppercase;letter-spacing:.4px}
body[data-skin="teams"] .tm-item{display:flex;align-items:center;gap:10px;padding:8px 12px;margin:0 6px;border-radius:6px;cursor:pointer}
body[data-skin="teams"] .tm-item:hover{background:#232222}
body[data-skin="teams"] .tm-item.on{background:#2d2c2c}
body[data-skin="teams"] .tm-item .nm{font-size:14px;color:#f3f3f3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
body[data-skin="teams"] .tm-item .pv{font-size:12px;color:#9a9a9a;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
body[data-skin="teams"] .tm-item .meta{margin-left:auto;font-size:11px;color:#8a8a8a}
body[data-skin="teams"] .tm-dot{width:8px;height:8px;border-radius:50%;background:#7b83eb;margin-left:auto}
/* header */
body[data-skin="teams"] .tm-head{display:flex;align-items:center;gap:12px;height:52px;padding:0 16px;background:#1f1f1f;border-bottom:1px solid #000}
body[data-skin="teams"] .tm-head .nm{font-size:15px;font-weight:600;color:#fff}
body[data-skin="teams"] .tm-tabs{display:flex;gap:18px;margin-left:8px}
body[data-skin="teams"] .tm-tabs span{font-size:14px;color:#b8b8b8;padding:16px 0;cursor:pointer}
body[data-skin="teams"] .tm-tabs span.on{color:#fff;box-shadow:inset 0 -2px 0 #7b83eb}
body[data-skin="teams"] .tm-head-ico{margin-left:auto;display:flex;gap:6px;color:#c3c3c3}
body[data-skin="teams"] .tm-head-ico button{width:34px;height:34px;border:none;background:transparent;color:inherit;border-radius:6px;cursor:pointer;display:grid;place-items:center}
body[data-skin="teams"] .tm-head-ico button:hover{background:#333}
/* messages */
body[data-skin="teams"] #log{padding:18px 22px 10px;background:#1f1f1f;gap:14px}
body[data-skin="teams"] .b{box-shadow:none;border-radius:8px}
body[data-skin="teams"] .me{background:#5b5fc7;color:#fff}
body[data-skin="teams"] .bot{background:#2d2c2c;color:#eaeaea;position:relative;margin-left:44px}
body[data-skin="teams"] .bot::before{content:"➤";position:absolute;left:-44px;top:0;width:32px;height:32px;border-radius:8px;
  background:linear-gradient(135deg,#4f52c9,#7b83eb);display:flex;align-items:center;justify-content:center;color:#fff;font-size:14px}
body[data-skin="teams"] .typing{color:#9a9a9a;margin-left:44px}
/* compose box */
body[data-skin="teams"] form{margin:8px 16px 16px;padding:6px 6px 6px 10px;background:#2d2c2c;border:1px solid #3d3c3c;border-radius:8px;gap:4px}
body[data-skin="teams"] #m{order:0;flex:1;background:transparent;border:none;color:#e9e9e9;outline:none;font-size:14px;padding:7px 4px}
body[data-skin="teams"] #m::placeholder{color:#9a9a9a}
body[data-skin="teams"] .tm-compose{order:1;display:flex;gap:2px;color:#c3c3c3}
body[data-skin="teams"] .tm-compose button{width:32px;height:32px;border:none;background:transparent;color:inherit;border-radius:6px;cursor:pointer;display:grid;place-items:center}
body[data-skin="teams"] .tm-compose button:hover{background:#3a3a3a}
body[data-skin="teams"] #att{order:2;background:transparent;border:none;color:#c3c3c3;width:32px;height:32px;border-radius:6px;cursor:pointer;font-size:15px}
body[data-skin="teams"] #att:hover{background:#3a3a3a}
body[data-skin="teams"] #send{order:3;background:transparent;border:none;color:#7b83eb;width:34px;height:34px;border-radius:6px;cursor:pointer;font-size:16px}
body[data-skin="teams"] #send:hover{background:#3a3a3a}
@media(max-width:820px){body[data-skin="teams"] .tm-list{display:none}}
@media(max-width:560px){body[data-skin="teams"] .tm-rail{display:none}}
</style></head><body data-skin="wa">
<div id="skinbar"><span>Preview:</span><button data-skin-btn="wa">WhatsApp</button><button data-skin-btn="teams">Teams</button></div>
<div id="shell">
  <nav class="tm-rail tm-only">
    <button class="tm-railbtn"><svg viewBox="0 0 24 24"><circle cx="5" cy="5" r="1.4"/><circle cx="12" cy="5" r="1.4"/><circle cx="19" cy="5" r="1.4"/><circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/><circle cx="5" cy="19" r="1.4"/><circle cx="12" cy="19" r="1.4"/><circle cx="19" cy="19" r="1.4"/></svg>Apps</button>
    <button class="tm-railbtn"><svg viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>Activity</button>
    <button class="tm-railbtn on"><svg viewBox="0 0 24 24"><path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.9 9.9 0 0 1-4-.9L3 21l1.9-4.9a8.4 8.4 0 0 1-.9-4A8.4 8.4 0 0 1 12.5 3 8.4 8.4 0 0 1 21 11.5z"/></svg>Chat</button>
    <button class="tm-railbtn"><svg viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>Calendar</button>
    <button class="tm-railbtn"><svg viewBox="0 0 24 24"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3 19.5 19.5 0 0 1-6-6 19.8 19.8 0 0 1-3-8.6A2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7 12.8 12.8 0 0 0 .7 2.8 2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4 12.8 12.8 0 0 0 2.8.7 2 2 0 0 1 1.7 2z"/></svg>Calls</button>
  </nav>
  <aside class="tm-list tm-only">
    <div class="tm-list-head"><b>Chat</b></div>
    <div class="tm-chips"><span class="tm-chip">Unread</span><span class="tm-chip">Channels</span><span class="tm-chip">Chats</span></div>
    <div class="tm-sec">Chats</div>
    <div class="tm-item on"><span class="tm-ava">➤</span><div style="min-width:0"><div class="nm">__AGENT__</div><div class="pv">Yep, I'm here 👋</div></div><span class="tm-dot"></span></div>
    <div class="tm-item"><span class="tm-ava" style="background:#6c4a9c">OW</span><div style="min-width:0"><div class="nm">Owner (WA)</div><div class="pv">Thanks, noted 👍</div></div><span class="meta">Tue</span></div>
    <div class="tm-item"><span class="tm-ava" style="background:#3a7d5d">FN</span><div style="min-width:0"><div class="nm">Finance</div><div class="pv">Reconciliation done</div></div><span class="meta">Mon</span></div>
  </aside>
  <main class="main">
    <header class="wa-head"><b>__AGENT__</b><small>__BIZ__ · AI employee · online</small></header>
    <header class="tm-head tm-only">
      <span class="tm-ava">➤</span>
      <span class="nm">__AGENT__</span>
      <nav class="tm-tabs"><span class="on">Chat</span><span>Shared</span></nav>
      <div class="tm-head-ico">
        <button title="Call"><svg viewBox="0 0 24 24"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3 19.5 19.5 0 0 1-6-6 19.8 19.8 0 0 1-3-8.6A2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7 12.8 12.8 0 0 0 .7 2.8 2 2 0 0 1-.5 2.1L8.1 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4 12.8 12.8 0 0 0 2.8.7 2 2 0 0 1 1.7 2z"/></svg></button>
        <button title="Search"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg></button>
        <button title="More"><svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/></svg></button>
      </div>
    </header>
    <div id="log"></div>
    <form id="f">
      <button type="button" id="att" title="__ATTACH__">📎</button>
      <input type="file" id="fi" accept="image/*,.pdf,.txt,.csv">
      <input id="m" placeholder="__PLACEHOLDER__" autocomplete="off" autofocus>
      <span class="tm-compose tm-only">
        <button type="button" title="Format"><svg viewBox="0 0 24 24"><path d="M4 7V5h16v2M9 5v14M7 19h4"/></svg></button>
        <button type="button" title="Emoji"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><path d="M9 9h.01M15 9h.01"/></svg></button>
      </span>
      <button id="send">➤</button>
    </form>
  </main>
</div>
<script>
const sid = sessionStorage.sid ||= (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
const log = document.getElementById('log');
function add(cls, text){
  const d=document.createElement('div');d.className='b '+cls;
  // linkify the export link (textNodes only — never innerHTML with model output)
  const re=/((?:https?:[/][/]|[/]export[.]xlsx)[^ ]+)/g; let last=0,m;
  while((m=re.exec(text))){
    d.appendChild(document.createTextNode(text.slice(last,m.index)));
    const a=document.createElement('a');a.href=m[1];a.target='_blank';
    a.textContent=m[1].indexOf('export.xlsx')>-1?'📊 Download Excel':m[1];
    a.style.cssText='color:#0F766E;font-weight:600;text-decoration:underline';
    d.appendChild(a); last=m.index+m[1].length;
  }
  d.appendChild(document.createTextNode(text.slice(last)));
  log.appendChild(d);log.scrollTop=1e9;return d}
let pendingImg=null,pendingFile=null;
async function send(text, image, file){
  if(text||image){const d=add('me', text||'');
    if(image){const im=document.createElement('img');im.src=image;d.prepend(im);}}
  const t=document.createElement('div');t.className='typing';
  const label=image?'__READPHOTO__':file?'__READDOC__':'__TYPING__';
  t.textContent=label;log.appendChild(t);log.scrollTop=1e9;
  // reading a photo runs ~9s on the vision model — show it is working
  const t0=Date.now();
  const tick=setInterval(()=>{t.textContent=label+' '+((Date.now()-t0)/1000).toFixed(0)+'s';},1000);
  try{
    const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid,message:text,image,file})});
    const j=await r.json();clearInterval(tick);t.remove();add('bot', j.reply||'(…)');
  }catch(e){clearInterval(tick);t.remove();add('bot','__OFFLINE__');}
}
document.getElementById('f').onsubmit=e=>{e.preventDefault();const m=document.getElementById('m');const v=m.value.trim();if(!v&&!pendingImg&&!pendingFile)return;m.value='';const img=pendingImg,fl=pendingFile;pendingImg=pendingFile=null;document.getElementById('att').textContent='📎';send(v,img,fl)};
document.getElementById('att').onclick=()=>document.getElementById('fi').click();
document.getElementById('fi').onchange=()=>{
  const f=document.getElementById('fi').files[0];if(!f)return;
  if(f.type.startsWith('image/')){
    // Phone photos are 3-6MB; downscale to ≤1600px JPEG so OCR is fast and
    // never rejected for size.
    const img=new Image();img.onload=()=>{
      const k=Math.min(1,1600/Math.max(img.width,img.height));
      const c=document.createElement('canvas');c.width=img.width*k;c.height=img.height*k;
      c.getContext('2d').drawImage(img,0,0,c.width,c.height);
      pendingImg=c.toDataURL('image/jpeg',0.85);document.getElementById('att').textContent='🖼️';
      URL.revokeObjectURL(img.src);
    };img.src=URL.createObjectURL(f);
  }
  else{const rd=new FileReader();rd.onload=()=>{pendingFile=rd.result;document.getElementById('att').textContent='📄'};rd.readAsDataURL(f);}
  document.getElementById('fi').value='';
};
// ---- skin switcher: same chat, two chromes (WhatsApp / MS Teams) ----
const skinBtns=document.querySelectorAll('#skinbar [data-skin-btn]');
function setSkin(s){document.body.dataset.skin=s;try{sessionStorage.skin=s}catch(e){}
  skinBtns.forEach(b=>b.classList.toggle('on',b.dataset.skinBtn===s));log.scrollTop=1e9;}
skinBtns.forEach(b=>b.onclick=()=>setSkin(b.dataset.skinBtn));
setSkin((()=>{try{return sessionStorage.skin}catch(e){}})()||'wa');
</script></body></html>"""
PAGE = (PAGE.replace("__AGENT__", AGENT).replace("__BIZ__", BUSINESS)
            .replace("__LANG__", "en" if LANG == "en" else "id")
            .replace("__ATTACH__", T("ui_attach")).replace("__PLACEHOLDER__", T("ui_placeholder"))
            .replace("__TYPING__", T("ui_typing")).replace("__OFFLINE__", T("ui_offline"))
            .replace("__READPHOTO__", T("ui_reading_photo")).replace("__READDOC__", T("ui_reading_doc")))

# ------------------------------------------------------------- app-shell page
# ui_mode == "app": the deliverable is an app, not a chatbot. Left rail +
# live dashboard cards (all numbers come from GET /state — code-owned session
# state, never model output) with the assistant docked on the right. The
# assistant reuses the exact same POST /chat as the chat page; after every
# reply the dashboard re-fetches /state so recorded closings / verdicts /
# totals appear in the cards the moment the brain records them.
PAGE_APP = """<!DOCTYPE html><html lang="__LANG__"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__AGENT__ · __BIZ__</title><style>
*{box-sizing:border-box;margin:0}html,body{height:100dvh}
body{font-family:'Segoe UI',system-ui,sans-serif;background:#111418;color:#E6E9ED;display:flex;flex-direction:column;overflow:hidden}
#top{display:flex;align-items:center;gap:12px;padding:10px 18px;background:#171B21;border-bottom:1px solid #232932}
#top .logo{width:34px;height:34px;border-radius:9px;background:linear-gradient(135deg,#0F766E,#14B8A6);display:grid;place-items:center;font-size:16px}
#top b{font-size:15px}#top small{color:#8B95A3;font-size:11.5px;display:block}
#top .pill{margin-left:auto;font-size:11px;color:#5EEAD4;background:rgba(20,184,166,.12);border:1px solid rgba(20,184,166,.35);padding:4px 10px;border-radius:12px}
#wrap{flex:1;display:flex;min-height:0}
#rail{width:190px;background:#171B21;border-right:1px solid #232932;padding:14px 10px;display:flex;flex-direction:column;gap:2px}
#rail a{color:#B7C0CC;text-decoration:none;font-size:13px;padding:9px 12px;border-radius:8px;display:flex;gap:9px;align-items:center}
#rail a.on{background:#232932;color:#fff}
#rail a:hover{background:#1D222A}
#rail .sec{font-size:10.5px;color:#5B6675;text-transform:uppercase;letter-spacing:.6px;padding:14px 12px 4px}
#dash{flex:1;overflow-y:auto;padding:20px;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;align-content:start}
.card{background:#171B21;border:1px solid #232932;border-radius:12px;padding:16px}
.card h3{font-size:12px;color:#8B95A3;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px}
.card .big{font-size:22px;font-weight:600}
.card .muted{color:#5B6675;font-size:13px}
.card table{width:100%;border-collapse:collapse;font-size:13px}
.card td{padding:6px 2px;border-bottom:1px solid #1E242C}
.card td:last-child{text-align:right;font-variant-numeric:tabular-nums}
.tag{font-size:10px;color:#FBBF24;border:1px solid rgba(251,191,36,.4);border-radius:8px;padding:1px 6px;margin-left:6px}
.v-line{font-size:13px;padding:5px 0;border-bottom:1px solid #1E242C;white-space:pre-wrap}
.card a{color:#5EEAD4}
#side{width:340px;display:flex;flex-direction:column;background:#141920;border-left:1px solid #232932;min-width:0}
#side header{padding:12px 14px;border-bottom:1px solid #232932;font-size:13px;display:flex;gap:8px;align-items:center}
#side header .dot{width:8px;height:8px;border-radius:50%;background:#34D399}
#log{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
.b{max-width:92%;padding:7px 11px;border-radius:9px;font-size:13px;line-height:1.45;white-space:pre-wrap}
.me{background:#0F766E;color:#fff;align-self:flex-end}
.bot{background:#232932;align-self:flex-start}
.b img{max-width:100%;border-radius:8px;display:block;margin-bottom:4px}
.typing{color:#5B6675;font-size:12px;align-self:flex-start;padding:0 4px}
form{display:flex;gap:6px;padding:10px;border-top:1px solid #232932}
#m{flex:1;background:#1D222A;border:1px solid #2A313B;border-radius:8px;color:#E6E9ED;padding:9px 12px;font-size:13px;outline:none;resize:none;font-family:inherit;max-height:110px}
button{border:none;background:#0F766E;color:#fff;border-radius:8px;padding:0 14px;font-size:15px;cursor:pointer}
#att{background:#1D222A;border:1px solid #2A313B;color:#8B95A3}
#fi{display:none}
@media(max-width:900px){#rail{display:none}}
@media(max-width:700px){#side{width:100%;position:fixed;inset:0;display:none}}
</style></head><body>
<header id="top"><span class="logo">◆</span><div><b>__BIZ__</b><small>__AGENT__ · AI operations app</small></div><span class="pill">● live</span></header>
<div id="wrap">
  <nav id="rail">
    <span class="sec">Workspace</span>
    <a class="on" href="#">▦ Dashboard</a>
    <a href="#" onclick="document.getElementById('m').focus();return false">✦ Assistant</a>
    <a href="#" id="nav-export" style="display:none">⬇ Excel export</a>
  </nav>
  <main id="dash"></main>
  <aside id="side">
    <header><span class="dot"></span><b>__AGENT__</b>&nbsp;<span style="color:#5B6675">assistant</span></header>
    <div id="log"></div>
    <form id="f"><button type="button" id="att" title="__ATTACH__">📎</button><input type="file" id="fi" accept="image/*,.pdf,.txt,.csv"><textarea id="m" rows="1" placeholder="__PLACEHOLDER__" autocomplete="off" autofocus></textarea><button>➤</button></form>
  </aside>
</div>
<script>
const sid = sessionStorage.sid ||= (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
const log = document.getElementById('log');
const dash = document.getElementById('dash');
function add(cls, text){
  const d=document.createElement('div');d.className='b '+cls;
  const re=/((?:https?:[/][/]|[/]export[.]xlsx)[^ ]+)/g; let last=0,m;
  while((m=re.exec(text))){
    d.appendChild(document.createTextNode(text.slice(last,m.index)));
    const a=document.createElement('a');a.href=m[1];a.target='_blank';
    a.textContent=m[1].indexOf('export.xlsx')>-1?'📊 Excel':m[1];
    a.style.cssText='color:#5EEAD4;font-weight:600';
    d.appendChild(a); last=m.index+m[1].length;
  }
  d.appendChild(document.createTextNode(text.slice(last)));
  log.appendChild(d);log.scrollTop=1e9;return d}
// ---- dashboard: rendered ONLY from /state (code-owned numbers) ----
function card(title){const c=document.createElement('div');c.className='card';
  const h=document.createElement('h3');h.textContent=title;c.appendChild(h);dash.appendChild(c);return c}
function renderState(st){
  dash.textContent='';
  if(st.workflow==='recon'){
    const c1=card('Today\\u2019s closing');
    if(st.closing){
      const t=document.createElement('table');
      for(const [k,v] of Object.entries(st.closing)){
        const tr=t.insertRow();tr.insertCell().textContent=k;tr.insertCell().textContent=v.toLocaleString('id-ID');}
      const tr=t.insertRow();tr.insertCell().innerHTML='<b>Total</b>';
      tr.insertCell().innerHTML='<b>'+st.closing_total.toLocaleString('id-ID')+'</b>';
      c1.appendChild(t);
    } else {const p=document.createElement('div');p.className='muted';p.textContent=st.empty_closing;c1.appendChild(p);}
    const c2=card('Reconciliation');
    if(st.verdicts&&st.verdicts.length){
      for(const v of st.verdicts){const d=document.createElement('div');d.className='v-line';d.textContent=v;c2.appendChild(d);}
    } else {const p=document.createElement('div');p.className='muted';p.textContent=st.empty_verdicts;c2.appendChild(p);}
    const c3=card('Channels');
    const t3=document.createElement('table');
    for(const ch of st.channels||[]){
      const tr=t3.insertRow();const td=tr.insertCell();td.textContent=ch.name;
      if(ch.assumed){const s=document.createElement('span');s.className='tag';s.textContent='assumed';td.appendChild(s);}
      tr.insertCell().textContent=ch.fee_rate!=null?(ch.fee_rate*100).toFixed(1)+'%':'—';}
    c3.appendChild(t3);
  } else {
    const c1=card('Catalog');
    const t=document.createElement('table');
    for(const it of st.catalog||[]){
      const tr=t.insertRow();tr.insertCell().textContent=it.name;
      tr.insertCell().textContent=it.price.toLocaleString('id-ID');}
    if(!(st.catalog||[]).length){const p=document.createElement('div');p.className='muted';p.textContent='—';c1.appendChild(p);}
    c1.appendChild(t);
  }
  const cg=card('Guardrails (enforced in code)');
  for(const g of st.guardrails||[]){const d=document.createElement('div');d.className='v-line';d.textContent='🛡 '+g;cg.appendChild(d);}
  if(!(st.guardrails||[]).length){const p=document.createElement('div');p.className='muted';p.textContent='—';cg.appendChild(p);}
  const ex=document.getElementById('nav-export');
  if(st.excel){ex.style.display='flex';ex.href=st.excel;}else{ex.style.display='none';}
}
async function refreshState(){
  try{const r=await fetch('/state?sid='+encodeURIComponent(sid));renderState(await r.json());}catch(e){}
}
let pendingImg=null,pendingFile=null;
async function send(text, image, file){
  if(text||image){const d=add('me', text||'');
    if(image){const im=document.createElement('img');im.src=image;d.prepend(im);}}
  const t=document.createElement('div');t.className='typing';
  const label=image?'__READPHOTO__':file?'__READDOC__':'__TYPING__';
  t.textContent=label;log.appendChild(t);log.scrollTop=1e9;
  const t0=Date.now();
  const tick=setInterval(()=>{t.textContent=label+' '+((Date.now()-t0)/1000).toFixed(0)+'s';},1000);
  try{
    const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid,message:text,image,file})});
    const j=await r.json();clearInterval(tick);t.remove();add('bot', j.reply||'(…)');
    refreshState();
  }catch(e){clearInterval(tick);t.remove();add('bot','__OFFLINE__');}
}
document.getElementById('f').onsubmit=e=>{e.preventDefault();const m=document.getElementById('m');const v=m.value.trim();if(!v&&!pendingImg&&!pendingFile)return;m.value='';const img=pendingImg,fl=pendingFile;pendingImg=pendingFile=null;document.getElementById('att').textContent='📎';send(v,img,fl)};
// Multi-line paste matters here (a closing is one channel per line):
// Enter sends, Shift+Enter makes a newline.
document.getElementById('m').addEventListener('keydown',e=>{
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();document.getElementById('f').requestSubmit();}
});
document.getElementById('att').onclick=()=>document.getElementById('fi').click();
document.getElementById('fi').onchange=()=>{
  const f=document.getElementById('fi').files[0];if(!f)return;
  if(f.type.startsWith('image/')){
    const img=new Image();img.onload=()=>{
      const k=Math.min(1,1600/Math.max(img.width,img.height));
      const c=document.createElement('canvas');c.width=img.width*k;c.height=img.height*k;
      c.getContext('2d').drawImage(img,0,0,c.width,c.height);
      pendingImg=c.toDataURL('image/jpeg',0.85);document.getElementById('att').textContent='🖼️';
      URL.revokeObjectURL(img.src);
    };img.src=URL.createObjectURL(f);
  }
  else{const rd=new FileReader();rd.onload=()=>{pendingFile=rd.result;document.getElementById('att').textContent='📄'};rd.readAsDataURL(f);}
  document.getElementById('fi').value='';
};
refreshState();
</script></body></html>"""
PAGE_APP = (PAGE_APP.replace("__AGENT__", AGENT).replace("__BIZ__", BUSINESS)
            .replace("__LANG__", "en" if LANG == "en" else "id")
            .replace("__ATTACH__", T("ui_attach")).replace("__PLACEHOLDER__", T("ui_placeholder"))
            .replace("__TYPING__", T("ui_typing")).replace("__OFFLINE__", T("ui_offline"))
            .replace("__READPHOTO__", T("ui_reading_photo")).replace("__READDOC__", T("ui_reading_doc")))


def app_state(sid):
    """Dashboard data for the app UI — every number is code-owned session
    state or spec data; nothing here ever comes from model output."""
    s = SESSIONS.get(sid) or {}
    state = {
        "workflow": WORKFLOW,
        "agent": AGENT,
        "business": BUSINESS,
        "guardrails": (POLICY.get("guardrails") or [])[:8],
        "empty_closing": "Belum ada closing tercatat" if LANG != "en" else "No closing recorded yet",
        "empty_verdicts": "Kirim closing + mutasi untuk rekonsiliasi" if LANG != "en"
                          else "Send a closing + statement to reconcile",
        "excel": None,
    }
    if WORKFLOW == "recon":
        state["channels"] = [
            {"name": (c.get("name") or "?").title(), "fee_rate": c.get("fee_rate"),
             "assumed": bool(c.get("assumed"))}
            for c in CHANNELS if isinstance(c, dict)
        ]
        closing = s.get("closing")
        state["closing"] = closing
        state["closing_total"] = sum(closing.values()) if closing else 0
        state["verdicts"] = s.get("verdicts") or []
        if closing:
            state["excel"] = f"/export.xlsx?sid={sid}"
    else:
        state["catalog"] = [{"name": c["name"], "price": c["price"]} for c in CATALOG]
    return state

# ---------------------------------------------------------------- PDF text

def _decode_stream(raw):
    """Standard filter chains in machine-generated exports: ASCII85+Flate
    (reportlab), Flate alone, or none."""
    b = raw.strip()
    for attempt in (lambda: zlib.decompress(base64.a85decode(b, adobe=True)),
                    lambda: zlib.decompress(b),
                    lambda: base64.a85decode(b, adobe=True),
                    lambda: b):
        try:
            out = attempt()
            if b"Tj" in out or b"TJ" in out:
                return out
        except Exception:
            continue
    return b""

def _unescape(b):
    b = re.sub(rb"\\([0-7]{1,3})", lambda m: bytes([int(m.group(1), 8) & 0xFF]), b)
    b = re.sub(rb"\\([nrtbf])", lambda m: {b"n": b"\n", b"r": b"", b"t": b" ",
                                           b"b": b"", b"f": b""}[m.group(1)], b)
    return re.sub(rb"\\(.)", rb"\1", b)

def pdf_text(data):
    """Text out of a machine-generated PDF (POS / accounting exports) using only
    the stdlib — the agent ships into a sandbox with no pip. Fragments are
    grouped back into rows by their y position and ordered by x, so a table row
    stays one line: 'Cash 34 3.450.000 0 3.450.000'."""
    items = []  # (page, -y, x, text)
    for page, sm in enumerate(re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S)):
        chunk = _decode_stream(sm.group(1))
        if not chunk:
            continue
        x = y = 0.0
        for op in re.finditer(
            rb"(-?[\d.]+)\s+(-?[\d.]+)\s+T[dD]"
            rb"|(?:[-\d.]+\s+){4}(-?[\d.]+)\s+(-?[\d.]+)\s+Tm"
            rb"|(\((?:\\.|[^\\()])*\))\s*Tj"
            rb"|(\[(?:[^\[\]\\]|\\.)*\])\s*TJ", chunk):
            if op.group(1):
                x, y = float(op.group(1)), float(op.group(2))
            elif op.group(3):
                x, y = float(op.group(3)), float(op.group(4))
            elif op.group(5):
                items.append((page, -y, x, _unescape(op.group(5)[1:-1]).decode("latin-1", "replace")))
            elif op.group(6):
                parts = re.findall(rb"\((?:\\.|[^\\()])*\)", op.group(6))
                items.append((page, -y, x, "".join(_unescape(p[1:-1]).decode("latin-1", "replace")
                                                   for p in parts)))
    lines, row, key = [], [], None
    for page, negy, x, text in sorted(items):
        k = (page, round(negy / 3))          # ~3pt tolerance = same visual row
        if key is not None and k != key:
            lines.append(" ".join(t for _, t in sorted(row)))
            row = []
        key, _ = k, row.append((x, text))
    if row:
        lines.append(" ".join(t for _, t in sorted(row)))
    return "\n".join(l.strip() for l in lines if l.strip())


# ---------------------------------------------------------------- XLSX export
def build_xlsx(rows):
    """A real .xlsx from the stdlib (it is a zip of XML) — the client asked for
    Excel, so the agent hands them a file Excel opens, not a screenshot."""
    def esc(v):
        return (str(v).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    sheet = ['<?xml version="1.0" encoding="UTF-8"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for r, row in enumerate(rows, 1):
        cells = []
        for c, val in enumerate(row):
            ref = f"{chr(65 + c)}{r}"
            if isinstance(val, (int, float)):
                cells.append(f'<c r="{ref}"><v>{val}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{esc(val)}</t></is></c>')
        sheet.append(f'<row r="{r}">' + "".join(cells) + "</row>")
    sheet.append("</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="Reconciliation" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   "</Relationships>")
        z.writestr("xl/worksheets/sheet1.xml", "".join(sheet))
    return buf.getvalue()

def recon_rows(s):
    """The reconciliation as spreadsheet rows — same numbers the chat showed."""
    rows = [["Channel", "Sales (closing)", "Status"]]
    for k, v in (s.get("closing") or {}).items():
        status = next((v2 for v2 in (s.get("verdicts") or []) if k.title() in v2), "")
        rows.append([k.title(), v, re.sub(r"[🟢🟡🔴]", "", status).strip() or "recorded"])
    if s.get("closing"):
        rows.append(["TOTAL", sum(s["closing"].values()), ""])
    return rows


# ---------------------------------------------------------------- image OCR
def ocr_image(data_url):
    """Photo of a closing / bank statement -> the text & numbers in it.
    Uses the vision model when configured; plain refusal otherwise, so the
    agent still works with zero keys."""
    if not (KIMI_KEY and KIMI_URL and KIMI_VISION_MODEL):
        return None
    body = json.dumps({
        "model": KIMI_VISION_MODEL,
        "temperature": 0.1,
        "max_tokens": 800,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text":
             "Read this business document photo (closing/receipt/bank statement). "
             "Transcribe ALL text and numbers exactly as written, line by line. Output only the transcription."},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
    }).encode("utf-8")
    req = urllib.request.Request(
        KIMI_URL + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + KIMI_KEY,
                 "User-Agent": "biks-forge-agent/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    return (data["choices"][0]["message"]["content"] or "").strip()


# ---------------------------------------------------------------- http server
class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/export.xlsx"):
            sid = self.path.partition("sid=")[2] or "web"
            rows = recon_rows(SESSIONS.get(sid) or {})
            if len(rows) <= 1:
                return self._json(404, {"error": "nothing recorded yet"})
            body = build_xlsx(rows)
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            self.send_header("Content-Disposition", 'attachment; filename="reconciliation.xlsx"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/health":
            return self._json(200, {"status": "ok"})
        if self.path.startswith("/state"):
            sid = self.path.partition("sid=")[2] or "web"
            return self._json(200, app_state(sid))
        if self.path == "/":
            body = (PAGE_APP if UI_MODE == "app" else PAGE).encode("utf-8", "replace")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/chat":
            return self._json(404, {"error": "not_found"})
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}
        sid = str(payload.get("session_id") or self.client_address[0])
        message = str(payload.get("message") or "")
        image = payload.get("image")
        doc = payload.get("file")          # data URL: PDF / txt / csv
        try:
            if doc:
                head, _, b64 = str(doc).partition(",")
                raw = base64.b64decode(b64 or "")
                extracted = pdf_text(raw) if "pdf" in head.lower() else raw.decode("utf-8", "replace")
                if not extracted.strip():
                    return self._json(200, {"reply": T("no_text_in_file")})
                message = (message + "\n" if message else "") + extracted
            elif image:
                extracted = ocr_image(str(image))
                if extracted is None:
                    return self._json(200, {"reply": T("no_vision")})
                message = (message + "\n" if message else "") + extracted
            reply = brain(sid, message)
        except Exception as exc:  # never die mid-demo
            reply = T("glitch", err=type(exc).__name__)
        self._json(200, {"reply": reply})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingTCPServer.allow_reuse_address = True
    with ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"{AGENT} for {BUSINESS} · workflow={WORKFLOW} · listening on {PORT}", flush=True)
        httpd.serve_forever()
