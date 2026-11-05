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
import logging
import os
import re
import time
from typing import Any, Optional
from urllib import error, request

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from telemetry import emit_llm_trace, telemetry_config, usage_from_openai_response

APP_NAME = "infriqa-triage-agent"
APP_VERSION = "1.1.1"
logger = logging.getLogger(__name__)

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
    runtime_config_enabled: bool = False
    runtime_config_applied: bool = False


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
    # Avoid inherited HTTP(S)_PROXY settings breaking local BYOK calls.
    try:
        import httpx

        kwargs["http_client"] = httpx.Client(trust_env=False, timeout=60.0)
    except Exception:
        pass
    return OpenAI(**kwargs), "openai"


def _chat_model() -> str:
    return (
        (os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT") or "").strip()
        or (os.getenv("AZURE_OPENAI_DEPLOYMENT") or "").strip()
        or (os.getenv("OPENAI_CHAT_MODEL") or "").strip()
        or (os.getenv("OPENAI_MODEL") or "").strip()
        or "gpt-4o-mini"
    )


def _runtime_config_flag_enabled() -> bool:
    """Opt-in only. Default off so existing Managed behavior is unchanged."""
    return (os.getenv("INFRIQA_RUNTIME_CONFIG_ENABLED") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _runtime_config(agent_id: str) -> dict[str, Any]:
    """
    Pull published AI Ops runtime knobs after healing Apply.

    Does not modify healing/AI Ops code. When the flag is off, returns {}.
    """
    if not _runtime_config_flag_enabled():
        return {}

    base_url = (
        (os.getenv("INFRIQA_AGENT_OPS_BASE_URL") or "").strip().rstrip("/")
        or (os.getenv("INFRIQA_PLATFORM_URL") or "").strip().rstrip("/")
        or "https://test.aignosticlabs.ai"
    )
    url = f"{base_url}/api/agent-ops/runtime-config/{agent_id}"
    req = request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "infriqa-triage-agent/1.1 (+runtime-config)",
        },
    )
    try:
        with request.urlopen(req, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
        config = payload.get("config") if isinstance(payload, dict) else {}
        return config if isinstance(config, dict) else {}
    except Exception as exc:
        logger.warning("Runtime config unavailable for agent %s: %s", agent_id, exc)
        return {}


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
    tcfg = telemetry_config()
    return {
        "status": "ok",
        "app": APP_NAME,
        "provider_mode": _provider_mode(),
        "llm_configured": bool(
            (os.getenv("OPENAI_API_KEY") or "").strip()
            or (os.getenv("AZURE_OPENAI_API_KEY") or "").strip()
        ),
        "runtime_config_enabled": _runtime_config_flag_enabled(),
        "telemetry": {
            "enabled": tcfg["enabled"],
            "ingest_url": tcfg["ingest_url"],
            "application_id": tcfg["application_id"],
            "agent_id": tcfg["agent_id"],
            "environment": tcfg["environment"],
        },
    }


@app.get("/api/healthz")
def healthz():
    return health()


@app.post("/agent/triage", response_model=TriageResponse)
def triage(req: TriageRequest):
    """
    Single support-style agent: classify an ops/support message and suggest next steps.
    """
    agent_id = (req.agent_id or "triage-bot").strip() or "triage-bot"
    flag_on = _runtime_config_flag_enabled()
    runtime_config = _runtime_config(agent_id) if flag_on else {}
    try:
        client, kind = _chat_client()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    model = str(runtime_config.get("model_tier") or _chat_model()).strip() or _chat_model()
    try:
        max_tokens = max(1, min(400, int(runtime_config.get("max_tokens", 400))))
    except (TypeError, ValueError):
        max_tokens = 400
    runtime_applied = bool(
        flag_on
        and (
            ("model_tier" in runtime_config and runtime_config.get("model_tier"))
            or ("max_tokens" in runtime_config and runtime_config.get("max_tokens") is not None)
        )
    )

    system = (
        "You are triage-bot, a support operations agent on Infriqa Managed hosting. "
        "Return ONLY compact JSON with keys: "
        "severity (one of: low, medium, high, critical), "
        "summary (one short sentence), "
        "next_steps (array of 2-4 concrete strings). "
        "No markdown, no extra keys."
    )
    user = f"Agent id: {agent_id}\nIssue:\n{req.message.strip()}"

    started = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        emit_llm_trace(
            model=model,
            duration_ms=(time.time() - started) * 1000.0,
            status="error",
            error_message=str(exc),
        )
        raise HTTPException(status_code=502, detail=f"LLM call failed: {exc}") from exc

    duration_ms = (time.time() - started) * 1000.0
    prompt_tokens, completion_tokens = usage_from_openai_response(resp)
    emit_llm_trace(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        duration_ms=duration_ms,
        status="ok",
        agent_id_override=(os.getenv("AGENT_ID") or os.getenv("INFRIQA_AGENT_ID") or "").strip()
        or None,
    )

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
        agent_id=agent_id,
        severity=severity,
        summary=summary,
        next_steps=next_steps,
        model=model,
        provider_mode=_provider_mode(),
        raw=content if not parsed else None,
        runtime_config_enabled=flag_on,
        runtime_config_applied=runtime_applied,
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT") or "8000")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
