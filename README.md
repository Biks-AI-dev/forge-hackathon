# 📝 Notula — AI Meeting Scribe

**Hackathon project: Daytona × Nosana × Kimi (via ai&).**
Talk to your client → live transcript → structured notes → a full PRD → a clickable UI prototype deployed to a Daytona sandbox — all before the meeting ends.

## Architecture

```
🎤 Web Speech API (browser, en-US)
      │  transcript chunks
      ▼
Express server (in-memory meeting state)
      │
      ├─ updateNotes()   ──► Kimi (OpenAI-compatible endpoint) — incremental structured notes
      ├─ generatePRD()   ──► Kimi — full markdown PRD
      ├─ generateUI()    ──► Kimi — single-file HTML prototype
      │
      └─ deployPreview() ──► Daytona sandbox (python -m http.server) → signed preview URL
```

- **LLM endpoint**: OpenAI-compatible — point `KIMI_BASE_URL` at ai& (or any Nosana deployment). Model is one env var (`KIMI_MODEL`).
- **Daytona**: the prototype is served from a sandbox (one sandbox reused across deploys, id cached in `data/.sandbox_id`). Without a Daytona key, preview still works locally at `/preview`.
- **Incremental notes**: every ~220 new transcript chars, the server sends *current notes + the new chunk* to the LLM (not the whole transcript) — token cost stays constant no matter how long the meeting runs.

## Setup

```bash
cp .env.example .env    # fill KIMI_API_KEY (+ DAYTONA_API_KEY if you have one)
npm install
npm start               # open http://localhost:4100 in Chrome (Web Speech API)
```

## Demo flow

1. **▶ Start Recording** — just talk; toggle the speaker button when the client speaks (🎙 Us ↔ 🧑‍💼 Client)
2. The middle panel fills itself: key points, requirements, action items, decisions
3. **📄 Generate PRD** — complete document (MoSCoW table, user flows, MVP scope, risks)
4. **🎨 Generate UI** — a clickable web prototype built from the requirements, shown in the Prototype tab
5. **🚀 Daytona** — the prototype goes live in a sandbox with a shareable URL you can open on the client's phone

## Structure

```
src/server.js        # Express + meeting state + endpoints
src/llm.js           # LLM client (chat, updateNotes, generatePRD, generateUI)
src/daytona.js       # prototype deploy → Daytona (via scripts/daytona_deploy.py)
scripts/daytona_deploy.py
public/index.html    # 3-panel UI + mic + preview (no build step)
```

## Known issue

`moonshotai/kimi-k2.7-code` on ai& currently returns degenerate output (repeated `!` until max_tokens, `finish_reason: length`, fingerprint `vllm-0.25.0-tp8-74c6b39d`) — reported; running on `deepseek-ai/deepseek-v4-flash` from the same endpoint until it's fixed. Switching back is a one-line `.env` change.
