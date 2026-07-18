import type { ApiError, Job, ResearchResult } from "./types";

export class ApiRequestError extends Error {
  constructor(
    public status: number,
    public body: ApiError,
  ) {
    super(body.message);
  }
}

async function parseOrThrow<T>(resp: Response): Promise<T> {
  const body = await resp.json();
  if (!resp.ok) throw new ApiRequestError(resp.status, body as ApiError);
  return body as T;
}

export interface CreateJobParams {
  sessionId: string;
  audio: Blob;
  audioFilename: string;
  clientFiles: File[];
  continuationOf?: string | null;
  researchId?: string | null;
}

export async function createJob(params: CreateJobParams): Promise<{ job_id: string }> {
  const form = new FormData();
  form.append("session_id", params.sessionId);
  form.append("audio", params.audio, params.audioFilename);
  for (const f of params.clientFiles) form.append("client_files", f);
  if (params.continuationOf) form.append("continuation_of", params.continuationOf);
  if (params.researchId) form.append("research_id", params.researchId);

  const resp = await fetch("/jobs", { method: "POST", body: form });
  return parseOrThrow(resp);
}

export async function getJob(jobId: string): Promise<Job> {
  const resp = await fetch(`/jobs/${jobId}`);
  return parseOrThrow(resp);
}

export async function createResearch(companyName: string, website: string): Promise<ResearchResult> {
  const form = new FormData();
  if (companyName) form.append("company_name", companyName);
  if (website) form.append("website", website);
  const resp = await fetch("/research", { method: "POST", body: form });
  return parseOrThrow(resp);
}
