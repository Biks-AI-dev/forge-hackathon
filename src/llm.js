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
          '"decisions": ["decision that was agreed on", ...]}',
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
  return chat(
    [
      { role: "system", content: FORGE_PRD_SYSTEM_PROMPT },
      {
        role: "user",
        content:
          `Structured meeting notes:\n${JSON.stringify(notes, null, 2)}\n\n` +
          `Latest transcript excerpt (extra context):\n${transcriptTail}`,
      },
    ],
    // The Forge PRD template produces a longer document than the generic one
    // (ASCII diagrams, multi-column tables, screen-by-screen walkthrough).
    // 20k tokens gives the thinking model room to reason + output the full doc.
    { temperature: 0.4, model: config.kimiSmartModel, maxTokens: 20000, timeoutMs: 300_000 }
  );
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
