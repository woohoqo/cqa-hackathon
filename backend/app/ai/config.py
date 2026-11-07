"""Validated, environment-backed configuration for the Gemini evaluator."""
from __future__ import annotations

import math
import os
import re
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from ..contracts import AIProviderError

_ROOT = Path(__file__).resolve().parents[3]
_MODEL = re.compile(r"gemini-[A-Za-z0-9][A-Za-z0-9._-]{0,150}\Z")
_GROUP = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")


@dataclass(frozen=True)
class AIConnection:
    """Immutable credential selection. Keys/fingerprints never enter diagnostics."""

    id: str
    api_key: str = field(repr=False)
    quota_group: str = "unknown"

    def identity(self, model: str) -> str:
        # Changing the credential or model permits a fresh authentication probe.
        return hashlib.sha256((model + "\0" + self.api_key).encode()).hexdigest()

    @property
    def label(self) -> str:
        return {"primary": "основной", "backup_1": "резерв 1", "backup_2": "резерв 2"}[self.id]


def _invalid(name: str) -> AIProviderError:
    return AIProviderError("invalid_ai_configuration", f"Invalid AI setting: {name}.")


def _number(name: str, default: float | int | None, *, integer: bool = False):
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw) if integer else float(raw)
    except (ValueError, OverflowError):
        raise _invalid(name) from None


@dataclass(frozen=True)
class AISettings:
    provider: str = "gemini"
    model: str = ""
    api_key: str = field(default="", repr=False)
    backup_api_key_1: str = field(default="", repr=False)
    backup_api_key_2: str = field(default="", repr=False)
    primary_quota_group: str = ""
    backup_1_quota_group: str = ""
    backup_2_quota_group: str = ""
    timeout_seconds: float = 30.0
    max_attempts: int = 3
    evaluation_timeout_seconds: float = 100.0
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 10.0
    min_request_interval_seconds: float = 0.0
    rate_limit_cooldown_seconds: float = 60.0
    quota_exhaustion_cooldown_seconds: float = 3600.0
    transient_cooldown_seconds: float = 5.0
    transient_attempts_per_connection: int = 2
    max_output_tokens: int = 8192
    temperature: float = 1.0
    thinking_level: str | None = None
    thinking_budget: int | None = None
    audit_path: str = "data/ai_usage.jsonl"
    input_price_per_million: float | None = None
    cached_input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    price_label: str = ""

    def __post_init__(self):
        if self.provider != "gemini":
            raise _invalid("AI_PROVIDER")
        if not isinstance(self.model, str) or not _MODEL.fullmatch(self.model):
            raise AIProviderError("provider_not_configured", "Set GEMINI_MODEL to a supported Gemini model ID.")
        keys = (("GEMINI_API_KEY", self.api_key),
                ("GEMINI_API_KEY_BACKUP_1", self.backup_api_key_1),
                ("GEMINI_API_KEY_BACKUP_2", self.backup_api_key_2))
        for name, key in keys:
            if not isinstance(key, str) or (key and (not key.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in key))):
                raise _invalid(name)
        if not any(key for _, key in keys):
            raise AIProviderError("provider_not_configured", "Укажите GEMINI_API_KEY для включения оценки.")
        for name, group in (("GEMINI_QUOTA_GROUP", self.primary_quota_group),
                            ("GEMINI_BACKUP_1_QUOTA_GROUP", self.backup_1_quota_group),
                            ("GEMINI_BACKUP_2_QUOTA_GROUP", self.backup_2_quota_group)):
            if not isinstance(group, str) or (group and not _GROUP.fullmatch(group)):
                raise _invalid(name)
            if group and any(group == key for _, key in keys):
                raise _invalid(name)  # Do not accidentally expose a credential as a group alias.
        bounds = (
            ("GEMINI_TIMEOUT_SECONDS", self.timeout_seconds, 0.01, 300),
            ("AI_EVALUATION_TIMEOUT_SECONDS", self.evaluation_timeout_seconds, 0.01, 600),
            ("AI_RETRY_BASE_SECONDS", self.retry_base_seconds, 0, 60),
            ("AI_RETRY_MAX_SECONDS", self.retry_max_seconds, 0, 60),
            ("AI_MIN_REQUEST_INTERVAL_SECONDS", self.min_request_interval_seconds, 0, 300),
            ("AI_RATE_LIMIT_COOLDOWN_SECONDS", self.rate_limit_cooldown_seconds, 1, 86400),
            ("AI_QUOTA_EXHAUSTION_COOLDOWN_SECONDS", self.quota_exhaustion_cooldown_seconds, 1, 604800),
            ("AI_TRANSIENT_COOLDOWN_SECONDS", self.transient_cooldown_seconds, 0, 300),
            ("GEMINI_TEMPERATURE", self.temperature, 0, 2),
        )
        for name, value, minimum, maximum in bounds:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise _invalid(name)
        if self.retry_max_seconds < self.retry_base_seconds:
            raise _invalid("AI_RETRY_MAX_SECONDS")
        for name, value, minimum, maximum in (
            ("AI_MAX_ATTEMPTS", self.max_attempts, 1, 5),
            ("AI_TRANSIENT_ATTEMPTS_PER_CONNECTION", self.transient_attempts_per_connection, 1, 5),
            ("GEMINI_MAX_OUTPUT_TOKENS", self.max_output_tokens, 1, 65536),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise _invalid(name)
        if self.thinking_level not in {None, "minimal", "low", "medium", "high"}:
            raise _invalid("GEMINI_THINKING_LEVEL")
        if self.thinking_budget is not None and (type(self.thinking_budget) is not int or not -1 <= self.thinking_budget <= 65536):
            raise _invalid("GEMINI_THINKING_BUDGET")
        if self.thinking_level is not None and self.thinking_budget is not None:
            raise _invalid("GEMINI_THINKING_LEVEL/GEMINI_THINKING_BUDGET")
        if not isinstance(self.audit_path, str) or not self.audit_path.strip() or "\x00" in self.audit_path:
            raise _invalid("AI_USAGE_LOG_PATH")
        for name, value in (
            ("AI_INPUT_USD_PER_MILLION", self.input_price_per_million),
            ("AI_CACHED_INPUT_USD_PER_MILLION", self.cached_input_price_per_million),
            ("AI_OUTPUT_USD_PER_MILLION", self.output_price_per_million),
        ):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise _invalid(name)
        if not isinstance(self.price_label, str) or len(self.price_label) > 200 or any(ord(c) < 32 for c in self.price_label):
            raise _invalid("AI_PRICE_LABEL")

    @classmethod
    def from_env(cls) -> "AISettings":
        try:
            load_dotenv(_ROOT / ".env", override=False)
        except (OSError, UnicodeError):
            raise AIProviderError("invalid_ai_configuration", "Could not read the AI environment configuration.") from None
        return cls(
            provider=os.getenv("AI_PROVIDER", "gemini").strip().lower(),
            model=os.getenv("GEMINI_MODEL", "").strip(),
            api_key=os.getenv("GEMINI_API_KEY", ""),
            backup_api_key_1=os.getenv("GEMINI_API_KEY_BACKUP_1", ""),
            backup_api_key_2=os.getenv("GEMINI_API_KEY_BACKUP_2", ""),
            primary_quota_group=os.getenv("GEMINI_QUOTA_GROUP", "").strip(),
            backup_1_quota_group=os.getenv("GEMINI_BACKUP_1_QUOTA_GROUP", "").strip(),
            backup_2_quota_group=os.getenv("GEMINI_BACKUP_2_QUOTA_GROUP", "").strip(),
            timeout_seconds=_number("GEMINI_TIMEOUT_SECONDS", 30.0),
            max_attempts=_number("AI_MAX_ATTEMPTS", 3, integer=True),
            evaluation_timeout_seconds=_number("AI_EVALUATION_TIMEOUT_SECONDS", 100.0),
            retry_base_seconds=_number("AI_RETRY_BASE_SECONDS", 1.0),
            retry_max_seconds=_number("AI_RETRY_MAX_SECONDS", 10.0),
            min_request_interval_seconds=_number("AI_MIN_REQUEST_INTERVAL_SECONDS", 0.0),
            rate_limit_cooldown_seconds=_number("AI_RATE_LIMIT_COOLDOWN_SECONDS", 60.0),
            quota_exhaustion_cooldown_seconds=_number("AI_QUOTA_EXHAUSTION_COOLDOWN_SECONDS", 3600.0),
            transient_cooldown_seconds=_number("AI_TRANSIENT_COOLDOWN_SECONDS", 5.0),
            transient_attempts_per_connection=_number("AI_TRANSIENT_ATTEMPTS_PER_CONNECTION", 2, integer=True),
            max_output_tokens=_number("GEMINI_MAX_OUTPUT_TOKENS", 8192, integer=True),
            temperature=_number("GEMINI_TEMPERATURE", 1.0),
            thinking_level=os.getenv("GEMINI_THINKING_LEVEL", "").strip().lower() or None,
            thinking_budget=_number("GEMINI_THINKING_BUDGET", None, integer=True),
            audit_path=os.getenv("AI_USAGE_LOG_PATH", "data/ai_usage.jsonl"),
            input_price_per_million=_number("AI_INPUT_USD_PER_MILLION", None),
            cached_input_price_per_million=_number("AI_CACHED_INPUT_USD_PER_MILLION", None),
            output_price_per_million=_number("AI_OUTPUT_USD_PER_MILLION", None),
            price_label=os.getenv("AI_PRICE_LABEL", ""),
        )

    def connections(self) -> tuple[AIConnection, ...]:
        """Prefer primary, then each nonempty, distinct reserve once; never cycle keys."""
        rows = (("primary", self.api_key, self.primary_quota_group),
                ("backup_1", self.backup_api_key_1, self.backup_1_quota_group),
                ("backup_2", self.backup_api_key_2, self.backup_2_quota_group))
        seen = set()
        result = []
        for identifier, key, group in rows:
            if key and key not in seen:
                result.append(AIConnection(identifier, key, group or "unknown"))
                seen.add(key)
        return tuple(result)
