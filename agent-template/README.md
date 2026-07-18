# agent-template — PLACEHOLDER

Owned by Dev B (PRD §4.2/4.3/4.4). `server.py` here is a stub the Provisioner
uses for testing until the real sandbox agent lands. Replace it freely.

## Runtime contract the Provisioner relies on

Declared in `forge.manifest.json`, read by the Provisioner — change the
manifest, not the Provisioner, if the contract needs to shift:

- `start_command` — how to boot the server (defaults to `python3 server.py`)
- `install_command` — run once before start if dependencies need installing (`null` = skip)
- `port` — the Provisioner sets `PORT` in the sandbox env to this value before starting; the server must read it from there
- `health_path` — polled until it returns HTTP 200; must not require auth
- `env_passthrough` — names of env vars (secrets) the Provisioner forwards from its own environment into the sandbox via `sandbox.update_env`, never written to any file

## Files the Provisioner injects at forge time (not committed here)

- `spec.json` — the validated ForgeSpec for this business
- env vars listed in `env_passthrough` (e.g. `KIMI_API_KEY`, `KIMI_BASE_URL`)

Everything else in this directory is copied into the sandbox verbatim,
except `forge.manifest.json` and `README.md` themselves.
