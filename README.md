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

## AI Ops telemetry (why OTel was empty)

Managed deploy injects LLM keys, but **does not auto-emit OTel**.
This app posts a tagged LLM span after each `/agent/triage` call to:

`POST {INFRIQA_AGENT_OPS_BASE_URL}/api/agent-ops/traces/ingest`

Required span tags: `application_id`, `agent_id`, `environment`, `attribution_method`.

Set these Managed env vars (Advanced environment):

| Key | Value |
| --- | --- |
| `INFRIQA_AGENT_OPS_BASE_URL` | Same origin as AI Ops UI (QA: `https://test.aignosticlabs.ai`) |
| `APPLICATION_ID` | `infriqa-triage-agent` |
| `AGENT_ID` | your registered agent id (e.g. `agent-aadf22cd6e8a`) |
| `ENVIRONMENT` | `dev` |
| `INFRIQA_TRACE_EMIT` | `1` |

Platform flag `AGENT_TRACE_ENABLED` must be on (already on for QA).
Cost rollups also need an AI telemetry **data source** (Log Analytics) — separate from Agent Ops traces.

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
