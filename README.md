# Infriqa Triage Agent

Tiny FastAPI AI app for **Infriqa Managed** + **AI Operations** testing.

- One agent: `triage-bot`
- One LLM call path that uses Managed-injected Azure OpenAI / `OPENAI_*` env
- Deploy-safe: `/health`, binds `0.0.0.0:$PORT`, single container

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Managed health probe |
| GET | `/` | App info |
| POST | `/agent/triage` | Support triage agent |

### Example

```bash
curl -s https://YOUR-APP-dev.infriqa.ai/health

curl -s -X POST https://YOUR-APP-dev.infriqa.ai/agent/triage \
  -H "Content-Type: application/json" \
  -d '{"message":"Latency spiked after last deploy for support answers","agent_id":"triage-bot"}'
```

## Deploy on Infriqa Managed (recommended)

1. Push this repo to GitHub (public or connected via GitHub App).
2. In Infriqa → Deploy / Managed hosting → select this repo.
3. Use Free / Managed plan with AI Actions if available.
4. **Leave Model API key / OpenAI key blank** so platform can inject Managed Azure OpenAI.
5. Optional env (or leave default): `INFRIQA_AI_MODE=managed`
6. Deploy and wait for live URL `https://<name>-dev.infriqa.ai`.
7. Call `/health` then `/agent/triage`.
8. In **AI Operations**, register agent `triage-bot` bound to this application and explore signals / cost / recommendations as a user.

## Local run

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
# optional local key for BYOK only:
# set OPENAI_API_KEY=...
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Why this shape

- Avoids CRA/Vite monorepo deploy failures
- Single Python container matches Infriqa Managed golden pattern
- Does not hardcode OpenAI keys (Managed inject can fill blank keys)
