import "dotenv/config";

export const config = {
  kimiBaseUrl: (process.env.KIMI_BASE_URL || "https://api.moonshot.ai/v1").replace(/\/$/, ""),
  kimiApiKey: process.env.KIMI_API_KEY || "",
  kimiModel: process.env.KIMI_MODEL || "kimi-k2-0711-preview",
  // Heavier model for the quality-critical, infrequent calls (PRD, UI
  // prototype); the fast kimiModel keeps handling the every-few-seconds
  // incremental notes.
  kimiSmartModel: process.env.KIMI_SMART_MODEL || process.env.KIMI_MODEL || "kimi-k2-0711-preview",
  // UI generation wants THROUGHPUT, not deep thinking — a fast non-thinking
  // model turns a 5-minute wait into ~1 minute at comparable UI quality.
  kimiUiModel: process.env.KIMI_UI_MODEL || process.env.KIMI_MODEL || "kimi-k2-0711-preview",

  // Whisper ASR service running on a Nosana GPU deployment (whisper-asr-webservice).
  // When set, the frontend records real audio and transcribes via this endpoint
  // instead of the browser's Web Speech API.
  nosanaWhisperUrl: (process.env.NOSANA_WHISPER_URL || "").replace(/\/$/, ""),

  daytonaApiKey: process.env.DAYTONA_API_KEY || "",
  daytonaApiUrl: (process.env.DAYTONA_API_URL || "https://app.daytona.io/api").replace(/\/$/, ""),

  port: Number(process.env.PORT ?? 4100),
};
