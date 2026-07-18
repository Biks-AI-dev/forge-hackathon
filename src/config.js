import "dotenv/config";

export const config = {
  kimiBaseUrl: (process.env.KIMI_BASE_URL || "https://api.moonshot.ai/v1").replace(/\/$/, ""),
  kimiApiKey: process.env.KIMI_API_KEY || "",
  kimiModel: process.env.KIMI_MODEL || "kimi-k2-0711-preview",
  // Heavier model for the quality-critical, infrequent calls (PRD); the fast
  // kimiModel handles the every-few-seconds incremental notes + spec extraction.
  kimiSmartModel: process.env.KIMI_SMART_MODEL || process.env.KIMI_MODEL || "kimi-k2-0711-preview",

  // Whisper ASR service running on a Nosana GPU deployment (whisper-asr-webservice).
  nosanaWhisperUrl: (process.env.NOSANA_WHISPER_URL || "").replace(/\/$/, ""),
  // Bahasa-first: meetings are held in Indonesian unless overridden per-request
  // (?lang=en on /api/audio) or via env.
  asrLanguage: process.env.ASR_LANGUAGE || "id",
  // Domain vocabulary fed to Whisper as initial_prompt so Indonesian SME terms
  // transcribe correctly instead of being mangled into English lookalikes.
  asrPromptId:
    process.env.ASR_PROMPT_ID ||
    "Rapat bisnis UMKM: rekonsiliasi, mutasi BCA, closing, QRIS, GoFood, GrabFood, ShopeeFood, " +
    "transfer, settlement, omzet, outlet, kasir, SPG, admin, nasi box, catering, selisih.",

  daytonaApiKey: process.env.DAYTONA_API_KEY || "",
  daytonaApiUrl: (process.env.DAYTONA_API_URL || "https://app.daytona.io/api").replace(/\/$/, ""),

  port: Number(process.env.PORT ?? 4100),
};
