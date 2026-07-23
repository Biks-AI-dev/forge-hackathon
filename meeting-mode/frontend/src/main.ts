import "./style.css";
import { ApiRequestError, createJob, createResearch, getJob } from "./api";
import { BrowserRecorder, canRecordInBrowser, formatDuration } from "./recorder";
import type { Job, JobState } from "./types";

// ---- DOM handles -----------------------------------------------------

function el<T extends HTMLElement>(id: string): T {
  const found = document.getElementById(id);
  if (!found) throw new Error(`missing #${id}`);
  return found as T;
}

const heroTitle = el<HTMLDivElement>("hero-title");
const heroSubtitle = el<HTMLDivElement>("hero-subtitle");
const heroTimerLabel = el<HTMLDivElement>("hero-timer-label");
const heroTimer = el<HTMLDivElement>("hero-timer");
const waveform = el<HTMLDivElement>("waveform");
const listenStatusText = el<HTMLSpanElement>("listen-status-text");
const listenContent = el<HTMLDivElement>("listen-content");
const btnRecord = el<HTMLButtonElement>("btn-record");
const btnStop = el<HTMLButtonElement>("btn-stop");
const btnCancel = el<HTMLButtonElement>("btn-cancel");
const btnUseUpload = el<HTMLButtonElement>("btn-use-upload");
const audioUploadWrap = el<HTMLDivElement>("audio-upload-wrap");
const audioUploadInput = el<HTMLInputElement>("audio-upload");
const fallbackReason = el<HTMLParagraphElement>("fallback-reason");
const playbackWrap = el<HTMLDivElement>("playback-wrap");
const playbackAudio = el<HTMLAudioElement>("playback-audio");
const btnDiscard = el<HTMLButtonElement>("btn-discard");

const researchCompany = el<HTMLInputElement>("research-company");
const researchWebsite = el<HTMLInputElement>("research-website");
const btnResearch = el<HTMLButtonElement>("btn-research");
const researchStatus = el<HTMLParagraphElement>("research-status");
const researchPreview = el<HTMLDivElement>("research-preview");

const clientFilesInput = el<HTMLInputElement>("client-files");
const btnSubmit = el<HTMLButtonElement>("btn-submit");
const resultCard = el<HTMLDivElement>("result-card");

// ---- Session / app state ----------------------------------------------

const SESSION_ID = (() => {
  let s = sessionStorage.getItem("meeting_mode_session_id");
  if (!s) {
    s = crypto.randomUUID();
    sessionStorage.setItem("meeting_mode_session_id", s);
  }
  return s;
})();

let recordedBlob: Blob | null = null;
let uploadedAudioFile: File | null = null;
let continuationOfJobId: string | null = null;
let researchId: string | null = null;
let submitting = false;
let pollTimer: number | null = null;
let pipelineStartMs = 0;
let pipelineTicker: number | null = null;

const STEP_ORDER: JobState[] = [
  "uploading",
  "transcribing",
  "reading_files",
  "mapping_topics",
  "extracting_forgespec",
  "validating_forgespec",
  "provisioning",
  "ready",
];

const STATE_LABELS: Record<JobState, string> = {
  idle: "idle",
  recording: "listening…",
  uploading: "uploading…",
  transcribing: "transcribing…",
  reading_files: "reading files…",
  mapping_topics: "mapping topics…",
  extracting_forgespec: "generating ForgeSpec…",
  validating_forgespec: "validating…",
  provisioning: "provisioning sandbox…",
  ready: "done",
  failed: "failed",
};

// ---- Recorder -----------------------------------------------------------

const recorder = new BrowserRecorder({
  onStateChange: (state) => {
    if (state === "recording") {
      setHero("recording");
      btnRecord.style.display = "none";
      btnStop.style.display = "inline-flex";
      btnCancel.style.display = "inline-flex";
      playbackWrap.style.display = "none";
    } else if (state === "requesting-permission") {
      btnRecord.disabled = true;
    } else if (state === "idle") {
      btnRecord.disabled = false;
      btnStop.style.display = "none";
      btnCancel.style.display = "none";
    }
  },
  onTick: (elapsedMs) => {
    heroTimer.textContent = formatDuration(elapsedMs);
  },
  onStopped: (blob) => {
    recordedBlob = blob;
    btnStop.style.display = "none";
    btnCancel.style.display = "none";
    btnRecord.disabled = false;
    playbackWrap.style.display = "block";
    playbackAudio.src = URL.createObjectURL(blob);
    updateSubmitEnabled();

    if (hasReadyInputs()) {
      maybeAutoSubmit(); // stop -> forge automatically, no extra click needed
    } else {
      setHero("idle", "Recording captured — attach client files above to forge automatically.");
    }
  },
  onPermissionDenied: (reason) => {
    showUploadFallback(reason);
  },
});

if (!canRecordInBrowser()) {
  showUploadFallback("This browser does not support in-browser recording — upload a finished recording instead.");
} else {
  btnUseUpload.style.display = "inline-flex";
}

function showUploadFallback(reason: string): void {
  audioUploadWrap.style.display = "block";
  fallbackReason.textContent = reason;
  btnRecord.style.display = canRecordInBrowser() ? "inline-flex" : "none";
}

btnUseUpload.addEventListener("click", () => showUploadFallback("Upload a finished recording instead of using the microphone."));
btnRecord.addEventListener("click", () => recorder.start());
btnStop.addEventListener("click", () => recorder.stop());
btnCancel.addEventListener("click", () => {
  recorder.cancel();
  setHero("idle");
});
btnDiscard.addEventListener("click", () => {
  recordedBlob = null;
  playbackWrap.style.display = "none";
  setHero("idle");
  updateSubmitEnabled();
});
audioUploadInput.addEventListener("change", (e) => {
  uploadedAudioFile = (e.target as HTMLInputElement).files?.[0] ?? null;
  updateSubmitEnabled();
  maybeAutoSubmit(); // same auto-forge behavior for the upload fallback path
});
clientFilesInput.addEventListener("change", () => {
  updateSubmitEnabled();
  maybeAutoSubmit(); // audio may already be waiting on these files
});

// ---- Hero card rendering (the "Voice quote"-style panel) --------------

function setHero(mode: "idle" | "recording" | JobState, contentOverride?: string): void {
  const label = mode === "idle" ? "idle" : STATE_LABELS[mode as JobState] ?? mode;
  listenStatusText.textContent = label;
  waveform.classList.toggle("idle", mode === "idle" || mode === "ready" || mode === "failed");

  if (mode === "idle") {
    heroTitle.textContent = "Meeting Mode";
    heroSubtitle.textContent = "Ready to record";
    heroTimerLabel.textContent = "elapsed";
    heroTimer.textContent = "00:00";
    listenContent.textContent = contentOverride ?? "Click record to start, or upload a finished recording below.";
    listenContent.classList.add("placeholder");
    return;
  }
  listenContent.classList.remove("placeholder");

  if (mode === "recording") {
    heroTitle.textContent = "Meeting Mode";
    heroSubtitle.textContent = "Recording in progress";
    heroTimerLabel.textContent = "elapsed";
    listenContent.textContent = "Recording — it will be transcribed once you stop. (No live captions in this build.)";
    return;
  }

  // Pipeline states
  heroTitle.textContent = "Meeting Mode";
  heroSubtitle.textContent = "Processing your meeting";
  heroTimerLabel.textContent = mode === "ready" ? "done in" : mode === "failed" ? "stopped at" : "processing";
}

function tickPipelineTimer(): void {
  heroTimer.textContent = formatDuration(Date.now() - pipelineStartMs);
}

function startPipelineTimer(): void {
  pipelineStartMs = Date.now();
  tickPipelineTimer();
  pipelineTicker = window.setInterval(tickPipelineTimer, 250);
}

function stopPipelineTimer(): void {
  if (pipelineTicker !== null) {
    window.clearInterval(pipelineTicker);
    pipelineTicker = null;
  }
}

setHero("idle");

// ---- Pre-meeting research -----------------------------------------------

function escapeHtml(s: string): string {
  const div = document.createElement("div");
  div.textContent = s;
  return div.innerHTML;
}

function renderResearchPreview(
  outcome: "empty" | "loading" | "ok" | "skipped" | "error",
  data?: { businessName: string | null; description: string; sourceUrls: string[] } | { message: string },
): void {
  if (outcome === "empty") {
    researchPreview.innerHTML = `<p class="hint" id="research-preview-empty">No research run yet.</p>`;
    return;
  }
  if (outcome === "loading") {
    researchPreview.innerHTML = `<p class="hint">Researching…</p>`;
    return;
  }
  if (outcome === "skipped" || outcome === "error") {
    const message = (data as { message: string }).message;
    researchPreview.innerHTML = `
      <span class="research-preview-badge skipped">${outcome === "error" ? "Failed" : "Skipped"}</span>
      <p class="hint" style="margin-top:0">${escapeHtml(message)}</p>`;
    return;
  }
  const d = data as { businessName: string | null; description: string; sourceUrls: string[] };
  researchPreview.innerHTML = `
    <span class="research-preview-badge ok">Found</span>
    <p class="research-preview-name">${escapeHtml(d.businessName ?? "(unnamed business)")}</p>
    ${d.description ? `<p class="research-preview-desc">${escapeHtml(d.description)}</p>` : ""}
    ${d.sourceUrls.length
      ? `<ul class="research-preview-sources">${d.sourceUrls.map((u) => `<li><a href="${escapeHtml(u)}" target="_blank" rel="noopener">${escapeHtml(u)}</a></li>`).join("")}</ul>`
      : ""}`;
}

btnResearch.addEventListener("click", async () => {
  const companyName = researchCompany.value.trim();
  const website = researchWebsite.value.trim();
  if (!companyName && !website) {
    researchStatus.textContent = "Enter a company name or website first.";
    return;
  }
  btnResearch.disabled = true;
  researchStatus.textContent = "Researching…";
  renderResearchPreview("loading");
  try {
    const result = await createResearch(companyName, website);
    if (result.status === "ok" && result.summary) {
      researchId = result.research_id;
      researchStatus.textContent = "Will be used as background context — lowest priority vs. the meeting and client files.";
      renderResearchPreview("ok", {
        businessName: result.summary.business_name,
        description: result.summary.description,
        sourceUrls: result.summary.source_urls,
      });
    } else {
      researchId = null;
      researchStatus.textContent = "Meeting Mode works fine without it.";
      renderResearchPreview("skipped", { message: result.warning ?? "No usable result." });
    }
  } catch {
    researchId = null;
    researchStatus.textContent = "Continuing without it.";
    renderResearchPreview("error", { message: "Research request failed to reach the server." });
  } finally {
    btnResearch.disabled = false;
  }
});

// ---- Submit / poll -----------------------------------------------------
//
// Auto-forge (no extra click after Stop): the moment both a finished
// audio blob AND at least one client file are present, the pipeline
// starts on its own. This is the same "no manual confirmation" principle
// as the ForgeSpec auto-POST — extended to the UI trigger itself, not
// just the generated spec. The Forge button stays as a manual fallback
// (e.g. after "Discard & re-record" leaves things in a state auto-submit
// won't retry on its own).

function hasReadyInputs(): boolean {
  const hasAudio = !!(recordedBlob || uploadedAudioFile);
  const hasClientFiles = clientFilesInput.files !== null && clientFilesInput.files.length > 0;
  return hasAudio && hasClientFiles;
}

function maybeAutoSubmit(): void {
  if (submitting || !hasReadyInputs()) return;
  void submitJob();
}

function updateSubmitEnabled(): void {
  btnSubmit.disabled = !hasReadyInputs() || submitting;
}

function renderSteps(state: JobState): string {
  const idx = STEP_ORDER.indexOf(state);
  return STEP_ORDER
    .map((s) => {
      const liIdx = STEP_ORDER.indexOf(s);
      const cls = liIdx < idx ? "done" : liIdx === idx ? "active" : "";
      const label = s.replace(/_/g, " ");
      return `<li class="${cls}">${label}</li>`;
    })
    .join("");
}

function renderAgentRow(a: Job["agents"][number]): string {
  const uiBadge = a.ui_mode === "app" ? "app UI" : "chat";
  return `
    <div class="agent-row" style="padding:8px 0;border-top:1px solid rgba(127,127,127,.2)">
      <p style="margin:0 0 2px">
        <strong>${escapeHtml(a.workflow ?? "agent")}</strong>
        <span class="research-preview-badge ok" style="margin-left:6px">${uiBadge}</span>
        ${a.elapsed_ms != null ? `<span class="hint" style="margin-left:6px">${a.elapsed_ms} ms</span>` : ""}
      </p>
      <p style="margin:0"><a href="${escapeHtml(a.chat_url ?? "#")}" target="_blank" rel="noopener">${escapeHtml(a.chat_url ?? "")}</a></p>
      ${a.replaced_sandbox_id ? `<p class="hint" style="margin:2px 0 0">Replaced previous sandbox ${escapeHtml(a.replaced_sandbox_id)}</p>` : ""}
    </div>`;
}

function renderResult(job: Job): void {
  resultCard.style.display = "block";
  if (job.state === "ready") {
    // One card per forged agent (one per detected workflow); topics show
    // what the Router separated so a multi-topic meeting visibly does NOT
    // collapse into one spec.
    const agents = job.agents?.length
      ? job.agents
      : [{ workflow: job.resolved_workflow, ui_mode: "chat" as const, business_name: job.resolved_business_name,
           chat_url: job.chat_url, sandbox_id: job.sandbox_id, slug: job.slug,
           elapsed_ms: job.elapsed_ms, replaced_sandbox_id: job.replaced_sandbox_id }];
    const topicsHtml = job.topics?.length
      ? `<p class="hint" style="margin:6px 0 0">Detected topic(s): ${job.topics
          .map((t) => `<strong>${escapeHtml(t.workflow)}</strong> — ${escapeHtml(t.title)}${t.wants_app_ui ? " (asked for an app UI)" : ""}`)
          .join(" · ")}</p>`
      : "";
    resultCard.innerHTML = `
      <div class="success-box">
        <h3 style="margin-top:0">${agents.length > 1 ? `${agents.length} agents are live` : "Agent is live"}</h3>
        <p style="margin-bottom:2px"><strong>${escapeHtml(job.resolved_business_name ?? "")}</strong></p>
        ${topicsHtml}
        ${agents.map(renderAgentRow).join("")}
      </div>
      <div class="row" style="margin-top:14px">
        <button class="btn btn-secondary" id="btn-record-again">Record again</button>
      </div>`;
  } else if (job.state === "failed") {
    const details = job.error?.details ?? [];
    resultCard.innerHTML = `
      <div class="error-box">
        <strong>${job.error?.message ?? "Something went wrong"}</strong>
        ${details.length ? `<ul>${details.map((d) => `<li>${d.field}: ${d.message}</li>`).join("")}</ul>` : ""}
        <p style="margin-top:10px">Use <em>Record again</em> and clarify the information above.</p>
      </div>
      <div class="row" style="margin-top:14px">
        <button class="btn btn-secondary" id="btn-record-again">Record again</button>
      </div>`;
  }
  document.getElementById("btn-record-again")?.addEventListener("click", () => resetForRecordAgain(job.job_id));
}

function resetForRecordAgain(jobId: string): void {
  continuationOfJobId = jobId;
  recordedBlob = null;
  uploadedAudioFile = null;
  audioUploadInput.value = "";
  clientFilesInput.value = "";
  playbackWrap.style.display = "none";
  resultCard.style.display = "none";
  submitting = false;
  stopPipelineTimer();
  setHero("idle");
  updateSubmitEnabled();
}

async function pollJob(jobId: string): Promise<void> {
  if (pollTimer !== null) window.clearTimeout(pollTimer);
  const job = await getJob(jobId);

  setHero(job.state);
  if (job.transcript_preview) {
    listenContent.textContent = job.transcript_preview;
  }
  document.getElementById("steps-list")!.innerHTML = renderSteps(job.state);

  if (job.state === "ready" || job.state === "failed") {
    submitting = false;
    stopPipelineTimer();
    tickPipelineTimer();
    renderResult(job);
    return;
  }
  pollTimer = window.setTimeout(() => pollJob(jobId), 1500);
}

async function submitJob(): Promise<void> {
  if (submitting) return;
  submitting = true;
  btnSubmit.disabled = true;
  resultCard.style.display = "none";

  const audio = recordedBlob ?? uploadedAudioFile;
  if (!audio) {
    submitting = false;
    return;
  }
  const filename = recordedBlob ? "recording.webm" : (uploadedAudioFile as File).name;
  const clientFiles = clientFilesInput.files ? Array.from(clientFilesInput.files) : [];

  setHero("uploading");
  startPipelineTimer();

  try {
    const { job_id } = await createJob({
      sessionId: SESSION_ID,
      audio,
      audioFilename: filename,
      clientFiles,
      continuationOf: continuationOfJobId,
      researchId,
    });
    continuationOfJobId = null;
    pollJob(job_id);
  } catch (err) {
    submitting = false;
    stopPipelineTimer();
    resultCard.style.display = "block";
    if (err instanceof ApiRequestError) {
      const details = err.body.details ?? [];
      resultCard.innerHTML = `<div class="error-box"><strong>${err.body.message}</strong>${
        details.length ? `<ul>${details.map((d) => `<li>${d.field}: ${d.message}</li>`).join("")}</ul>` : ""
      }</div>`;
    } else {
      resultCard.innerHTML = `<div class="error-box">Could not reach the server: ${String(err)}</div>`;
    }
  }
}

btnSubmit.addEventListener("click", () => void submitJob());
