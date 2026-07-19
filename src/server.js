import express from "express";
import path from "node:path";
import { spawn } from "node:child_process";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { config } from "./config.js";
import { updateNotes, generatePRD, extractForgeSpec, validateForgeSpec } from "./llm.js";
import { forgeSandbox, isDaytonaConfigured } from "./daytona.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");
const AGENTS_DIR = path.join(ROOT, "data", "agents");
const AGENT_TEMPLATE = path.join(ROOT, "agent-template", "server.py");
const PYTHON = process.env.NOTULA_PYTHON || (process.platform === "win32" ? "python" : "python3");

const EMPTY_NOTES = { summary: "", points: [], requirements: [], action_items: [], decisions: [] };

// Single-meeting in-memory state — one meeting at a time; /api/reset for a
// fresh one. The forged agent itself is a separate process (see below), so a
// meeting reset never kills a client's ongoing test chat mid-sentence unless
// a new spec replaces it.
const state = {
  transcript: [], // { speaker, text, at }
  notes: structuredClone(EMPTY_NOTES),
  prd: null,
  spec: null, // the ForgeSpec — THE contract; everything the agent knows
  previewUrl: null, // local employee chat, once forged
  sandboxUrl: null, // per-client isolated sandbox (Daytona), after handover
  sandboxId: null,
  sandboxStale: false, // spec changed after last handover
  busy: { notes: false, prd: false, spec: false, sandbox: false },
  lastError: null,
  charsSinceNotes: 0,
  autoForge: true,
  lastForgeAt: null,
};

let meetingEpoch = 0;

// ===== incremental note-taking =====
const NOTES_TRIGGER_CHARS = 220;

let notesInflight = null;

async function runNotesPass() {
  const epoch = meetingEpoch;
  const pending = state.transcript.filter((t) => !t.analyzed);
  if (pending.length === 0) return;

  state.busy.notes = true;
  const chunk = pending.map((t) => t.text).join("\n");
  try {
    const updated = await updateNotes(state.notes, chunk);
    if (epoch !== meetingEpoch) return;
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

// ===== generation bots =====

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

// The Architect + the Builder: meeting -> ForgeSpec -> running AI employee.
// The spec is extracted by the LLM but validated/defaulted in code
// (llm.js validateForgeSpec); the agent process itself never lets the LLM
// touch a number (agent-template/server.py: understand -> decide -> speak).
async function runSpecBot() {
  if (state.busy.spec) return;
  const epoch = meetingEpoch;
  state.busy.spec = true;
  try {
    const tail = state.transcript.slice(-30).map((t) => t.text).join("\n");
    const spec = await extractForgeSpec(state.notes, tail);
    if (epoch !== meetingEpoch) return;
    const changed = JSON.stringify(spec) !== JSON.stringify(state.spec);
    state.spec = spec;
    if (changed) {
      state.sandboxStale = Boolean(state.sandboxUrl);
      await deployLocalAgent(spec);
    }
  } catch (err) {
    console.error("[spec]", err.message);
    if (epoch === meetingEpoch) state.lastError = `spec: ${err.message}`;
  } finally {
    state.busy.spec = false;
  }
}

// ===== the local AI employee (instant test instance) =====
// One python process running the SAME agent-template that ships to the
// client sandbox. Local instance gets the Kimi env so the voice step is on;
// in the sandbox the agent runs template-only (no egress there) — either way
// every number comes from code.
const AGENT_PORT = Number(process.env.AGENT_PORT || 4300);
let agentChild = null;
let agentDir = null;

async function specDir(spec) {
  const dir = path.join(AGENTS_DIR, spec.slug);
  await mkdir(dir, { recursive: true });
  await writeFile(path.join(dir, "spec.json"), JSON.stringify(spec, null, 2));
  return dir;
}

async function deployLocalAgent(spec) {
  agentDir = await specDir(spec);
  if (agentChild) {
    agentChild.removeAllListeners("exit");
    agentChild.kill();
    agentChild = null;
  }
  const child = spawn(PYTHON, [AGENT_TEMPLATE], {
    cwd: agentDir,
    env: {
      ...process.env,
      PORT: String(AGENT_PORT),
      KIMI_API_KEY: config.kimiApiKey,
      KIMI_BASE_URL: config.kimiBaseUrl,
      KIMI_MODEL: config.kimiModel,
    },
  });
  child.stderr.on("data", (d) => console.error("[agent]", String(d).trim()));
  child.on("error", (e) => console.error("[agent] spawn:", e.message));
  child.on("exit", (code) => {
    if (agentChild === child) {
      agentChild = null;
      if (code) console.error(`[agent] exited ${code}`);
    }
  });
  agentChild = child;
  // wait for /health so previewUrl is only shown when the employee answers
  for (let i = 0; i < 20; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${AGENT_PORT}/health`, { signal: AbortSignal.timeout(500) });
      if (r.ok) {
        state.previewUrl = "/employee";
        console.log(`[agent] ${spec.persona.agent_name} for ${spec.business.name} live on :${AGENT_PORT}`);
        return;
      }
    } catch {}
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error("local agent did not come up on :" + AGENT_PORT);
}

// ===== auto-forge loop =====
// Notes refresh -> PRD + ForgeSpec (which re-stamps the local employee).
// The Daytona handover is explicit (/api/handover): sandboxes take ~a minute
// and belong to the moment the FDE sends the client THEIR link.
const FORGE_INTERVAL_MS = 15_000;
const MIN_NEW_CHARS_TO_FORGE = 120;
let forgedChars = 0;

async function forgeCycle(force = false) {
  const totalChars = state.transcript.reduce((n, t) => n + t.text.length, 0);
  if (totalChars === 0) return;
  if (!force && totalChars - forgedChars < MIN_NEW_CHARS_TO_FORGE) return;
  forgedChars = totalChars;
  state.lastForgeAt = Date.now();

  await refreshNotes();
  if (!state.notes.summary) return; // nothing understood yet
  await Promise.allSettled([runPrdBot(), runSpecBot()]);
}

setInterval(() => {
  if (state.autoForge) forgeCycle().catch((err) => console.error("[forge]", err.message));
}, FORGE_INTERVAL_MS);

// ===== tiny proxy to the local employee =====
async function agentProxy(res, agentPath, init) {
  try {
    const r = await fetch(`http://127.0.0.1:${AGENT_PORT}${agentPath}`, init);
    const body = await r.text();
    res.status(r.status).type(r.headers.get("content-type") || "text/plain").send(body);
  } catch {
    res
      .status(503)
      .type("html")
      .send("<p style='font-family:sans-serif'>Belum ada AI employee — jalankan meeting dulu, dia lahir dari situ. 🙂</p>");
  }
}

export function createApp() {
  const app = express();
  app.use(express.json({ limit: "12mb" }));

  app.get("/api/state", (req, res) => {
    res.json({
      notes: state.notes,
      busy: { ...state.busy, ui: false, deploy: false }, // legacy keys for the UI
      lastError: state.lastError,
      autoForge: state.autoForge,
      lastForgeAt: state.lastForgeAt,
      previewUrl: state.previewUrl,
      sandboxUrl: state.sandboxUrl,
      sandboxStale: state.sandboxStale,
      spec: state.spec,
      workflow: state.spec?.workflow || null,
      prdLen: state.prd?.length ?? 0,
      uiLen: 0,
      transcriptCount: state.transcript.length,
      daytonaConfigured: isDaytonaConfigured(),
      whisperConfigured: Boolean(config.nosanaWhisperUrl),
      outputType: "chatbot", // the Forge makes AI employees, nothing else
    });
  });

  app.get("/api/prd", (req, res) => {
    res.type("text/plain").send(state.prd ?? "");
  });

  // The confirm-screen seam: read the spec, or push a corrected one. A POSTed
  // spec goes through the same code validation as an extracted one, then the
  // local employee is re-stamped immediately.
  app.get("/api/spec", (req, res) => {
    res.json(state.spec ?? {});
  });
  app.post("/api/spec", async (req, res) => {
    try {
      const spec = validateForgeSpec(req.body, state.notes, JSON.stringify(state.notes));
      state.spec = spec;
      state.sandboxStale = Boolean(state.sandboxUrl);
      await deployLocalAgent(spec);
      res.json({ ok: true, spec });
    } catch (err) {
      res.status(400).json({ error: err.message });
    }
  });

  // Audio chunk -> Whisper (Nosana GPU) -> transcript. Bahasa-first: language
  // hint + a domain vocab prompt so QRIS/mutasi/GoFood come out clean.
  app.post("/api/audio", express.raw({ type: () => true, limit: "30mb" }), async (req, res) => {
    if (!config.nosanaWhisperUrl) return res.status(400).json({ error: "NOSANA_WHISPER_URL is not set" });
    if (!req.body?.length) return res.status(400).json({ error: "empty audio" });
    const speaker = String(req.query.speaker || "Speaker");
    const lang = String(req.query.lang || config.asrLanguage);
    try {
      const fd = new FormData();
      fd.append("audio_file", new Blob([req.body], { type: "audio/webm" }), "chunk.webm");
      let url = `${config.nosanaWhisperUrl}/asr?encode=true&task=transcribe&language=${encodeURIComponent(lang)}&output=json`;
      if (lang === "id") url += `&initial_prompt=${encodeURIComponent(config.asrPromptId)}`;
      const r = await fetch(url, { method: "POST", body: fd, signal: AbortSignal.timeout(60_000) });
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

  // A whole meeting transcript in one file (Otter/Fireflies/manual .txt).
  // The reliable path when live ASR is not good enough: upload, and the same
  // notes -> spec -> employee pipeline runs on it.
  app.post("/api/transcript/bulk", async (req, res) => {
    const raw = String(req.body?.text || "").trim();
    if (!raw) return res.status(400).json({ error: "text required" });
    // pack lines into ~500-char segments so the notes bot sees coherent chunks
    const lines = raw.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    const segments = [];
    let buf = "";
    for (const line of lines) {
      buf = buf ? `${buf}\n${line}` : line;
      if (buf.length >= 500) { segments.push(buf); buf = ""; }
    }
    if (buf) segments.push(buf);
    if (segments.length === 0) return res.status(400).json({ error: "no usable text" });
    for (const seg of segments) {
      state.transcript.push({ speaker: "upload", text: seg, at: Date.now(), analyzed: false });
      state.charsSinceNotes += seg.length;
    }
    res.json({ ok: true, lines: segments.length });
    try {
      await refreshNotes();
      await forgeCycle(true);
    } catch (e) {
      console.error("[bulk]", e.message);
    }
  });

  app.post("/api/notes/refresh", async (req, res) => {
    await refreshNotes();
    res.json({ ok: true, notes: state.notes });
  });

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

  // THE HANDOVER: clone the employee into the client's own isolated sandbox
  // and return the link that goes to THEIR phone. Re-running it re-stamps the
  // same client's sandbox (slug-keyed), so refine -> handover -> refine works.
  app.post("/api/handover", async (req, res) => {
    if (!state.spec) return res.status(400).json({ error: "no spec forged yet" });
    if (state.busy.sandbox) return res.status(409).json({ error: "handover already running" });
    if (!isDaytonaConfigured()) return res.status(400).json({ error: "DAYTONA_API_KEY is not set" });
    state.busy.sandbox = true;
    try {
      const dir = await specDir(state.spec);
      const { url, sandboxId } = await forgeSandbox(dir);
      state.sandboxUrl = url;
      state.sandboxId = sandboxId;
      state.sandboxStale = false;
      res.json({ ok: true, url, sandboxId });
    } catch (err) {
      console.error("[handover]", err.message);
      state.lastError = `handover: ${err.message}`;
      res.status(502).json({ error: err.message });
    } finally {
      state.busy.sandbox = false;
    }
  });

  // The local employee, proxied: page + chat + health.
  app.get("/employee", (req, res) => agentProxy(res, "/", {}));
  app.post("/chat", express.json(), (req, res) =>
    agentProxy(res, "/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req.body || {}),
    })
  );

  app.post("/api/reset", (req, res) => {
    meetingEpoch += 1;
    state.transcript = [];
    state.notes = structuredClone(EMPTY_NOTES);
    state.prd = null;
    state.spec = null;
    state.previewUrl = null;
    state.sandboxUrl = null;
    state.sandboxId = null;
    state.sandboxStale = false;
    state.lastError = null;
    state.charsSinceNotes = 0;
    state.lastForgeAt = null;
    forgedChars = 0;
    if (agentChild) {
      agentChild.kill();
      agentChild = null;
    }
    res.json({ ok: true });
  });

  app.use(express.static(path.join(__dirname, "..", "public")));
  return app;
}

// Warm the Whisper model so the meeting's first words never eat the cold start.
async function warmUpWhisper() {
  if (!config.nosanaWhisperUrl) return;
  try {
    const ping = await fetch(`${config.nosanaWhisperUrl}/docs`, { signal: AbortSignal.timeout(10_000) });
    if (!ping.ok) {
      console.warn(`[whisper] service not ready (${ping.status}) — Nosana container may be restarting`);
      return;
    }
    const silenceWav = Buffer.concat([
      Buffer.from("RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00", "binary"),
      Buffer.from([0x80, 0x3e, 0, 0, 0, 0x7d, 0, 0, 2, 0, 0x10, 0]),
      Buffer.from("data", "binary"),
      Buffer.alloc(4 + 9600),
    ]);
    const fd = new FormData();
    fd.append("audio_file", new Blob([silenceWav], { type: "audio/wav" }), "warmup.wav");
    const t0 = Date.now();
    await fetch(`${config.nosanaWhisperUrl}/asr?encode=true&task=transcribe&language=${config.asrLanguage}&output=json`, {
      method: "POST", body: fd, signal: AbortSignal.timeout(120_000),
    });
    console.log(`[whisper] warm (${Date.now() - t0}ms)`);
  } catch (err) {
    console.warn("[whisper] warm-up failed:", err.message);
  }
}

const app = createApp();
app.listen(config.port, () => {
  console.log(`[forge] listening on http://localhost:${config.port}`);
  if (!config.kimiApiKey) console.warn("[forge] KIMI_API_KEY belum diisi — fitur AI belum aktif");
  if (!isDaytonaConfigured()) console.warn("[forge] DAYTONA_API_KEY belum diisi — handover sandbox nonaktif");
  warmUpWhisper();
  setInterval(warmUpWhisper, 5 * 60 * 1000);
});

for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    if (agentChild) agentChild.kill();
    process.exit(0);
  });
}
