"""Validated, environment-backed configuration for the Gemini evaluator."""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from ..contracts import AIProviderError

_ROOT = Path(__file__).resolve().parents[3]
_MODEL = re.compile(r"gemini-[A-Za-z0-9][A-Za-z0-9._-]{0,150}\Z")


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
    timeout_seconds: float = 30.0
    max_attempts: int = 3
    evaluation_timeout_seconds: float = 100.0
    retry_base_seconds: float = 1.0
    retry_max_seconds: float = 10.0
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
        if not isinstance(self.api_key, str) or not self.api_key.strip():
            raise AIProviderError("provider_not_configured", "Set GEMINI_API_KEY to enable evaluation.")
        if not self.api_key.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in self.api_key):
            raise _invalid("GEMINI_API_KEY")
        bounds = (
            ("GEMINI_TIMEOUT_SECONDS", self.timeout_seconds, 0.01, 300),
            ("AI_EVALUATION_TIMEOUT_SECONDS", self.evaluation_timeout_seconds, 0.01, 600),
            ("AI_RETRY_BASE_SECONDS", self.retry_base_seconds, 0, 60),
            ("AI_RETRY_MAX_SECONDS", self.retry_max_seconds, 0, 60),
            ("GEMINI_TEMPERATURE", self.temperature, 0, 2),
        )
        for name, value, minimum, maximum in bounds:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
                raise _invalid(name)
        if self.retry_max_seconds < self.retry_base_seconds:
            raise _invalid("AI_RETRY_MAX_SECONDS")
        for name, value, minimum, maximum in (
            ("AI_MAX_ATTEMPTS", self.max_attempts, 1, 5),
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
            timeout_seconds=_number("GEMINI_TIMEOUT_SECONDS", 30.0),
            max_attempts=_number("AI_MAX_ATTEMPTS", 3, integer=True),
            evaluation_timeout_seconds=_number("AI_EVALUATION_TIMEOUT_SECONDS", 100.0),
            retry_base_seconds=_number("AI_RETRY_BASE_SECONDS", 1.0),
            retry_max_seconds=_number("AI_RETRY_MAX_SECONDS", 10.0),
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
