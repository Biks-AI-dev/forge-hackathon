"""
Parallel sandbox verification (PRD §2 stretch: "Three sandboxes for three
different specs run simultaneously"). Only meaningful once Gate 1 and
Gate 2 are stable — this exercises the same /forge endpoint they already
validated, just three times at once, to prove per-client isolation.

Proves:
1. Three concurrent POST /forge calls each get a distinct sandbox_id,
   chat_url, and slug.
2. All three chat URLs are independently reachable at the same time.
3. No cross-sandbox data leakage — each sandbox's own spec.json and
   rendered chat page show ONLY its own business's name/products/
   prices/persona, never another business's.

Usage:
    python parallel_verify.py [--provisioner-url http://127.0.0.1:8899] [--keep]

Requires the Provisioner running locally (or at --provisioner-url) and a
valid DAYTONA_API_KEY in .env (used only for the cross-sandbox spec.json
leak check, via the same daytona_ops module the Provisioner itself uses).
"""
import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.daytona_ops import make_client  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("parallel-verify")


# --- Three deliberately distinct ForgeSpecs: different business, workflow,
# persona, catalogue/channels, and prices, so any leakage is unambiguous
# (no shared strings or numbers between them). ---

SPEC_A = {
    "workflow": "sales",
    "persona": {"agent_name": "Rani", "language": "id", "tone": "ceria", "owner_name": "Bu Rani"},
    "products": {
        "store": {"name": "Toko Roti Manis Pagi", "location": "Bandung", "hours": "06:00-18:00",
                   "wa_number": "", "kurir": "internal"},
        "categories": [
            {"name": "Roti", "variants": [
                {"id": "roti-coklat", "name": "Roti Coklat", "price": 12000, "aliases": ["roti coklat"]},
                {"id": "roti-keju", "name": "Roti Keju", "price": 14000, "aliases": ["roti keju"]},
            ]},
        ],
    },
    "policy": {"currency": "IDR", "payment": "transfer", "guardrails": ["never invent a price"]},
}

SPEC_B = {
    "workflow": "sales",
    "persona": {"agent_name": "Joko", "language": "id", "tone": "santai", "owner_name": "Pak Joko"},
    "products": {
        "store": {"name": "Warung Kopi Senja", "location": "Yogyakarta", "hours": "16:00-23:00",
                   "wa_number": "", "kurir": "internal"},
        "categories": [
            {"name": "Kopi", "variants": [
                {"id": "kopi-susu", "name": "Kopi Susu Gula Aren", "price": 18000, "aliases": ["kopsus"]},
                {"id": "kopi-hitam", "name": "Kopi Hitam", "price": 10000, "aliases": ["kopi item"]},
            ]},
        ],
    },
    "policy": {"currency": "IDR", "payment": "cash-on-pickup", "guardrails": ["off-catalogue escalates to owner"]},
}

SPEC_C = {
    "workflow": "recon",
    "persona": {"agent_name": "Nusa", "language": "id", "tone": "tenang, jelas", "owner_name": "Pak Dharma",
                "admin_name": "Mbak Sari"},
    "business": {"name": "Dapoer Nusantara Parallel", "outlets": ["DN1", "DN2"], "bank": "BCA"},
    "channels": [
        {"name": "CASH", "hits_bank": False},
        {"name": "QRIS", "fee_rate": 0.007, "settle_days": 1, "assumed": True},
        {"name": "GOFOOD", "fee_rate": 0.20, "settle_days": 2, "assumed": True},
        {"name": "TRANSFER", "fee_rate": 0.0, "settle_days": 0},
    ],
    "policy": {"currency": "IDR", "guardrails": [
        "match on GROSS, book fees separately",
        "whatever remains is red: never force the gap to zero",
    ]},
}

SPECS = [("Toko Roti Manis Pagi", SPEC_A), ("Warung Kopi Senja", SPEC_B), ("Dapoer Nusantara Parallel", SPEC_C)]

# Strings that must never appear in another business's sandbox — names,
# products, and prices distinct enough not to collide by accident.
IDENTIFYING_STRINGS = {
    "Toko Roti Manis Pagi": ["Toko Roti Manis Pagi", "Roti Coklat", "Roti Keju", "12000", "14000", "Bu Rani"],
    "Warung Kopi Senja": ["Warung Kopi Senja", "Kopi Susu Gula Aren", "Kopi Hitam", "18000", "10000", "Pak Joko"],
    "Dapoer Nusantara Parallel": ["Dapoer Nusantara Parallel", "Pak Dharma", "Mbak Sari"],
}


@dataclass
class ForgeResult:
    business: str
    ok: bool
    elapsed_s: float
    sandbox_id: str | None = None
    chat_url: str | None = None
    slug: str | None = None
    error: str | None = None


@dataclass
class HealthResult:
    business: str
    ok: bool
    status_code: int | None = None
    error: str | None = None


def forge(provisioner_url: str, business: str, spec: dict) -> ForgeResult:
    t0 = time.perf_counter()
    try:
        resp = requests.post(f"{provisioner_url.rstrip('/')}/forge", json=spec, timeout=110)
    except requests.RequestException as exc:
        return ForgeResult(business, False, time.perf_counter() - t0, error=f"request failed: {exc}")
    elapsed = time.perf_counter() - t0
    if resp.status_code != 200:
        return ForgeResult(business, False, elapsed, error=f"HTTP {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    return ForgeResult(
        business, True, elapsed,
        sandbox_id=body.get("sandbox_id"), chat_url=body.get("chat_url"), slug=body.get("slug"),
    )


def health_check(business: str, chat_url: str) -> HealthResult:
    try:
        resp = requests.get(f"{chat_url.rstrip('/')}/health", timeout=15)
    except requests.RequestException as exc:
        return HealthResult(business, False, error=f"request failed: {exc}")
    return HealthResult(business, resp.status_code == 200, status_code=resp.status_code)


def check_leakage(business: str, chat_url: str, sandbox_id: str, daytona) -> list[str]:
    """Returns a list of leak descriptions (empty = clean)."""
    leaks = []
    own_strings = set(IDENTIFYING_STRINGS[business])
    other_strings: dict[str, list[str]] = {
        b: strs for b, strs in IDENTIFYING_STRINGS.items() if b != business
    }

    # 1. Rendered chat page — what a real user would actually see.
    try:
        page = requests.get(chat_url, timeout=15).text
    except requests.RequestException as exc:
        leaks.append(f"could not fetch chat page: {exc}")
        page = ""
    for other_business, strings in other_strings.items():
        for s in strings:
            if s and s in page:
                leaks.append(f"chat page for '{business}' contains '{s}' from '{other_business}'")

    # 2. The actual spec.json written into the sandbox — the ground truth.
    try:
        sandbox = daytona.get(sandbox_id)
        spec_text = sandbox.fs.download_file("spec.json").decode()
    except Exception as exc:
        leaks.append(f"could not download spec.json: {exc}")
        return leaks

    for other_business, strings in other_strings.items():
        for s in strings:
            if s and s in spec_text:
                leaks.append(f"spec.json for '{business}' contains '{s}' from '{other_business}'")

    for s in own_strings:
        if s not in spec_text and s not in page:
            # Not a leak, but worth surfacing: own data didn't make it through either.
            leaks.append(f"WARNING (not a leak): expected own value '{s}' not found in spec.json or chat page")

    return leaks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provisioner-url", default="http://127.0.0.1:8899")
    parser.add_argument("--keep", action="store_true", help="do not delete sandboxes on success")
    args = parser.parse_args()

    log.info("=== 1-2. Three ForgeSpecs prepared, calling POST /forge concurrently ===")
    t_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(forge, args.provisioner_url, biz, spec): biz for biz, spec in SPECS}
        forge_results: dict[str, ForgeResult] = {}
        for fut in as_completed(futures):
            r = fut.result()
            forge_results[r.business] = r
            if r.ok:
                log.info("forge OK business=%s elapsed=%.2fs sandbox_id=%s slug=%s",
                          r.business, r.elapsed_s, r.sandbox_id, r.slug)
            else:
                log.error("forge FAILED business=%s elapsed=%.2fs error=%s", r.business, r.elapsed_s, r.error)

    t_forge_done = time.perf_counter()

    failures = [r for r in forge_results.values() if not r.ok]
    successes = [r for r in forge_results.values() if r.ok]

    log.info("=== 3-4. Confirming distinct sandbox_id / chat_url / slug ===")
    distinctness_issues = []
    for field_name in ("sandbox_id", "chat_url", "slug"):
        values = [getattr(r, field_name) for r in successes]
        if len(values) != len(set(values)):
            distinctness_issues.append(f"duplicate {field_name} across successful forges: {values}")
    if not distinctness_issues:
        log.info("all %d successful forges have distinct sandbox_id, chat_url, and slug", len(successes))
    else:
        for issue in distinctness_issues:
            log.error(issue)

    log.info("=== 5-6. Health-checking all chat URLs concurrently ===")
    t_health_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(successes) or 1) as pool:
        futures = {pool.submit(health_check, r.business, r.chat_url): r.business for r in successes}
        health_results: dict[str, HealthResult] = {}
        for fut in as_completed(futures):
            r = fut.result()
            health_results[r.business] = r
            log.info("health business=%s ok=%s status=%s", r.business, r.ok, r.status_code)
    t_health_done = time.perf_counter()
    log.info("all %d health checks completed within %.2fs of each other (concurrent, not serial)",
              len(successes), t_health_done - t_health_start)

    log.info("=== 7. Cross-sandbox leakage check ===")
    daytona = make_client()
    leak_report: dict[str, list[str]] = {}
    for r in successes:
        leaks = check_leakage(r.business, r.chat_url, r.sandbox_id, daytona)
        leak_report[r.business] = leaks
        if leaks:
            for l in leaks:
                log.warning("business=%s: %s", r.business, l)
        else:
            log.info("business=%s: no leakage detected", r.business)

    t_total = time.perf_counter() - t_start

    # --- 8. Report ---
    print("\n" + "=" * 70)
    print("PARALLEL SANDBOX VERIFICATION REPORT")
    print("=" * 70)
    print(f"\nTotal concurrent duration: {t_total:.2f}s (forge phase: {t_forge_done - t_start:.2f}s)")
    print(f"\nIndividual provisioning times:")
    for r in forge_results.values():
        status = "OK" if r.ok else "FAILED"
        print(f"  {r.business:30s} {status:8s} {r.elapsed_s:.2f}s"
              + (f"  sandbox_id={r.sandbox_id}" if r.ok else f"  error={r.error}"))

    print(f"\nDistinctness: {'PASS' if not distinctness_issues else 'FAIL'}")
    for issue in distinctness_issues:
        print(f"  - {issue}")

    print(f"\nHealth checks:")
    for r in health_results.values():
        print(f"  {r.business:30s} {'OK' if r.ok else 'FAILED'}  status={r.status_code}")

    print(f"\nCross-sandbox leakage:")
    any_leak = False
    for business, leaks in leak_report.items():
        real_leaks = [l for l in leaks if not l.startswith("WARNING")]
        warnings = [l for l in leaks if l.startswith("WARNING")]
        if real_leaks:
            any_leak = True
            print(f"  {business}: LEAK DETECTED")
            for l in real_leaks:
                print(f"    - {l}")
        else:
            print(f"  {business}: clean")
        for w in warnings:
            print(f"    - {w}")

    print(f"\nFailures: {len(failures)}")
    for r in failures:
        print(f"  - {r.business}: {r.error}")

    overall_pass = (
        not failures
        and not distinctness_issues
        and all(r.ok for r in health_results.values())
        and not any_leak
    )
    print(f"\nOVERALL: {'PASS' if overall_pass else 'FAIL'}")
    print("=" * 70 + "\n")

    if not args.keep:
        log.info("cleaning up sandboxes")
        for r in successes:
            try:
                daytona.get(r.sandbox_id).delete(timeout=30, wait=True)
                log.info("deleted sandbox=%s (%s)", r.sandbox_id, r.business)
            except Exception as exc:
                log.error("failed to delete sandbox=%s (%s): %s", r.sandbox_id, r.business, exc)

    sys.exit(0 if overall_pass else 1)


if __name__ == "__main__":
    main()
