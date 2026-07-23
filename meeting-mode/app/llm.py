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

TOPIC_MAP_PROMPT = """You are the Router agent in the Biks Forge pipeline. You read a meeting \
transcript and list the DISTINCT business workflows the client actually discussed — you do NOT \
merge different problems into one. Output exactly one JSON object, nothing else.

Supported workflows:
- "recon": bank reconciliation pain — admin manually matches channel closings against bank mutasi.
- "sales": order chaos pain — catalog Q&A, cart, totals, payment confirmation.

Output shape:
{"topics": [
  {"workflow": "recon"|"sales",
   "title": short label for this topic (client's own words where possible),
   "summary": 1-2 sentences of what THIS topic covers, in the transcript's language,
   "evidence": [1-3 short verbatim quotes from the transcript that belong to this topic],
   "wants_app_ui": true|false}
]}

Rules:
- One entry per DISTINCT workflow genuinely discussed as a business pain (at most one "recon" and \
one "sales" entry). A passing mention is not a topic — there must be a real problem or request.
- Never merge two different problems into one topic; never invent a topic that was not discussed.
- If the whole meeting is one workflow, return exactly one topic.
- "wants_app_ui": true ONLY if the client explicitly asked for a dashboard / app / portal-style \
tool for this topic (e.g. "saya mau dashboard", "bukan chat, tapi aplikasi"), otherwise false.
- JSON only. No markdown fences, no prose."""

SYSTEM_PROMPT = """You are the Architect agent in the Biks Forge pipeline. You read a meeting \
transcript plus the client's own files and produce exactly one ForgeSpec JSON object — nothing \
else. No markdown fences, no prose, no explanation. JSON only.

ForgeSpec v2 schema. Pick ONE workflow based on the client's actual pain described in the \
transcript (when a TOPIC SCOPE section is present, its workflow is already decided for you — \
use it):

--- workflow: "recon" (bank reconciliation pain — admin manually matches channel closings \
against bank mutasi) ---
{
  "workflow": "recon",
  "ui_mode": "chat" | "app",
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
  "ui_mode": "chat" | "app",
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
- For sales: products.categories must contain EVERY priced item that belongs to the workflow \
being specced — scan the transcript and the files fully, do not stop after the first item you \
find. Each variant needs a real numeric price; never invent a price.
- When a TOPIC SCOPE section is present, this meeting covered MORE THAN ONE distinct topic and \
you are building the spec for exactly ONE of them. Use ONLY content that belongs to the in-scope \
topic; products, channels, numbers, and guardrails that belong to the other listed topics are OUT \
OF SCOPE and must be EXCLUDED — do not blend topics into one spec.
- "ui_mode": "chat" by default. Set "app" ONLY if the client explicitly asked for a dashboard / \
app / portal-style tool for this workflow rather than (or on top of) a chat assistant — never \
infer it from the workflow type alone.
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


def _chat_json(system_prompt: str, user_content: str) -> dict:
    """One OpenAI-compatible chat call that must return a JSON object."""
    if not config.LLM_API_KEY or not config.LLM_BASE_URL:
        raise LLMError("LLM_API_KEY / LLM_BASE_URL not configured")

    payload = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
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


_KNOWN_WORKFLOWS = ("recon", "sales")


def map_topics(transcript: str) -> list[dict]:
    """Segments the meeting into distinct workflow topics BEFORE any spec is
    generated — the fix for 'everything gets merged into one spec'. Returns a
    normalized list: known workflows only, deduped (first mention wins), at
    most one topic per supported workflow. Raises LLMError on hard failure;
    the caller decides whether that is fatal (pipeline falls back to the
    legacy single-spec path)."""
    raw = _chat_json(TOPIC_MAP_PROMPT, f"MEETING TRANSCRIPT:\n{transcript}")
    topics = raw.get("topics")
    if not isinstance(topics, list):
        raise LLMError("topic map response had no 'topics' list")

    normalized: list[dict] = []
    seen: set[str] = set()
    for t in topics:
        if not isinstance(t, dict):
            continue
        wf = t.get("workflow")
        if wf not in _KNOWN_WORKFLOWS or wf in seen:
            continue
        seen.add(wf)
        normalized.append({
            "workflow": wf,
            "title": str(t.get("title") or wf),
            "summary": str(t.get("summary") or ""),
            "evidence": [str(q) for q in (t.get("evidence") or []) if isinstance(q, str)][:3],
            "wants_app_ui": bool(t.get("wants_app_ui")),
        })

    if not normalized:
        raise LLMError("topic map found no supported workflow topics")
    return normalized[:len(_KNOWN_WORKFLOWS)]


def generate_forge_spec(
    *,
    transcript: str,
    excel_summaries: list[str],
    pdf_summaries: list[str],
    research_context: str | None = None,
    workflow_hint: str | None = None,
    topic_focus: dict | None = None,
    other_topics: list[dict] | None = None,
) -> dict:
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

    if topic_focus:
        scope_lines = [
            "TOPIC SCOPE: this meeting covered more than one distinct topic. Build the ForgeSpec "
            "for EXACTLY this one:",
            f'- workflow: "{topic_focus["workflow"]}" (use this as the spec\'s "workflow")',
            f"- topic: {topic_focus.get('title', '')} — {topic_focus.get('summary', '')}",
        ]
        for quote in topic_focus.get("evidence") or []:
            scope_lines.append(f'- evidence: "{quote}"')
        if topic_focus.get("wants_app_ui"):
            scope_lines.append(
                '- the client explicitly asked for an app/dashboard-style tool for THIS topic: '
                'set "ui_mode": "app"'
            )
        if other_topics:
            scope_lines.append(
                "OUT OF SCOPE (these are being specced separately — exclude their content entirely):"
            )
            for ot in other_topics:
                scope_lines.append(
                    f"- {ot.get('title', ot.get('workflow', '?'))} — {ot.get('summary', '')}"
                )
        user_parts.append("\n".join(scope_lines))

    return _chat_json(SYSTEM_PROMPT, "\n\n---\n\n".join(user_parts))
