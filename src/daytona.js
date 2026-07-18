import { spawn, spawnSync } from "node:child_process";
import { writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config } from "./config.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");
const DATA_DIR = path.join(ROOT, "data");

// The Daytona Python SDK is already proven working on this machine (the
// daytona-test project) — reuse it via a small script instead of
// re-implementing the REST API in Node. Override with NOTULA_PYTHON if the
// venv lives elsewhere.
const PYTHON = process.env.NOTULA_PYTHON || "python3";

export function isDaytonaConfigured() {
  return Boolean(config.daytonaApiKey);
}

/**
 * Deploys the generated single-file UI into a Daytona sandbox and returns a
 * signed preview URL. Reuses one sandbox across deploys (id cached in
 * data/.sandbox_id) so repeat generations are fast.
 */
let sdkOk = null; // checked once per process

export async function deployPreview(html) {
  if (!isDaytonaConfigured()) throw new Error("DAYTONA_API_KEY is not set in .env");
  if (sdkOk === null) sdkOk = spawnSync(PYTHON, ["-c", "import daytona"]).status === 0;
  if (!sdkOk) throw new Error("daytona SDK not installed — showing local preview instead (pip install daytona)");

  await mkdir(DATA_DIR, { recursive: true });
  const htmlPath = path.join(DATA_DIR, "prototype.html");
  await writeFile(htmlPath, html);

  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [path.join(ROOT, "scripts", "daytona_deploy.py"), htmlPath], {
      env: { ...process.env, DAYTONA_API_KEY: config.daytonaApiKey },
    });
    let out = "";
    let err = "";
    // Without this, a missing Python binary emits an unhandled 'error' event
    // and takes down the whole server mid-forge.
    child.on("error", (e) => reject(new Error(`spawn ${PYTHON}: ${e.message}`)));
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (err += d));
    child.on("close", (code) => {
      if (code !== 0) return reject(new Error(`daytona_deploy.py exit ${code}: ${err.slice(-400)}`));
      try {
        const lastLine = out.trim().split("\n").pop();
        resolve(JSON.parse(lastLine)); // { url, sandboxId }
      } catch {
        reject(new Error(`unreadable output: ${out.slice(-200)}`));
      }
    });
  });
}
