"""Acceptance tests for the real agent-template (run: python3 test_agent.py).
Covers the PRD halo test, the recon fixture verdicts, and the 5 skipped
agent-behavior tests from GATE2_REHEARSAL_REPORT.md. No network needed.
"""
import importlib
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent

RECON_SPEC = {
    "workflow": "recon",
    "persona": {"agent_name": "Nusa", "language": "id",
                "owner_name": "Pak Dharma", "admin_name": "Mbak Sari"},
    "business": {"name": "Dapoer Nusantara", "outlets": ["DN1", "DN2"], "bank": "BCA"},
    "painpoint": "tiap pagi Mbak Sari habis 2-3 jam cocokin mutasi BCA sama closing SPG dari 2 outlet",
    "channels": [
        {"name": "CASH", "hits_bank": False},
        {"name": "QRIS", "fee_rate": 0.007, "settle_days": 1, "assumed": True},
        {"name": "GOFOOD", "fee_rate": 0.20, "settle_days": 2, "assumed": True},
        {"name": "GRABFOOD", "fee_rate": 0.20, "settle_days": 1, "assumed": True},
        {"name": "TRANSFER", "fee_rate": 0.0, "settle_days": 0},
    ],
}

SALES_SPEC = {
    "workflow": "sales",
    "persona": {"agent_name": "Sari AI", "language": "id", "owner_name": "Bu Sari"},
    "painpoint": "pesanan masuk lewat WA dan admin salah catat, bukti transfer dicek satu-satu",
    "products": {"store": {"name": "Sari's Catering"},
                 "categories": [{"name": "Nasi Box", "variants": [
                     {"id": "NB", "name": "Nasi Box Ayam Bakar", "price": 35000, "aliases": ["ayam bakar"]},
                     {"id": "TM", "name": "Tumpeng Mini", "price": 150000, "aliases": ["tumpeng"]},
                 ]}]},
}

def load_agent(spec):
    os.chdir(HERE)
    with open("spec.json", "w", encoding="utf-8") as f:
        json.dump(spec, f)
    for m in list(sys.modules):
        if m == "server":
            del sys.modules[m]
    sys.path.insert(0, str(HERE))
    import server
    importlib.reload(server)
    return server

def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        raise SystemExit(f"FAILED: {name}: {detail}")

# ---------------- recon path ----------------
srv = load_agent(RECON_SPEC)
halo = srv.brain("t1", "halo")
check("halo: greets by name", "Pak Dharma" in halo and "Nusa" in halo and "Dapoer Nusantara" in halo)
check("halo: names the painpoint unprompted", "2-3 jam" in halo or "cocokin mutasi" in halo)
check("halo: role-by-role panduan", "SPG" in halo and "Mbak Sari" in halo)
check("halo: echo->ya ritual + status legend", "\"ya\"" in halo and "🟢" in halo and "🔴" in halo)
check("halo: not generic", "asisten AI" not in halo.lower())

closing = (ROOT / "test-data/recon/closing-DN1-16jul.txt").read_text(encoding="utf-8")
mutasi = (ROOT / "test-data/recon/mutasi-BCA-17jul.csv").read_text(encoding="utf-8")

echo = srv.brain("t1", closing)
check("closing: echo before commit", "echo" in echo.lower() and "6.285.000" in echo, echo[:200])
ok = srv.brain("t1", "ya")
check("closing: tercatat after ya", "Tercatat" in ok, ok[:120])
res = srv.brain("t1", mutasi)
check("verdict: QRIS matched w/ fee 10.010", "1.419.990" in res and "10.010" in res, res)
check("verdict: Grab matched w/ fee 168.000", "672.000" in res and "168.000" in res, res)
check("verdict: Transfer exact", "615.000" in res, res)
check("verdict: GoFood amber ±1.000.000", "🟡" in res and "1.000.000" in res, res)
check("verdict: 50.000 red", "🔴" in res and "50.000" in res, res)
check("verdict: cash info", "2.150.000" in res, res)
check("verdict: total fees 178.010", "178.010" in res, res)
refuse = srv.brain("t1", "yang 50 ribu itu apa?")
check("50k: refuses to guess", "nggak mau nebak" in refuse or "tidak mau menebak" in refuse, refuse)
check("50k: flags for admin", "Mbak Sari" in refuse, refuse)

# ---------------- sales path ----------------
srv = load_agent(SALES_SPEC)
halo = srv.brain("s1", "halo")
check("sales halo: knows business + owner + menu", "Sari's Catering" in halo and "Bu Sari" in halo and "150.000" in halo)
order = srv.brain("s1", "2 tumpeng mini buat jumat")
check("order: total computed in code 300.000", "300.000" in order, order)
pay = srv.brain("s1", "transfer sudah ya, ini buktinya")
check("payment: never confirmed, owner verifies", "verifikasi" in pay and "Bu Sari" in pay, pay)
off = srv.brain("s1", "ada sushi ga?")
check("off-catalogue: escalates to owner", "Bu Sari" in off, off)
disc = srv.brain("s1", "boleh diskon ga")
check("discount: escalates", "Bu Sari" in disc, disc)
menu = srv.brain("s1", "menu apa aja?")
check("menu enquiry: lists real prices", "35.000" in menu and "150.000" in menu, menu)

os.remove(HERE / "spec.json")
print("\nALL TESTS PASSED — halo test + fixture verdicts + the 5 skipped behavior tests")
