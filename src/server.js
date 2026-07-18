import express from "express";
import path from "node:path";
import { appendFile, mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { config } from "./config.js";
import { updateNotes, generatePRD, generateUI, chatAsBusiness } from "./llm.js";
import { deployPreview, isDaytonaConfigured } from "./daytona.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

const EMPTY_NOTES = { summary: "", points: [], requirements: [], action_items: [], decisions: [] };

// Single-meeting in-memory state — a hackathon demo runs one meeting at a
// time; restart the server for a fresh one (or hit /api/reset).
const state = {
  transcript: [], // { speaker, text, at }
  notes: structuredClone(EMPTY_NOTES),
  prd: null,
  ui: null,
  previewUrl: null,
  busy: { notes: false, prd: false, ui: false, deploy: false },
  lastError: null,
  charsSinceNotes: 0,
  autoForge: true, // the "many bots" mode: PRD + UI keep regenerating as the client talks
  chatHistory: [], // the forged agent's WhatsApp conversation ({role, content})
  lastForgeAt: null,
};

// Bumped by /api/reset. Bots capture the epoch when they start and discard
// their result if a reset happened mid-generation — otherwise an in-flight
// bot from the previous meeting writes stale artifacts into the fresh one.
let meetingEpoch = 0;

// ===== incremental note-taking =====
const NOTES_TRIGGER_CHARS = 220; // update notes roughly every ~2-3 sentences

let notesInflight = null;

async function runNotesPass() {
  const epoch = meetingEpoch;
  const pending = state.transcript.filter((t) => !t.analyzed);
  if (pending.length === 0) return;

  state.busy.notes = true;
  const chunk = pending.map((t) => t.text).join("\n");
  try {
    const updated = await updateNotes(state.notes, chunk);
    if (epoch !== meetingEpoch) return; // meeting was reset mid-flight
    state.notes = updated;
    pending.forEach((t) => (t.analyzed = true));
    state.charsSinceNotes = 0;
    state.lastError = null;
  } catch (err) {
    console.error("[notes]", err.message);
    if (epoch === meetingEpoch) state.lastError = `notes: ${err.message}`;
  } finally {
    state.busy.notes = false;
  }
}

// Serialized: a caller that arrives mid-pass WAITS for it, then runs another
// pass if new transcript landed meanwhile — so "refresh then generate PRD"
// always sees up-to-date notes instead of silently skipping (the old
// fire-and-forget guard returned stale notes in exactly that race).
async function refreshNotes() {
  while (notesInflight) await notesInflight.catch(() => {});
  if (!state.transcript.some((t) => !t.analyzed)) return;
  notesInflight = runNotesPass();
  try {
    await notesInflight;
  } finally {
    notesInflight = null;
  }
}

// ===== generation bots (shared by routes and the auto-forge loop) =====

async function runPrdBot() {
  if (state.busy.prd) return;
  const epoch = meetingEpoch;
  state.busy.prd = true;
  try {
    const tail = state.transcript.slice(-30).map((t) => t.text).join("\n");
    const prd = await generatePRD(state.notes, tail);
    if (epoch === meetingEpoch) state.prd = prd;
  } catch (err) {
    console.error("[prd]", err.message);
    if (epoch === meetingEpoch) state.lastError = `prd: ${err.message}`;
  } finally {
    state.busy.prd = false;
  }
}

async function runUiBot() {
  if (state.busy.ui) return;
  const epoch = meetingEpoch;
  state.busy.ui = true;
  try {
    // Uses whatever PRD exists right now — it catches up next cycle. That's
    // the point of the forge loop: parallel bots, artifacts converge.
    const ui = await generateUI(state.notes, state.prd);
    if (epoch === meetingEpoch) state.ui = ui;
  } catch (err) {
    console.error("[ui]", err.message);
    if (epoch === meetingEpoch) state.lastError = `ui: ${err.message}`;
  } finally {
    state.busy.ui = false;
  }
}

let lastDeployedUi = null;

async function runDeployBot() {
  if (!state.ui || state.busy.deploy || !isDaytonaConfigured()) return;
  if (state.ui === lastDeployedUi) return; // nothing new to ship
  state.busy.deploy = true;
  try {
    const { url } = await deployPreview(state.ui);
    state.previewUrl = url;
    lastDeployedUi = state.ui;
  } catch (err) {
    console.error("[deploy]", err.message);
    state.lastError = `deploy: ${err.message}`;
  } finally {
    state.busy.deploy = false;
  }
}

// ===== auto-forge loop =====
// Every cycle: if the conversation grew enough, refresh notes, then let the
// PRD bot and UI bot run IN PARALLEL, then ship the new UI to Daytona.
// Nobody presses anything; the artifacts just keep catching up.
const FORGE_INTERVAL_MS = 15_000; // safety net; the real trigger is notes-refresh below
const MIN_NEW_CHARS_TO_FORGE = 120;
let forgedChars = 0;

async function forgeCycle(force = false) {
  const totalChars = state.transcript.reduce((n, t) => n + t.text.length, 0);
  if (totalChars === 0) return;
  if (!force && totalChars - forgedChars < MIN_NEW_CHARS_TO_FORGE) return;
  forgedChars = totalChars;
  state.lastForgeAt = Date.now();

  await refreshNotes();
  if (state.notes.output_type === "chatbot") {
    // Conversational solution: the agent IS the deliverable. Spec injection,
    // no UI codegen, no sandbox — the WhatsApp template talks to /api/chat.
    state.previewUrl = "/wa-chat.html";
    await runPrdBot();
    return;
  }
  await Promise.allSettled([runPrdBot(), runUiBot()]);
  await runDeployBot();
}

setInterval(() => {
  if (state.autoForge) forgeCycle().catch((err) => console.error("[forge]", err.message));
}, FORGE_INTERVAL_MS);

export function createApp() {
  const app = express();
  app.use(express.json({ limit: "12mb" }));

  // Light status for the 2.5s poll — the heavyweight payloads (full PRD
  // markdown, generated UI html, transcript) are NOT included; the frontend
  // fetches them only when the length counters change. Shipping them on
  // every poll made the browser parse hundreds of KB/minute for nothing.
  app.get("/api/state", (req, res) => {
    res.json({
      notes: state.notes,
      busy: state.busy,
      lastError: state.lastError,
      autoForge: state.autoForge,
      lastForgeAt: state.lastForgeAt,
      previewUrl: state.previewUrl,
      prdLen: state.prd?.length ?? 0,
      uiLen: state.ui?.length ?? 0,
      transcriptCount: state.transcript.length,
      daytonaConfigured: isDaytonaConfigured(),
      whisperConfigured: Boolean(config.nosanaWhisperUrl),
      outputType: state.notes.output_type || "app",
    });
  });

  app.get("/api/prd", (req, res) => {
    res.type("text/plain").send(state.prd ?? "");
  });

  // Audio chunk -> Whisper on Nosana GPU -> transcript entry. The frontend
  // sends complete ~6s webm files (it restarts MediaRecorder per chunk, so
  // every blob has container headers whisper can decode).
  app.post("/api/audio", express.raw({ type: () => true, limit: "30mb" }), async (req, res) => {
    if (!config.nosanaWhisperUrl) return res.status(400).json({ error: "NOSANA_WHISPER_URL is not set" });
    if (!req.body?.length) return res.status(400).json({ error: "empty audio" });
    const speaker = String(req.query.speaker || "Speaker");
    try {
      const fd = new FormData();
      fd.append("audio_file", new Blob([req.body], { type: "audio/webm" }), "chunk.webm");
      const r = await fetch(
        `${config.nosanaWhisperUrl}/asr?encode=true&task=transcribe&language=en&output=json`,
        { method: "POST", body: fd, signal: AbortSignal.timeout(60_000) }
      );
      if (!r.ok) throw new Error(`whisper ${r.status}: ${(await r.text().catch(() => "")).slice(0, 200)}`);
      const data = await r.json();
      const text = (data.text || "").trim();
      if (text) {
        state.transcript.push({ speaker, text, at: Date.now(), analyzed: false });
        state.charsSinceNotes += text.length;
        if (state.charsSinceNotes >= NOTES_TRIGGER_CHARS)
          refreshNotes().then(() => { if (state.autoForge) forgeCycle().catch((e) => console.error("[forge]", e.message)); });
      }
      res.json({ ok: true, text });
    } catch (err) {
      console.error("[audio]", err.message);
      res.status(502).json({ error: err.message });
    }
  });

  app.post("/api/transcript", (req, res) => {
    const { text, speaker } = req.body || {};
    if (!text?.trim()) return res.status(400).json({ error: "text required" });
    state.transcript.push({ speaker: speaker || "Speaker", text: text.trim(), at: Date.now(), analyzed: false });
    state.charsSinceNotes += text.length;
    if (state.charsSinceNotes >= NOTES_TRIGGER_CHARS)
      refreshNotes().then(() => { if (state.autoForge) forgeCycle().catch((e) => console.error("[forge]", e.message)); });
    res.json({ ok: true });
  });

  // Force a notes pass (e.g. right before generating the PRD)
  app.post("/api/notes/refresh", async (req, res) => {
    await refreshNotes();
    res.json({ ok: true, notes: state.notes });
  });

  // Force one full cycle right now (the "forge now" button). Fire-and-forget:
  // a cycle can take minutes (UI bot), progress is visible via /api/state.
  app.post("/api/forge", (req, res) => {
    forgeCycle(true).catch((err) => console.error("[forge]", err.message));
    res.json({ ok: true, started: true });
  });

  app.post("/api/autoforge", (req, res) => {
    state.autoForge = Boolean(req.body?.enabled);
    res.json({ ok: true, autoForge: state.autoForge });
  });

  app.post("/api/generate/prd", async (req, res) => {
    if (state.busy.prd) return res.status(409).json({ error: "PRD generation already running" });
    await refreshNotes();
    await runPrdBot();
    res.json({ ok: true });
  });

  app.post("/api/generate/ui", async (req, res) => {
    if (state.busy.ui) return res.status(409).json({ error: "UI generation already running" });
    await refreshNotes();
    await runUiBot();
    res.json({ ok: true });
  });

  app.post("/api/deploy", async (req, res) => {
    if (!state.ui) return res.status(400).json({ error: "no UI generated yet" });
    if (state.busy.deploy) return res.status(409).json({ error: "deploy already running" });
    lastDeployedUi = null; // manual deploy always ships, even if unchanged
    await runDeployBot();
    res.json({ ok: true, url: state.previewUrl });
  });

  // The forged agent: real replies as the client's business (spec injection).
  // [RECORD]{json} lines in the reply are stripped and appended to the ledger
  // (data/ledger.jsonl — swap for Google Sheets when creds are wired in).
  app.post("/api/chat", async (req, res) => {
    const { message, image } = req.body || {};
    if (!message?.trim() && !image) return res.status(400).json({ error: "message or image required" });
    try {
      const raw = await chatAsBusiness(state.notes, state.prd, state.chatHistory, message?.trim() || "", image);
      const records = [];
      const reply = raw
        .split("\n")
        .filter((line) => {
          const m = line.match(/^\s*\[RECORD\]\s*(\{.*\})\s*$/);
          if (!m) return true;
          try { records.push(JSON.parse(m[1])); } catch {}
          return false;
        })
        .join("\n")
        .trim();
      if (records.length) {
        const dir = path.join(__dirname, "..", "data");
        await mkdir(dir, { recursive: true });
        await appendFile(
          path.join(dir, "ledger.jsonl"),
          records.map((r) => JSON.stringify({ at: new Date().toISOString(), ...r })).join("\n") + "\n"
        );
      }
      state.chatHistory.push(
        { role: "user", content: message?.trim() || "(image)" },
        { role: "assistant", content: reply }
      );
      res.json({ reply, recorded: records.length });
    } catch (err) {
      console.error("[chat]", err.message);
      res.status(502).json({ error: err.message });
    }
  });

  app.post("/api/reset", (req, res) => {
    meetingEpoch += 1;
    state.transcript = [];
    state.notes = structuredClone(EMPTY_NOTES);
    state.prd = null;
    state.ui = null;
    state.previewUrl = null;
    state.lastError = null;
    state.charsSinceNotes = 0;
    state.lastForgeAt = null;
    state.chatHistory = [];
    forgedChars = 0;
    lastDeployedUi = null;
    res.json({ ok: true });
  });

  // Local preview of the generated prototype (works without Daytona)
  app.get("/preview", (req, res) => {
    if (!state.ui) return res.status(404).send("<p>No prototype yet. Generate the UI first.</p>");
    res.type("html").send(state.ui);
  });

  app.use(express.static(path.join(__dirname, "..", "public")));
  return app;
}

// First ASR request after a container (re)start pays ~40s of model loading.
// Warm it from here at startup and re-check every few minutes so the demo's
// first spoken words never eat the cold start; also logs when the Nosana
// container is down/re-initializing (503) so it's visible in notula.log.
async function warmUpWhisper() {
  if (!config.nosanaWhisperUrl) return;
  try {
    const ping = await fetch(`${config.nosanaWhisperUrl}/docs`, { signal: AbortSignal.timeout(10_000) });
    if (!ping.ok) {
      console.warn(`[whisper] service not ready (${ping.status}) — Nosana container may be restarting`);
      return;
    }
    // 0.3s of silence is enough to force the model into VRAM.
    const silenceWav = Buffer.concat([
      Buffer.from("RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00", "binary"),
      Buffer.from([0x80, 0x3e, 0, 0, 0, 0x7d, 0, 0, 2, 0, 0x10, 0]),
      Buffer.from("data", "binary"),
      Buffer.alloc(4 + 9600),
    ]);
    const fd = new FormData();
    fd.append("audio_file", new Blob([silenceWav], { type: "audio/wav" }), "warmup.wav");
    const t0 = Date.now();
    await fetch(`${config.nosanaWhisperUrl}/asr?encode=true&task=transcribe&language=en&output=json`, {
      method: "POST", body: fd, signal: AbortSignal.timeout(120_000),
    });
    console.log(`[whisper] warm (${Date.now() - t0}ms)`);
  } catch (err) {
    console.warn("[whisper] warm-up failed:", err.message);
  }
}

const app = createApp();
app.listen(config.port, () => {
  console.log(`[notula] listening on http://localhost:${config.port}`);
  if (!config.kimiApiKey) console.warn("[notula] KIMI_API_KEY belum diisi — fitur AI belum aktif");
  if (!isDaytonaConfigured()) console.warn("[notula] DAYTONA_API_KEY belum diisi — preview pakai /preview lokal");
  warmUpWhisper();
  setInterval(warmUpWhisper, 5 * 60 * 1000);
});
