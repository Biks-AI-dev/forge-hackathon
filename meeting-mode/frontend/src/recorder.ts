// Browser audio capture (PRD §2 item 1 / finalized architecture #1-2):
// record -> stop -> blob, with the upload fallback path when recording
// is unsupported or permission is denied. No live transcription here —
// MediaRecorder only ever produces a finished blob on stop, same as the
// upload fallback; there is no streaming ASR anywhere in this build.

export type RecorderState = "idle" | "requesting-permission" | "recording" | "stopped";

export interface RecorderCallbacks {
  onStateChange: (state: RecorderState) => void;
  onTick: (elapsedMs: number) => void;
  onStopped: (blob: Blob) => void;
  onPermissionDenied: (reason: string) => void;
}

export function canRecordInBrowser(): boolean {
  return !!navigator.mediaDevices?.getUserMedia && typeof window.MediaRecorder !== "undefined";
}

export class BrowserRecorder {
  private mediaRecorder: MediaRecorder | null = null;
  private mediaStream: MediaStream | null = null;
  private chunks: Blob[] = [];
  private startTime = 0;
  private tickInterval: number | null = null;
  private state: RecorderState = "idle";

  constructor(private callbacks: RecorderCallbacks) {}

  async start(): Promise<void> {
    if (!canRecordInBrowser()) {
      this.callbacks.onPermissionDenied("This browser does not support in-browser recording.");
      return;
    }

    this.setState("requesting-permission");
    try {
      // Permission is requested only here — on explicit user interaction,
      // never on page load (finalized architecture requirement).
      this.mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      this.setState("idle");
      this.callbacks.onPermissionDenied("Microphone permission was denied.");
      return;
    }

    this.chunks = [];
    this.mediaRecorder = new MediaRecorder(this.mediaStream);
    this.mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) this.chunks.push(e.data);
    };
    this.mediaRecorder.onstop = () => {
      const blob = new Blob(this.chunks, { type: this.mediaRecorder?.mimeType || "audio/webm" });
      this.mediaStream?.getTracks().forEach((t) => t.stop());
      this.stopTicking();
      this.setState("stopped");
      this.callbacks.onStopped(blob);
    };

    this.mediaRecorder.start();
    this.startTime = Date.now();
    this.setState("recording");
    this.tickInterval = window.setInterval(() => {
      this.callbacks.onTick(Date.now() - this.startTime);
    }, 250);
  }

  stop(): void {
    if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
      this.mediaRecorder.stop();
    }
  }

  cancel(): void {
    if (this.mediaRecorder && this.mediaRecorder.state !== "inactive") {
      this.mediaRecorder.stop();
    }
    this.mediaStream?.getTracks().forEach((t) => t.stop());
    this.stopTicking();
    this.chunks = [];
    this.setState("idle");
  }

  private stopTicking(): void {
    if (this.tickInterval !== null) {
      window.clearInterval(this.tickInterval);
      this.tickInterval = null;
    }
  }

  private setState(state: RecorderState): void {
    this.state = state;
    this.callbacks.onStateChange(state);
  }

  getState(): RecorderState {
    return this.state;
  }
}

export function formatDuration(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const m = String(Math.floor(totalSeconds / 60)).padStart(2, "0");
  const s = String(totalSeconds % 60).padStart(2, "0");
  return `${m}:${s}`;
}
