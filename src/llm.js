import { config } from "./config.js";
import { FORGE_PRD_SYSTEM_PROMPT } from "./prd-template.js";

// Generation can legitimately take a while on a GPU endpoint (full PRD or a
// complete UI file), so the timeout is generous — callers stream progress to
// the browser via polling, not by blocking on this.
const REQUEST_TIMEOUT_MS = 120_000;

async function chat(messages, { temperature = 0.4, maxTokens, model, timeoutMs = REQUEST_TIMEOUT_MS, signal } = {}) {
  if (!config.kimiApiKey) throw new Error("KIMI_API_KEY is not set in .env");
  const abort = signal ? AbortSignal.any([AbortSignal.timeout(timeoutMs), signal]) : AbortSignal.timeout(timeoutMs);

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
    signal: abort,
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
          '"workflow": {"stages": [{"name": "ONE word stage name (e.g. Input, Match, Review, Report)", ' +
          '"steps": [{"label": "1-3 words", "actor": "who does it (a person mentioned, AI Assistant, Owner, System)", "detail": "3-6 words: from what to what"}, ...]}, ...]}}\n' +
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

  const notes = JSON.parse(stripFence(raw));
  // "Kami Bali Banana" -> "Bali Banana" everywhere the name is shown
  if (notes.business_name) {
    notes.business_name = String(notes.business_name).replace(/^\s*(kami|kita|we are|we're|jadi)\s+/i, "").trim();
  }
  return notes;
}

/**
 * Full PRD in markdown following the Biks Forge format (8 sections: decisions,
 * experience walkthrough, architecture pipeline, brief customer-language table,
 * build plan, cut list, risks & fallbacks, file map). Built from the structured
 * notes + raw transcript tail. The system prompt lives in prd-template.js so it
 * can be reviewed and revised independently of the LLM client.
 */
export async function generatePRD(notes, transcriptTail, { signal } = {}) {
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
    return await chat(messages, { temperature: 0.4, model: config.kimiSmartModel, maxTokens: 16000, timeoutMs: 300_000, signal });
  } catch (err) {
    // Thinking models sometimes burn the whole budget reasoning and emit
    // nothing — fall back to the fast non-thinking model rather than failing.
    if (!/ran out of tokens|empty content/.test(err.message)) throw err;
    console.warn("[prd] smart model overran, falling back to", config.kimiModel);
    return chat(messages, { temperature: 0.4, model: config.kimiModel, maxTokens: 16000, timeoutMs: 240_000, signal });
  }
}

// ===== the Architect: meeting notes -> ForgeSpec (the contract the agent runs on) =====
// The LLM proposes; this code disposes. Every field is validated and defaulted
// here so a malformed extraction can never produce a broken agent.

const STANDARD_FEES = {
  QRIS: { fee_rate: 0.007, settle_days: 1 },
  GOFOOD: { fee_rate: 0.2, settle_days: 2 },
  GRABFOOD: { fee_rate: 0.2, settle_days: 1 },
  SHOPEEFOOD: { fee_rate: 0.2, settle_days: 1 },
  TRANSFER: { fee_rate: 0, settle_days: 0 },
  EDC: { fee_rate: 0.02, settle_days: 1 },
};

function routeWorkflow(candidate, textBlob) {
  if (candidate === "recon" || candidate === "sales") return candidate;
  // keyword fallback so the router never leaves the library
  const t = textBlob.toLowerCase();
  const reconHits = (t.match(/rekonsiliasi|mutasi|closing|settlement|bank statement|bank mutation|reconcil|matching|match(?:es|ing)? receipts|discrepan|finance admin|selisih|cocokin|selis/g) || []).length;
  const salesHits = (t.match(/pesanan|order|menu|katalog|catalogue|harga|price list|jualan|customer chat/g) || []).length;
  return salesHits > reconHits ? "sales" : "recon";
}

export function validateForgeSpec(raw, notes, textBlob) {
  const spec = typeof raw === "object" && raw !== null ? raw : {};
  spec.workflow = routeWorkflow(spec.workflow, textBlob || JSON.stringify(notes));

  const persona = (spec.persona ||= {});
  persona.language ||= notes.meeting_language === "id" ? "id" : "en";
  persona.owner_name ||= "Owner";
  persona.admin_name ||= "admin";

  const biz = (spec.business ||= {});
  biz.name ||= notes.business_name || "Bisnis Anda";
  // "Kami Bali Banana" -> "Bali Banana": strip pronoun/filler prefixes the
  // extractor sometimes drags in from the sentence
  biz.name = String(biz.name).replace(/^\s*(kami|kita|we are|we're|jadi)\s+/i, "").trim() || "Bisnis Anda";
  persona.agent_name ||= `${biz.name.split(/\s+/)[0]} AI`;

  if (!spec.painpoint) {
    spec.painpoint = notes.points?.[0] || notes.summary || "";
  }

  if (spec.workflow === "recon") {
    let channels = Array.isArray(spec.channels) ? spec.channels : [];
    channels = channels
      .filter((c) => c && c.name)
      .map((c) => {
        const name = String(c.name).toUpperCase().replace(/[^A-Z]/g, "");
        const std = STANDARD_FEES[name];
        const out = { name, hits_bank: c.hits_bank !== false && name !== "CASH" };
        if (name === "CASH") out.hits_bank = false;
        if (out.hits_bank) {
          if (typeof c.fee_rate === "number" && c.fee_rate >= 0 && c.fee_rate < 0.5) {
            out.fee_rate = c.fee_rate;
          } else if (std) {
            out.fee_rate = std.fee_rate;
            out.assumed = true;
          } else {
            out.fee_rate = 0;
            out.assumed = true;
          }
          out.settle_days = Number.isInteger(c.settle_days) ? c.settle_days : std?.settle_days ?? 0;
        }
        return out;
      });
    if (channels.length === 0) {
      channels = ["CASH", "QRIS", "GOFOOD", "GRABFOOD", "TRANSFER"].map((name) => {
        const std = STANDARD_FEES[name];
        return name === "CASH"
          ? { name, hits_bank: false }
          : { name, hits_bank: true, ...std, assumed: true };
      });
    }
    spec.channels = channels;
    delete spec.products;
  } else {
    const products = (spec.products ||= {});
    products.store ||= {};
    products.store.name ||= biz.name;
    let cats = Array.isArray(products.categories) ? products.categories : [];
    cats = cats
      .map((cat) => ({
        name: cat?.name || "Menu",
        variants: (cat?.variants || [])
          .filter((v) => v && v.name && Number(v.price) > 0)
          .map((v) => ({
            id: v.id || v.name.slice(0, 8).toUpperCase().replace(/\s+/g, "-"),
            name: v.name,
            price: Number(v.price),
            aliases: Array.isArray(v.aliases) ? v.aliases : [],
          })),
      }))
      .filter((cat) => cat.variants.length > 0);
    products.categories = cats; // empty catalogue is allowed: agent says "menu menyusul", owner fills it in
    delete spec.channels;
  }

  spec.slug = String(biz.name)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40) || "client";
  return spec;
}

export async function extractForgeSpec(notes, transcriptTail) {
  const raw = await chat(
    [
      {
        role: "system",
        content:
          "You are the Architect in the Biks Forge. From meeting notes + transcript, produce the " +
          "ForgeSpec JSON that configures the client's AI employee. Choose the workflow from the " +
          "production library:\n" +
          '- "recon": the pain is matching bank statements/mutasi against sales closings, settlements, fees\n' +
          '- "sales": the pain is taking orders/customer chats, price lists, recording sales\n' +
          "Reply with STRICT JSON only:\n" +
          '{"workflow":"recon|sales",' +
          '"persona":{"agent_name":"short friendly bot name","language":"id|en","owner_name":"how the owner was addressed IN THIS MEETING; if no name was spoken use just Pak or Bu — NEVER invent a name","admin_name":"the admin/staff name ONLY if spoken; else the word admin"},' +
          '"business":{"name":"...","outlets":["..."],"bank":"..."},' +
          '"painpoint":"ONE sentence, in the meeting language, describing the daily pain in the client\'s own words (used to greet them, so make it specific: who, what, how long)",' +
          '"channels":[{"name":"CASH|QRIS|GOFOOD|GRABFOOD|TRANSFER|...","fee_rate":0.007,"settle_days":1}] (recon only; omit fee_rate if not stated in the meeting),' +
          '"products":{"store":{"name":"..."},"categories":[{"name":"...","variants":[{"name":"...","price":35000,"aliases":["..."]}]}]} (sales only; prices ONLY if explicitly stated)\n' +
          "Never invent numbers: omit any fee/price not said in the meeting.",
      },
      {
        role: "user",
        content:
          `Meeting notes:\n${JSON.stringify(notes, null, 2)}\n\n` +
          `Transcript tail:\n${transcriptTail}`,
      },
    ],
    { temperature: 0.2, maxTokens: 2500, timeoutMs: 90_000 }
  );
  let parsed = {};
  try {
    parsed = JSON.parse(stripFence(raw));
  } catch {
    // validateForgeSpec fills a safe default spec from the notes alone
  }
  return validateForgeSpec(parsed, notes, `${JSON.stringify(notes)}\n${transcriptTail}`);
}
