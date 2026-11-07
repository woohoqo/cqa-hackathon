"""Append-only usage ledger. Never store transcript, prompts, API keys or source URLs."""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os
from pathlib import Path
import threading

from ..contracts import AIProviderError

_LOCK = threading.Lock()
_TOKEN_FIELDS = {
    "prompt_tokens": "promptTokenCount",
    "cached_input_tokens": "cachedContentTokenCount",
    "output_tokens": "candidatesTokenCount",
    "thinking_tokens": "thoughtsTokenCount",
    "total_tokens": "totalTokenCount",
    "tool_input_tokens": "toolUsePromptTokenCount",
}


def usage_counts(native: dict | None) -> dict:
    native = native or {}
    result = {}
    for name, source in _TOKEN_FIELDS.items():
        value = native.get(source)
        result[name] = value if type(value) is int and value >= 0 else None
    return result


def estimated_cost(usage: dict, settings) -> float | None:
    """Known usage and caller-supplied rate snapshot only; thinking is paid output."""
    required = ("prompt_tokens", "cached_input_tokens", "output_tokens", "thinking_tokens")
    rates = (settings.input_price_per_million, settings.cached_input_price_per_million,
             settings.output_price_per_million)
    if any(usage[key] is None for key in required) or any(rate is None for rate in rates):
        return None
    if usage["cached_input_tokens"] > usage["prompt_tokens"]:
        return None
    if usage["tool_input_tokens"] not in (None, 0):
        return None  # This evaluator has no tools; do not guess a pricing policy for them.
    input_rate, cached_rate, output_rate = map(lambda n: Decimal(str(n)), rates)
    amount = ((usage["prompt_tokens"] - usage["cached_input_tokens"]) * input_rate
              + usage["cached_input_tokens"] * cached_rate
              + (usage["output_tokens"] + usage["thinking_tokens"]) * output_rate) / 1_000_000
    value = float(amount)
    return value if math.isfinite(value) else None


def _open(path: str):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)


def check_audit_sink(path: str):
    try:
        with _LOCK:
            fd = _open(path)
            os.close(fd)
    except OSError:
        raise AIProviderError("ai_audit_unavailable", "Не удалось открыть журнал расхода AI.") from None


def record_attempt(settings, *, run_id: str, conversation_id: str, checklist_id: str,
                   checklist_version: int, prompt_version: str, attempt: int, status: str,
                   reply=None, error_code: str | None = None, connection_id: str = "primary",
                   quota_group: str = "unknown", started_at: str | None = None,
                   finished_at: str | None = None, duration_ms: float | None = None,
                   input_sha256: str | None = None, checklist_sha256: str | None = None,
                   request_input_sha256: str | None = None, system_instruction_sha256: str | None = None):
    usage = usage_counts(reply.usage if reply else None)
    # IDs from arbitrary imports can contain personal data; correlate with stable hashes.
    fingerprint = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    record = {
        "at": datetime.now(timezone.utc).isoformat(), "run_id": run_id, "attempt": attempt,
        "connection_id": connection_id, "quota_group": quota_group,
        "network_attempted": True, "started_at": started_at, "finished_at": finished_at,
        "duration_ms": duration_ms, "input_sha256": input_sha256,
        "checklist_sha256": checklist_sha256, "request_input_sha256": request_input_sha256,
        "system_instruction_sha256": system_instruction_sha256,
        "conversation_id_sha256": fingerprint(conversation_id),
        "checklist_id_sha256": fingerprint(checklist_id), "checklist_version": checklist_version,
        "provider": settings.provider, "requested_model": settings.model,
        "model_version": reply.model_version if reply else None,
        "response_id": reply.response_id if reply else None,
        "prompt_version": prompt_version, "status": status, "error_code": error_code,
        "finish_reason": reply.finish_reason if reply else None,
        "usage": usage, "cost_usd": estimated_cost(usage, settings),
        "prices": {"input_usd_per_million": settings.input_price_per_million,
                   "cached_input_usd_per_million": settings.cached_input_price_per_million,
                   "output_usd_per_million": settings.output_price_per_million,
                   "label": settings.price_label},
    }
    raw = (json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode()
    try:
        with _LOCK:
            fd = _open(settings.audit_path)
            try:
                # Handle partial writes without interleaving concurrent in-process calls.
                offset = 0
                while offset < len(raw):
                    written = os.write(fd, raw[offset:])
                    if written <= 0:
                        raise OSError("Incomplete audit write")
                    offset += written
            finally:
                os.close(fd)
    except OSError:
        raise AIProviderError("ai_audit_unavailable", "Не удалось сохранить расход AI.") from None
