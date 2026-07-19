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
import json
import os
import re
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

def parse_channel_lines(text):
    """'QRIS: Rp 1.430.000' style lines → {CHANNEL: amount}"""
    out = {}
    # Spec channels PLUS the standard Indonesian set — a meeting that only
    # mentioned "card" must not produce an agent blind to Cash/QRIS/GoFood.
    known = sorted({c["name"].upper() for c in CHANNELS} | {"CASH", "QRIS", "GOFOOD", "GRABFOOD", "TRANSFER", "CARD"})
    alias = {"GOJEK": "GOFOOD", "GO FOOD": "GOFOOD", "GO-FOOD": "GOFOOD",
             "GRAB": "GRABFOOD", "TRANSFER BCA": "TRANSFER", "TF": "TRANSFER", "TUNAI": "CASH"}
    for line in text.splitlines():
        m = NUM_RE.search(line)
        if not m:
            continue
        head = line.split(":")[0].strip().upper()
        head = alias.get(head, head)
        for k in known:
            if k in head or head in k:
                out[k] = parse_amount(m.group(1))
                break
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
            fee_txt = f" (biaya {rp(fee)})" if fee else " — persis"
            verdicts.append(f"🟢 {name.title()} {rp(gross)} → masuk {rp(expect)}{fee_txt}")
        elif (c.get("settle_days") or 0) > 0:
            verdicts.append(f"🟡 {name.title()} {rp(gross)} belum masuk — normal H+{c['settle_days']}, "
                            f"kutunggu ±{rp(expect)}")
        else:
            verdicts.append(f"🔴 {name.title()} {rp(gross)} belum ketemu di mutasi — perlu dicek")
    for i, (desc, amt, is_cr) in enumerate(credits):
        if i in used:
            continue
        if is_cr:
            reds.append((desc.strip() or "kredit tanpa keterangan", amt))
            verdicts.append(f"🔴 Kredit {rp(amt)} \"{desc.strip()}\" tidak cocok dengan closing manapun")
        else:
            verdicts.append(f"ℹ️ Debit {rp(amt)} \"{desc.strip()}\" — biaya bank, bukan penjualan")
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

# ---------------------------------------------------------------- session brain
SESSIONS = {}

def brain(sid, msg):
    s = SESSIONS.setdefault(sid, {"greeted": False, "closing": None, "credits": None,
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
        return speak(f"Tercatat ✅ Sekarang kirim mutasi banknya ya 👇")

    looks_mutasi = any(k in text.upper() for k in
                       ("MUTASI", "SALDO", "KETERANGAN", "SETTLEMENT", "DISBURSE", ",CR", ",DB", "\"CR\"", "\"DB\""))
    ch = parse_channel_lines(text)

    if ch and len(ch) >= 2 and not looks_mutasi:
        s["pending_echo"] = ch
        lines = " · ".join(f"{k.title()} {rp(v)}" for k, v in ch.items())
        total = rp(sum(ch.values()))
        return speak(f"Aku echo dulu ya 👇\n{lines}\nTotal omzet {total}. Benar? Balas \"ya\" 👍")

    mut = parse_mutasi(text) if looks_mutasi else []
    if mut:
        s["credits"] = mut
        if s["closing"]:
            return run_recon(s)
        return speak(f"Mutasi kuterima ({len(mut)} baris). Sekarang kirim closing-nya ya 👇")

    # questions about a red item — REFUSE TO GUESS (the money moment)
    if s["reds"] and re.search(r"\b\d|itu apa|apa itu|kenapa|dari mana", text.lower()):
        d, amt = s["reds"][0]
        return speak(f"Jujur aku nggak tahu — dan aku nggak mau nebak. Kredit {rp(amt)} itu "
                     f"tidak cocok dengan closing manapun. Sudah kutandai 🔴 untuk dicek {ADMIN} ya.")

    if s["verdicts"] and re.search(r"gimana|hasil|selisih|cocok|status|kemarin", text.lower()):
        return speak(summary(s))

    # payment guardrail holds in EVERY workflow: never confirm before the human verifies
    if re.search(r"konfirm|sudah\s*(bayar|transfer|tf)|bukti|lunas|paid", text.lower()):
        return speak(f"Kalau soal konfirmasi pembayaran, itu wewenang {ADMIN} ya 🙏 "
                     f"Kucatat dulu, {ADMIN} yang verifikasi — aku tidak pernah mengonfirmasi sendiri.")

    return speak("Siap 🙌 Kirim closing per channel atau paste mutasi banknya, nanti kucocokkan. "
                 "Ketik \"panduan\" kalau mau lihat cara pakai lagi.")

def run_recon(s):
    verdicts, fee, reds = reconcile(s["closing"], s["credits"])
    s["verdicts"], s["reds"] = verdicts, reds
    return speak(summary(s, fee))

def summary(s, fee=None):
    body = "\n".join(s["verdicts"])
    fee_line = f"\nTotal biaya channel tercatat: {rp(fee)}." if fee else ""
    tail = f"\nYang 🔴 kutandai untuk {ADMIN} — aku tidak akan menebak penjelasannya."
    return f"Hasil rekonsiliasi:\n{body}{fee_line}{tail}"

def brain_sales(s, text):
    if PAY_RE.search(text):     # NEVER confirm an unverified payment
        return speak(f"Bukti transfernya kucatat ya 🙏 {OWNER} verifikasi dulu, "
                     f"baru pesananmu kukunci. Kukabari begitu terkonfirmasi ✅")
    it = find_item(text)
    if it:
        qty = parse_qty(text)
        total = qty * it["price"]           # computed in code, never by the model
        return speak(f"{qty} × {it['name']} = {rp(total)}. Kirim ke mana? 📍")
    if re.search(r"diskon|kurang|nego", text.lower()):
        return speak(f"Untuk harga khusus aku harus tanya {OWNER} dulu ya 🙏 Kuteruskan sekarang.")
    if re.search(r"menu|harga|list|apa aja", text.lower()):
        menu = "\n".join(f"• {c['name']} — {rp(c['price'])}" for c in CATALOG)
        return speak(f"Ini menunya 👇\n{menu}\nMau pesan yang mana?")
    if len(text) > 2:
        return speak(f"Hmm, itu di luar daftar menuku — kuteruskan ke {OWNER} ya 🙏 "
                     f"Sementara itu, ketik \"menu\" untuk lihat pilihan.")
    return speak("Mau pesan apa? 😊 Ketik \"menu\" untuk lihat pilihan.")

# ---------------------------------------------------------------- chat page
PAGE = """<!DOCTYPE html><html lang="id"><head><meta charset="UTF-8">
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
<form id="f"><button type="button" id="att" title="Kirim foto closing / mutasi / file">📎</button><input type="file" id="fi" accept="image/*,.txt,.csv" style="display:none"><input id="m" placeholder="Ketik pesan…" autocomplete="off" autofocus><button>➤</button></form>
<script>
const sid = sessionStorage.sid ||= (crypto.randomUUID ? crypto.randomUUID() : String(Math.random()));
const log = document.getElementById('log');
function add(cls, text){const d=document.createElement('div');d.className='b '+cls;d.textContent=text;log.appendChild(d);log.scrollTop=1e9;return d}
let pendingImg=null;
async function send(text, image){
  if(text||image){const d=add('me', text||'');
    if(image){const im=document.createElement('img');im.src=image;d.prepend(im);}}
  const t=document.createElement('div');t.className='typing';t.textContent='mengetik…';log.appendChild(t);log.scrollTop=1e9;
  try{
    const r=await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({session_id:sid,message:text,image})});
    const j=await r.json();t.remove();add('bot', j.reply||'(…)');
  }catch(e){t.remove();add('bot','⚠️ koneksi terputus, coba lagi');}
}
document.getElementById('f').onsubmit=e=>{e.preventDefault();const m=document.getElementById('m');const v=m.value.trim();if(!v&&!pendingImg)return;m.value='';const img=pendingImg;pendingImg=null;document.getElementById('att').textContent='📎';send(v,img)};
document.getElementById('att').onclick=()=>document.getElementById('fi').click();
document.getElementById('fi').onchange=()=>{
  const f=document.getElementById('fi').files[0];if(!f)return;
  if(f.type.startsWith('image/')){const rd=new FileReader();rd.onload=()=>{pendingImg=rd.result;document.getElementById('att').textContent='🖼️'};rd.readAsDataURL(f);}
  else{const rd=new FileReader();rd.onload=()=>{const m=document.getElementById('m');m.value=(m.value?m.value+'\\n':'')+rd.result.slice(0,4000)};rd.readAsText(f);}
  document.getElementById('fi').value='';
};
</script></body></html>"""
PAGE = PAGE.replace("__AGENT__", AGENT).replace("__BIZ__", BUSINESS)

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
        try:
            if image:
                extracted = ocr_image(str(image))
                if extracted is None:
                    return self._json(200, {"reply": (
                        "Aku belum bisa baca foto di sini 🙏 Ketik angkanya sebagai teks ya."
                        if LANG == "id" else
                        "I can't read photos here yet 🙏 Please type the numbers as text.")})
                message = (message + "\n" if message else "") + extracted
            reply = brain(sid, message)
        except Exception as exc:  # never die mid-demo
            reply = f"Maaf, ada kendala kecil di sisiku 🙏 Coba kirim ulang ya. ({type(exc).__name__})"
        self._json(200, {"reply": reply})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingTCPServer.allow_reuse_address = True
    with ThreadingTCPServer(("0.0.0.0", PORT), Handler) as httpd:
        print(f"{AGENT} for {BUSINESS} · workflow={WORKFLOW} · listening on {PORT}", flush=True)
        httpd.serve_forever()
