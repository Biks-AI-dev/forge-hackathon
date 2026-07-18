// Mirrors meeting-mode/app/job_store.py JobState + Job.to_public_dict()
// and app/main.py's /research response shapes. Keep in sync by hand —
// this is a small enough surface that a shared schema generator would be
// more ceremony than it's worth for a hackathon build.

export type JobState =
  | "idle"
  | "recording"
  | "uploading"
  | "transcribing"
  | "reading_files"
  | "extracting_forgespec"
  | "validating_forgespec"
  | "provisioning"
  | "ready"
  | "failed";

export interface FieldError {
  field: string;
  message: string;
}

export interface JobError {
  error: string;
  message: string;
  details: FieldError[];
}

export interface Job {
  job_id: string;
  state: JobState;
  error: JobError | null;
  transcript_provider: string | null;
  transcript_duration_s: number | null;
  transcript_preview: string | null;
  resolved_business_name: string | null;
  resolved_workflow: string | null;
  chat_url: string | null;
  sandbox_id: string | null;
  slug: string | null;
  elapsed_ms: number | null;
  replaced_sandbox_id: string | null;
}

export interface ApiError {
  error: string;
  message: string;
  details: FieldError[];
}

export interface ResearchSummary {
  business_name: string | null;
  description: string;
  source_urls: string[];
}

export interface ResearchResult {
  research_id: string;
  status: "ok" | "skipped";
  warning: string | null;
  summary?: ResearchSummary;
}
