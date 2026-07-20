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
    return speak(summary(s, fee))

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
*{box-sizing:border-box;margin:0}body{font-family:'Segoe UI',system-ui,sans-serif;background:#EFEAE2;height:100dvh;display:flex;flex-direction:column}
header{background:#0F766E;color:#fff;padding:12px 16px}header b{font-size:15px;display:block}header small{opacity:.85;font-size:11.5px}
#log{flex:1;overflow-y:auto;padding:14px 12px;display:flex;flex-direction:column;gap:8px}
.b{max-width:86%;padding:8px 12px;border-radius:10px;font-size:14px;line-height:1.45;white-space:pre-wrap;box-shadow:0 1px 1px rgba(0,0,0,.08)}
.me{background:#D9FDD3;align-self:flex-end;border-top-right-radius:3px}
.bot{background:#fff;align-self:flex-start;border-top-left-radius:3px}
.typing{color:#888;font-size:12px;align-self:flex-start;padding:0 4px}
form{display:flex;gap:8px;padding:10px 12px;background:#F0F2F5}
input{flex:1;border:none;border-radius:20px;padding:11px 16px;font-size:14px;outline:none}
button{border:none;background:#0F766E;color:#fff;width:44px;height:44px;border-radius:50%;font-size:17px;cursor:pointer}\n#att{background:#fff;color:#54656F;border:1px solid #ddd}\n.b img{max-width:100%;border-radius:8px;display:block;margin-bottom:4px}
</style></head><body>
<header><b>__AGENT__</b><small>__BIZ__ · AI employee · online</small></header>
<div id="log"></div>
<form id="f"><button type="button" id="att" title="__ATTACH__">📎</button><input type="file" id="fi" accept="image/*,.pdf,.txt,.csv" style="display:none"><input id="m" placeholder="__PLACEHOLDER__" autocomplete="off" autofocus><button>➤</button></form>
<script>
const sid = sessionStorage.sid ||= (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
const log = document.getElementById('log');
function add(cls, text){const d=document.createElement('div');d.className='b '+cls;d.textContent=text;log.appendChild(d);log.scrollTop=1e9;return d}
let pendingImg=null,pendingFile=null;
async function send(text, image, file){
  if(text||image){const d=add('me', text||'');
    if(image){const im=document.createElement('img');im.src=image;d.prepend(im);}}
  const t=document.createElement('div');t.className='typing';t.textContent='__TYPING__';log.appendChild(t);log.scrollTop=1e9;
  try{
    const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid,message:text,image,file})});
    const j=await r.json();t.remove();add('bot', j.reply||'(…)');
  }catch(e){t.remove();add('bot','__OFFLINE__');}
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
</script></body></html>"""
PAGE = (PAGE.replace("__AGENT__", AGENT).replace("__BIZ__", BUSINESS)
            .replace("__LANG__", "en" if LANG == "en" else "id")
            .replace("__ATTACH__", T("ui_attach")).replace("__PLACEHOLDER__", T("ui_placeholder"))
            .replace("__TYPING__", T("ui_typing")).replace("__OFFLINE__", T("ui_offline")))

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
        if self.path == "/":
            body = PAGE.encode("utf-8")
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
