"""One LLM call: transcript + extracted files (+ optional research) in,
raw ForgeSpec JSON out. OpenAI-compatible chat completions (PRD §5 step 7,
originally specified against Kimi; provider is Doubleword.ai / gpt-oss —
see config.py for why). Never trusts the result as valid — that's
forgespec_validation.py's job."""
import json
import logging
import re

import requests

from . import config

log = logging.getLogger("meeting-mode")

SYSTEM_PROMPT = """You are the Architect agent in the Biks Forge pipeline. You read a meeting \
transcript plus the client's own files and produce exactly one ForgeSpec JSON object — nothing \
else. No markdown fences, no prose, no explanation. JSON only.

ForgeSpec v2 schema. Pick ONE workflow based on the client's actual pain described in the \
transcript:

--- workflow: "recon" (bank reconciliation pain — admin manually matches channel closings \
against bank mutasi) ---
{
  "workflow": "recon",
  "persona": {"agent_name": str, "language": "id", "tone": str, "owner_name": str, "admin_name": str},
  "business": {"name": str, "outlets": [str, ...], "bank": str},
  "channels": [
    {"name": str, "hits_bank": bool, "fee_rate": float (0-1, omit for CASH/TRANSFER), "settle_days": int, "assumed": bool}
  ],
  "policy": {"currency": "IDR", "guardrails": [str, ...]}
}

--- workflow: "sales" (order chaos pain — catalog Q&A, cart, payment confirmation) ---
{
  "workflow": "sales",
  "persona": {"agent_name": str, "language": "id", "tone": str, "owner_name": str},
  "products": {
    "store": {"name": str, "location": str, "hours": str, "wa_number": str, "kurir": str},
    "categories": [
      {"name": str, "variants": [{"id": str, "name": str, "price": number, "aliases": [str, ...]}]}
    ]
  },
  "policy": {"currency": "IDR", "payment": str, "guardrails": [str, ...]}
}

Rules:
- business.name (recon) or products.store.name (sales) is REQUIRED and must be a real business \
name from the transcript or files — never invent one, never leave it blank.
- persona.agent_name: if the transcript does not explicitly name the agent, DO NOT invent an \
unrelated name. Default it to the owner's first name or a short form of the business name — \
never a name that appears nowhere in the source material.
- "policy" is a TOP-LEVEL key, a sibling of "products" (sales) or "channels" (recon) — it is \
NEVER nested inside "products" or any other object. Re-check your output's key nesting before \
answering.
- For recon: channels must be non-empty. Fee rates you infer rather than hear explicitly must be \
marked "assumed": true — never state an assumed rate as if it were confirmed.
- For sales: products.categories must contain EVERY priced item mentioned anywhere in the \
transcript or the client's files — scan the files fully, do not stop after the first item you \
find. Each variant needs a real numeric price; never invent a price.
- Source precedence when inputs disagree, HIGHEST to LOWEST: (1) client's uploaded PDF/Excel \
files — the durable record, prefer these over vague verbal mentions since transcription can \
mishear numbers; (2) the meeting transcript; (3) "PRE-MEETING RESEARCH" context if present — \
public web data, may be stale, wrong, or about the wrong business entirely. NEVER let web \
research override or contradict something the transcript or a client file actually said — web \
research may only fill in gaps neither of the other two sources covers. Whenever two sources \
disagree on a value you used, keep both source values discoverable by not silently averaging or \
guessing — use the higher-priority source's value and note the discrepancy (which sources, which \
values) in an extra "notes" array field at the top level.
- Output valid JSON matching one of the two shapes above. Nothing else.
"""


class LLMError(Exception):
    pass


def _extract_json(raw_text: str) -> dict:
    text = raw_text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise LLMError("LLM response was not valid JSON")


def generate_forge_spec(
    *,
    transcript: str,
    excel_summaries: list[str],
    pdf_summaries: list[str],
    research_context: str | None = None,
    workflow_hint: str | None = None,
) -> dict:
    if not config.LLM_API_KEY or not config.LLM_BASE_URL:
        raise LLMError("LLM_API_KEY / LLM_BASE_URL not configured")

    user_parts = [f"MEETING TRANSCRIPT:\n{transcript}"]
    for s in excel_summaries:
        user_parts.append(f"CLIENT EXCEL FILE:\n{s}")
    for s in pdf_summaries:
        user_parts.append(f"CLIENT PDF FILE:\n{s}")
    if research_context:
        user_parts.append(f"PRE-MEETING RESEARCH (optional, enrichment only, may be incomplete):\n{research_context}")
    if workflow_hint:
        # "Record again" continuation: this is a correction for a business
        # already forged as `workflow_hint`, not a fresh routing decision.
        user_parts.append(
            f'CONTINUATION NOTE: this is a corrected re-recording for a business already classified '
            f'as workflow="{workflow_hint}". Unless the transcript describes a completely different '
            f'business problem, keep workflow="{workflow_hint}" — do not re-route based on incidental '
            f"wording (e.g. mentioning payment verification does not make this a recon business)."
        )

    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n---\n\n".join(user_parts)},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }

    try:
        resp = requests.post(
            f"{config.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=config.LLM_TIMEOUT_S,
        )
    except requests.RequestException as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc

    if resp.status_code != 200:
        # Never log the request body (contains transcript content) or the
        # API key; the response body from a well-behaved API is safe to log.
        raise LLMError(f"LLM returned HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise LLMError(f"unexpected LLM response shape: {exc}") from exc

    return _extract_json(content)
