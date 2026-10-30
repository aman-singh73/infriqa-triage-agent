"""
Emit Agent Ops traces to Infriqa control plane.

AI Ops verification expects spans tagged with application_id (+ agent_id)
posted to POST /api/agent-ops/traces/ingest on the same host as the UI.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib import error, request

logger = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def telemetry_config() -> dict[str, Any]:
    base = (
        (os.getenv("INFRIQA_AGENT_OPS_BASE_URL") or "").strip().rstrip("/")
        or (os.getenv("INFRIQA_PLATFORM_URL") or "").strip().rstrip("/")
        or "https://test.aignosticlabs.ai"
    )
    return {
        "enabled": (os.getenv("INFRIQA_TRACE_EMIT") or "1").strip().lower()
        not in ("0", "false", "no", "off"),
        "base_url": base,
        "ingest_url": f"{base}/api/agent-ops/traces/ingest",
        "application_id": (
            (os.getenv("APPLICATION_ID") or os.getenv("INFRIQA_APPLICATION_ID") or "")
            .strip()
            or "infriqa-triage-agent"
        ),
        "agent_id": (
            (os.getenv("AGENT_ID") or os.getenv("INFRIQA_AGENT_ID") or "")
            .strip()
            or "agent-aadf22cd6e8a"
        ),
        "environment": (
            (os.getenv("ENVIRONMENT") or os.getenv("INFRIQA_ENVIRONMENT") or "dev")
            .strip()
            or "dev"
        ),
        "attribution_method": (
            (os.getenv("ATTRIBUTION_METHOD") or "tagged").strip() or "tagged"
        ),
    }


def emit_llm_trace(
    *,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    duration_ms: float = 0.0,
    status: str = "ok",
    error_message: Optional[str] = None,
    agent_id_override: Optional[str] = None,
) -> dict[str, Any]:
    """
    Best-effort fire-and-forget ingest. Never raises to callers.
    Returns a small status dict for /health debugging.
    """
    cfg = telemetry_config()
    if not cfg["enabled"]:
        return {"ok": False, "skipped": True, "reason": "disabled"}

    agent_id = (agent_id_override or cfg["agent_id"] or "").strip() or None
    trace_id = f"triage-{uuid.uuid4().hex[:16]}"
    span_id = uuid.uuid4().hex[:16]
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end
    try:
        if duration_ms and duration_ms > 0:
            start = datetime.fromtimestamp(
                end.timestamp() - (float(duration_ms) / 1000.0),
                tz=timezone.utc,
            ).replace(microsecond=0)
    except Exception:
        start = end

    attrs: dict[str, Any] = {
        "application_id": cfg["application_id"],
        "app_id": cfg["application_id"],
        "environment": cfg["environment"],
        "deployment.environment": cfg["environment"],
        "attribution_method": cfg["attribution_method"],
        "model": model,
        "tokens_in": int(prompt_tokens or 0),
        "tokens_out": int(completion_tokens or 0),
        "service.name": "infriqa-triage-agent",
    }
    if agent_id:
        attrs["agent_id"] = agent_id
        attrs["ai.agent_id"] = agent_id

    span: dict[str, Any] = {
        "span_id": span_id,
        "parent_span_id": None,
        "name": "llm.chat.completions",
        "operation_name": "llm.chat.completions",
        "kind": "llm",
        "start_time": start.isoformat().replace("+00:00", "Z"),
        "end_time": end.isoformat().replace("+00:00", "Z"),
        "duration_ms": float(duration_ms or 0.0),
        "status": "error" if status == "error" else "ok",
        "attributes": attrs,
    }
    if error_message:
        span["error"] = {"message": str(error_message)[:500]}

    payload: dict[str, Any] = {
        "trace_id": trace_id,
        "source": "infriqa-triage-agent",
        "spans": [span],
        "summary": {
            "application_ids": [cfg["application_id"]],
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": int(prompt_tokens or 0) + int(completion_tokens or 0),
            "models_used": [model] if model else [],
        },
    }
    if agent_id:
        payload["agent_id"] = agent_id

    def _post(body_obj: dict[str, Any]) -> dict[str, Any]:
        raw_body = json.dumps(body_obj).encode("utf-8")
        req = request.Request(
            cfg["ingest_url"],
            data=raw_body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                # Cloudflare on QA blocks default Python-urllib UA (1010).
                "User-Agent": "infriqa-triage-agent/1.1 (+agent-ops-trace-emit)",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    parsed = json.loads(raw) if raw else {}
                except Exception:
                    parsed = {"raw": raw[:300]}
                ok = bool(parsed.get("success")) or resp.status in (200, 201)
                return {
                    "ok": ok,
                    "http_status": resp.status,
                    "trace_id": body_obj.get("trace_id"),
                    "ingest_url": cfg["ingest_url"],
                    "response": parsed,
                }
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
            return {
                "ok": False,
                "http_status": exc.code,
                "trace_id": body_obj.get("trace_id"),
                "ingest_url": cfg["ingest_url"],
                "error": detail[:500],
            }

    try:
        result = _post(payload)
        # If agent_id is unknown on this control-plane host, still ingest with app tags.
        err = str(result.get("error") or "")
        if (
            not result.get("ok")
            and payload.get("agent_id")
            and "not registered in agent_registry" in err
        ):
            retry = dict(payload)
            retry.pop("agent_id", None)
            retry["trace_id"] = f"triage-{uuid.uuid4().hex[:16]}"
            result = _post(retry)
            result["retried_without_agent_id"] = True
        if result.get("ok"):
            logger.info(
                "trace emit ok accepted=%s trace_id=%s",
                (result.get("response") or {}).get("accepted"),
                result.get("trace_id"),
            )
        else:
            logger.warning(
                "trace emit failed status=%s err=%s",
                result.get("http_status"),
                str(result.get("error") or result.get("response"))[:300],
            )
        return result
    except Exception as exc:
        logger.warning("trace emit failed: %s", exc)
        return {
            "ok": False,
            "trace_id": trace_id,
            "ingest_url": cfg["ingest_url"],
            "error": str(exc)[:500],
        }


def usage_from_openai_response(resp: Any) -> tuple[int, int]:
    try:
        usage = getattr(resp, "usage", None)
        if usage is None and isinstance(resp, dict):
            usage = resp.get("usage") or {}
        if usage is None:
            return 0, 0
        prompt = int(getattr(usage, "prompt_tokens", None) or usage.get("prompt_tokens") or 0)
        completion = int(
            getattr(usage, "completion_tokens", None) or usage.get("completion_tokens") or 0
        )
        return prompt, completion
    except Exception:
        return 0, 0
