"""
Infriqa Triage Agent — minimal Managed-hosting AI app.

Designed for Infriqa Managed deploy:
- binds 0.0.0.0:$PORT
- GET /health for probes
- leaves LLM keys blank so Managed can inject platform Azure OpenAI
- one agent endpoint for AI Operations demos
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

APP_NAME = "infriqa-triage-agent"
APP_VERSION = "1.0.0"

app = FastAPI(title=APP_NAME, version=APP_VERSION)

_origins = [o.strip() for o in (os.getenv("ALLOWED_ORIGINS") or "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins if _origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class TriageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    agent_id: Optional[str] = Field(
        default="triage-bot",
        description="Logical agent id for AI Ops attribution demos",
    )


class TriageResponse(BaseModel):
    agent_id: str
    severity: str
    summary: str
    next_steps: list[str]
    model: str
    provider_mode: str
    raw: Optional[str] = None


def _provider_mode() -> str:
    mode = (os.getenv("INFRIQA_AI_MODE") or "").strip().lower()
    if mode:
        return mode
    if (os.getenv("AZURE_OPENAI_ENDPOINT") or "").strip():
        return "azure_openai"
    if (os.getenv("OPENAI_API_KEY") or "").strip():
        return "openai"
    return "unconfigured"


def _chat_client():
    """
    Prefer Managed-injected Azure OpenAI / OPENAI_* env.
    Supports:
      - OPENAI_API_KEY + OPENAI_BASE_URL (Managed injects Azure deployment URL)
      - AZURE_OPENAI_* classic vars
      - plain OpenAI when only OPENAI_API_KEY is set
    """
    try:
        from openai import OpenAI, AzureOpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is not installed") from exc

    api_key = (
        (os.getenv("OPENAI_API_KEY") or "").strip()
        or (os.getenv("AZURE_OPENAI_API_KEY") or "").strip()
    )
    if not api_key:
        raise RuntimeError(
            "No LLM API key configured. On Infriqa Managed, leave Model API key blank "
            "so platform Azure OpenAI can be injected (INFRIQA_AI_MODE=managed)."
        )

    azure_endpoint = (os.getenv("AZURE_OPENAI_ENDPOINT") or "").strip().rstrip("/")
    api_version = (
        (os.getenv("AZURE_OPENAI_API_VERSION") or os.getenv("OPENAI_API_VERSION") or "")
        .strip()
        or "2024-08-01-preview"
    )
    base_url = (os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE") or "").strip()

    # Managed often sets OPENAI_BASE_URL to .../openai/deployments/<name>
    if azure_endpoint:
        return AzureOpenAI(
            api_key=api_key,
            azure_endpoint=azure_endpoint,
            api_version=api_version,
        ), "azure"

    if base_url and "openai.azure.com" in base_url:
        # Strip /openai/deployments/<dep> so AzureOpenAI can use azure_endpoint cleanly
        m = re.match(r"^(https?://[^/]+)", base_url)
        endpoint = m.group(1) if m else base_url
        return AzureOpenAI(
            api_key=api_key,
            azure_endpoint=endpoint,
            api_version=api_version,
        ), "azure"

    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs), "openai"


def _chat_model() -> str:
    return (
        (os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT") or "").strip()
        or (os.getenv("AZURE_OPENAI_DEPLOYMENT") or "").strip()
        or (os.getenv("OPENAI_CHAT_MODEL") or "").strip()
        or (os.getenv("OPENAI_MODEL") or "").strip()
        or "gpt-4o-mini"
    )


def _parse_agent_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}
    return {}


@app.get("/")
def root():
    return {
        "app": APP_NAME,
        "version": APP_VERSION,
        "health": "/health",
        "agent": "POST /agent/triage",
        "provider_mode": _provider_mode(),
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "app": APP_NAME,
        "provider_mode": _provider_mode(),
        "llm_configured": bool(
            (os.getenv("OPENAI_API_KEY") or "").strip()
            or (os.getenv("AZURE_OPENAI_API_KEY") or "").strip()
        ),
    }


@app.get("/api/healthz")
def healthz():
    return health()


@app.post("/agent/triage", response_model=TriageResponse)
def triage(req: TriageRequest):
    """
    Single support-style agent: classify an ops/support message and suggest next steps.
    """
    try:
        client, kind = _chat_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    model = _chat_model()
    system = (
        "You are triage-bot, a support operations agent on Infriqa Managed hosting. "
        "Return ONLY compact JSON with keys: "
        "severity (one of: low, medium, high, critical), "
        "summary (one short sentence), "
        "next_steps (array of 2-4 concrete strings). "
        "No markdown, no extra keys."
    )
    user = f"Agent id: {req.agent_id}\nIssue:\n{req.message.strip()}"

    try:
        if kind == "azure":
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                max_tokens=400,
            )
        else:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                max_tokens=400,
            )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}") from exc

    content = ""
    try:
        content = (resp.choices[0].message.content or "").strip()
    except Exception:
        content = ""

    parsed = _parse_agent_json(content)
    severity = str(parsed.get("severity") or "medium").strip().lower()
    if severity not in {"low", "medium", "high", "critical"}:
        severity = "medium"
    summary = str(parsed.get("summary") or content[:240] or "Unable to summarize.").strip()
    steps_raw = parsed.get("next_steps") or []
    next_steps: list[str] = []
    if isinstance(steps_raw, list):
        next_steps = [str(s).strip() for s in steps_raw if str(s).strip()][:4]
    if not next_steps:
        next_steps = [
            "Confirm the failing service or agent id",
            "Check recent latency/error metrics in AI Operations",
            "Open Action Center if a recommendation appears",
        ]

    return TriageResponse(
        agent_id=(req.agent_id or "triage-bot").strip() or "triage-bot",
        severity=severity,
        summary=summary,
        next_steps=next_steps,
        model=model,
        provider_mode=_provider_mode(),
        raw=content if not parsed else None,
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT") or "8000")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
