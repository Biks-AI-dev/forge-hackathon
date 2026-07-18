import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config } from "./config.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");

// The Daytona Python SDK is proven on this machine — reuse it via a small
// script instead of re-implementing the REST API in Node. Override with
// NOTULA_PYTHON if the interpreter lives elsewhere.
const PYTHON = process.env.NOTULA_PYTHON || (process.platform === "win32" ? "python" : "python3");

export function isDaytonaConfigured() {
  return Boolean(config.daytonaApiKey);
}

let sdkOk = null; // checked once per process

/**
 * THE HANDOVER: clone the AI employee into the client's own isolated Daytona
 * sandbox. agentDir must contain spec.json; the provisioner uploads it with
 * agent-template/server.py, starts the server, and returns { url, sandboxId }.
 * Slug-keyed sandbox reuse lives in the python script (.sandbox_id in agentDir),
 * so re-handover after a spec refinement re-stamps the SAME client sandbox.
 */
export async function forgeSandbox(agentDir) {
  if (!isDaytonaConfigured()) throw new Error("DAYTONA_API_KEY is not set in .env");
  if (sdkOk === null) sdkOk = spawnSync(PYTHON, ["-c", "import daytona"]).status === 0;
  if (!sdkOk) throw new Error("daytona SDK not installed (pip install daytona)");

  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, [path.join(ROOT, "scripts", "forge_daytona.py"), agentDir], {
      env: { ...process.env, DAYTONA_API_KEY: config.daytonaApiKey, DAYTONA_API_URL: config.daytonaApiUrl },
    });
    let out = "";
    let err = "";
    child.on("error", (e) => reject(new Error(`spawn ${PYTHON}: ${e.message}`)));
    child.stdout.on("data", (d) => (out += d));
    child.stderr.on("data", (d) => (err += d));
    child.on("close", (code) => {
      if (code !== 0) return reject(new Error(`forge_daytona.py exit ${code}: ${err.slice(-400)}`));
      try {
        resolve(JSON.parse(out.trim().split("\n").pop()));
      } catch {
        reject(new Error(`unreadable output: ${out.slice(-200)}`));
      }
    });
  });
}
