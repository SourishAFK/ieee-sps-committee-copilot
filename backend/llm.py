"""Pluggable LLM client: Gemini (default) or Claude, with a rules-only fallback.

Three things matter here, in this order:

1. Never break the app. Every call returns `None` on failure and the caller
   falls back to deterministic output. A committee tool that dies because a
   free-tier quota ran out is worse than one that gives terser advice.
2. Respect the free tier. Google AI Studio's free tier allows only a handful
   of requests per minute and a small daily budget, so calls are self-throttled
   and every response is cached on disk by prompt hash.
3. Stay swappable. `LLM_PROVIDER` selects the backend; nothing above this
   module knows which one is in use.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from .config import CACHE_DIR, settings

_LLM_CACHE = CACHE_DIR / "llm"
_LLM_CACHE.mkdir(parents=True, exist_ok=True)

_THROTTLE_LOCK = threading.Lock()
_last_call_at = 0.0

# Set once a provider reports quota exhaustion, so the rest of the session
# skips straight to the fallback instead of burning retries.
_quota_exhausted = False


class LLMUnavailable(RuntimeError):
    pass


def _cache_key(system: str, prompt: str, model: str) -> Path:
    digest = hashlib.sha256(f"{model}\x00{system}\x00{prompt}".encode()).hexdigest()[:32]
    return _LLM_CACHE / f"{digest}.json"


def _throttle() -> None:
    """Space calls out to stay inside the configured requests-per-minute cap."""
    rpm = settings.llm.gemini_max_rpm
    if rpm <= 0:
        return
    min_gap = 60.0 / rpm
    global _last_call_at
    with _THROTTLE_LOCK:
        wait = min_gap - (time.time() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.time()


def _is_quota_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(s in text for s in ("quota", "429", "resource_exhausted", "rate limit"))


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------
def _call_gemini(system: str, prompt: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=settings.llm.gemini_key)
    resp = client.models.generate_content(
        model=settings.llm.gemini_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system or None,
            temperature=0.6,
            max_output_tokens=2048,
        ),
    )
    return (resp.text or "").strip()


def _call_claude(system: str, prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=settings.llm.anthropic_key)
    resp = client.messages.create(
        model=settings.llm.claude_model,
        max_tokens=2048,
        system=system or "",
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip()


PROVIDERS = {"gemini": _call_gemini, "claude": _call_claude}


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------
def available() -> bool:
    return settings.llm.enabled and not _quota_exhausted


def status() -> dict[str, Any]:
    return {
        "provider": settings.llm.provider,
        "configured": settings.llm.enabled,
        "quota_exhausted": _quota_exhausted,
        "model": (
            settings.llm.gemini_model
            if settings.llm.provider == "gemini"
            else settings.llm.claude_model
        ),
        "cached_responses": len(list(_LLM_CACHE.glob("*.json"))),
    }


def complete(system: str, prompt: str, use_cache: bool = True) -> str | None:
    """Run one completion. Returns None whenever the LLM cannot be used."""
    global _quota_exhausted

    provider = settings.llm.provider
    fn = PROVIDERS.get(provider)
    if fn is None or not settings.llm.enabled:
        return None

    model = status()["model"]
    cache_file = _cache_key(system, prompt, model)
    if use_cache and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))["text"]
        except (json.JSONDecodeError, KeyError):
            pass

    if _quota_exhausted:
        return None

    attempts = max(1, settings.llm.gemini_max_retries)
    last: Exception | None = None
    for _ in range(attempts):
        try:
            _throttle()
            text = fn(system, prompt)
            if text:
                cache_file.write_text(json.dumps({"text": text}), encoding="utf-8")
                return text
        except Exception as exc:  # noqa: BLE001 - degrade rather than crash
            last = exc
            if _is_quota_error(exc):
                _quota_exhausted = True
                break
    if last is not None:
        print(f"[llm] {provider} unavailable: {last}")
    return None


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def complete_json(system: str, prompt: str, use_cache: bool = True) -> dict[str, Any] | None:
    """Completion that must return a JSON object. None if unusable."""
    raw = complete(
        system + "\n\nRespond with a single valid JSON object and nothing else.",
        prompt,
        use_cache=use_cache,
    )
    if not raw:
        return None

    candidate = raw.strip()
    block = _JSON_BLOCK.search(candidate)
    if block:
        candidate = block.group(1).strip()
    else:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start != -1 and end > start:
            candidate = candidate[start : end + 1]

    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None
