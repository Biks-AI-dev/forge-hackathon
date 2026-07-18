import { config } from "./config.js";
import { FORGE_PRD_SYSTEM_PROMPT } from "./prd-template.js";

// Generation can legitimately take a while on a GPU endpoint (full PRD or a
// complete UI file), so the timeout is generous — callers stream progress to
// the browser via polling, not by blocking on this.
const REQUEST_TIMEOUT_MS = 120_000;

async function chat(messages, { temperature = 0.4, maxTokens, model, timeoutMs = REQUEST_TIMEOUT_MS } = {}) {
  if (!config.kimiApiKey) throw new Error("KIMI_API_KEY is not set in .env");

  const res = await fetch(`${config.kimiBaseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${config.kimiApiKey}`,
    },
    body: JSON.stringify({
      model: model ?? config.kimiModel,
      messages,
      temperature,
      ...(maxTokens ? { max_tokens: maxTokens } : {}),
    }),
    signal: AbortSignal.timeout(timeoutMs),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`LLM API error ${res.status}: ${body.slice(0, 300)}`);
  }

  const data = await res.json();
  const msg = data.choices[0].message;
  // Thinking models (glm-5.2 etc.) put chain-of-thought in `reasoning` and
  // can run out of tokens before emitting content — surface that clearly
  // instead of returning an empty document.
  if (!msg.content?.trim()) {
    throw new Error(
      data.choices[0].finish_reason === "length"
        ? "model ran out of tokens while thinking — raise maxTokens"
        : "model returned empty content"
    );
  }
  return msg.content;
}

const stripFence = (s) =>
  String(s ?? "")
    .trim()
    .replace(/^```[a-z]*\s*/i, "")
    .replace(/\s*```$/, "");

/**
 * Incremental note-taking: feed the meeting state so far + the newest
 * transcript chunk, get back the full updated structured notes. Re-sending
 * current notes (instead of the whole transcript every time) keeps the
 * prompt small no matter how long the meeting runs.
 */
export async function updateNotes(currentNotes, newChunk) {
  const raw = await chat(
    [
      {
        role: "system",
        content:
          "You are the note-taker in a meeting between a dev team and their client. You receive the " +
          "structured notes so far (JSON) plus the newest transcript chunk, and return the UPDATED, merged " +
          "notes. The transcript has NO speaker labels — infer from context who is talking: needs, wishes " +
          "and constraints are typically the client speaking; solutions, estimates and clarifying questions " +
          "are typically the team. Keep existing points unless the new transcript corrects them. Never " +
          "invent things that weren't said. Reply with strict JSON only, all values in English:\n" +
          '{"summary": "1-2 sentences capturing the meeting so far", ' +
          '"points": ["key point", ...], ' +
          '"requirements": ["product/feature requirement the client mentioned", ...], ' +
          '"action_items": ["task + owner (if mentioned)", ...], ' +
          '"decisions": ["decision that was agreed on", ...], ' +
          '"business_name": "client business name if mentioned, else \\"\\"", ' +
          '"meeting_language": "en or id — the language the meeting is mostly held in", ' +
          '"output_type": "chatbot or app", ' +
          '"workflow": {"stages": [{"name": "ONE word stage name (e.g. Input, Match, Review, Report)", ' +
          '"steps": [{"label": "1-3 words", "actor": "who does it (a person mentioned, AI Assistant, Owner, System)", "detail": "3-6 words: from what to what"}, ...]}, ...]}}\n' +
          "output_type: chatbot whenever the solution talks to people — WhatsApp, chat, messaging, replying " +
          "to customers/staff, reconciliation via chat. app ONLY when the client explicitly needs a " +
          "dashboard/tool UI and no conversation. When in doubt: chatbot.\n" +
          "The workflow is the client's future process with the solution: 3-5 stages, 1-3 steps each. " +
          "Every step says WHO does WHAT (e.g. {\"label\":\"Send Closing\",\"actor\":\"Sari\",\"detail\":\"shift receipt → WhatsApp\"}). " +
          "Keep it stable between updates — only change it when the conversation genuinely changes the process.",
      },
      {
        role: "user",
        content:
          `Notes so far:\n${JSON.stringify(currentNotes)}\n\n` +
          `Latest transcript:\n${newChunk}`,
      },
    ],
    { temperature: 0.2 }
  );

  return JSON.parse(stripFence(raw));
}

/**
 * Full PRD in markdown following the Biks Forge format (8 sections: decisions,
 * experience walkthrough, architecture pipeline, brief customer-language table,
 * build plan, cut list, risks & fallbacks, file map). Built from the structured
 * notes + raw transcript tail. The system prompt lives in prd-template.js so it
 * can be reviewed and revised independently of the LLM client.
 */
export async function generatePRD(notes, transcriptTail) {
  const messages = [
    { role: "system", content: FORGE_PRD_SYSTEM_PROMPT },
    {
      role: "user",
      content:
        `Structured meeting notes:\n${JSON.stringify(notes, null, 2)}\n\n` +
        `Latest transcript excerpt (extra context):\n${transcriptTail}`,
    },
  ];
  try {
    return await chat(messages, { temperature: 0.4, model: config.kimiSmartModel, maxTokens: 32000, timeoutMs: 300_000 });
  } catch (err) {
    // Thinking models sometimes burn the whole budget reasoning and emit
    // nothing — fall back to the fast non-thinking model rather than failing.
    if (!/ran out of tokens|empty content/.test(err.message)) throw err;
    console.warn("[prd] smart model overran, falling back to", config.kimiModel);
    return chat(messages, { temperature: 0.4, model: config.kimiModel, maxTokens: 16000, timeoutMs: 240_000 });
  }
}

/**
 * One self-contained HTML file implementing a clickable UI prototype of what
 * the client asked for. Single file so it can be served/deployed anywhere
 * (local /preview or a Daytona sandbox) with zero build step.
 */
export async function generateUI(notes, prd) {
  const raw = await chat(
    [
      {
        role: "system",
        content:
          "You are a frontend engineer. Build ONE complete HTML file (inline CSS + JS, no external " +
          "libraries, no CDNs) that is a clickable UI prototype of the described product, entirely in " +
          "English. Modern, clean, responsive, with plausible dummy data matching the client's domain. " +
          "Every main button/menu must do something (at minimum navigate between views with JS). Reply " +
          "with ONLY the HTML code — no explanation, no markdown fence.",
      },
      {
        role: "user",
        content:
          `Meeting notes:\n${JSON.stringify(notes, null, 2)}\n\n` +
          (prd ? `PRD:\n${prd.slice(0, 6000)}` : "(No PRD yet — use the meeting notes alone)"),
      },
    ],
    { temperature: 0.5, model: config.kimiUiModel, maxTokens: 16000, timeoutMs: 180_000 }
  );

  return stripFence(raw);
}

/**
 * The forged agent itself: replies AS the client's business assistant.
 * Spec injection (notes + PRD excerpt), never codegen. Actions the model can
 * take: append a [RECORD]{json} line — the server strips it and writes it to
 * the ledger (data/ledger.jsonl; swap for Google Sheets when creds exist).
 * An attached image (data URL) is passed as vision content for OCR.
 */
export async function chatAsBusiness(notes, prd, history, userText, imageDataUrl) {
  const biz = notes.business_name || "the business";
  const system =
    `You are the AI assistant of ${biz}, live in their WhatsApp. You were configured from a ` +
    `discovery meeting — this spec is your ONLY source of truth about the business:\n` +
    `${JSON.stringify(notes)}\n` +
    (prd ? `PRD excerpt:\n${prd.slice(0, 3000)}\n` : "") +
    `Rules:\n` +
    `- Act as the business's own assistant talking to its staff/customers. Warm, brief, WhatsApp tone.\n` +
    `- Speak ${notes.meeting_language === "id" ? "Bahasa Indonesia" : "English"} by default (the meeting's language); ` +
    `switch only if the user writes in the other language.\n` +
    `- NEVER invent numbers, prices, or balances. If the spec doesn't contain it, say you'll check with the owner.\n` +
    `- Never confirm a payment as received without owner verification — say it's being verified.\n` +
    `- If the user sends an image (receipt, closing, statement): read every number out of it (OCR), ` +
    `echo the extracted figures back for confirmation, then record them.\n` +
    `- When a transaction/order/closing/statement should be saved, append as the LAST line:\n` +
    `[RECORD] {"type":"...","items":...,"amounts":...}\n` +
    `That line is machine-parsed and written to the business ledger — keep it valid single-line JSON.`;

  const content = imageDataUrl
    ? [{ type: "text", text: userText || "(image attached)" }, { type: "image_url", image_url: { url: imageDataUrl } }]
    : userText;

  return chat(
    [{ role: "system", content: system }, ...history.slice(-12), { role: "user", content }],
    { temperature: 0.4, maxTokens: 1500, timeoutMs: 90_000,
      ...(imageDataUrl ? { model: config.kimiVisionModel } : {}) }
  );
}
