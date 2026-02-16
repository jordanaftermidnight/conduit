"""
providers.py — Conduit model provider abstraction layer

Supports:
  - Anthropic Claude (claude-sonnet-4, claude-opus-4, claude-haiku-4.5)
  - OpenAI (gpt-4o, gpt-4o-mini, etc.)
  - Ollama (local models via REST API)
  - Any OpenAI-compatible endpoint (llama.cpp server, LM Studio, vLLM, text-generation-webui)

Each provider implements the same interface: send messages, get back text.
"""

import os
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("conduit")


@dataclass
class ProviderResponse:
    """Unified response from any provider."""
    text: str
    model: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    provider: str = ""


class BaseProvider(ABC):
    """Abstract base for all model providers."""

    name: str = "base"

    @abstractmethod
    def chat(self, system: str, messages: list[dict], **kwargs) -> ProviderResponse:
        """Send a conversation and return the response."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this provider is configured and reachable."""
        ...

    def __repr__(self):
        return f"<{self.__class__.__name__} model={getattr(self, 'model', '?')}>"


# ═══════════════════════════════════════════════════════════════════════
# ANTHROPIC (Claude)
# ═══════════════════════════════════════════════════════════════════════

class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, model: str = "claude-sonnet-4-20250514", api_key: Optional[str] = None):
        self.model = model
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def chat(self, system: str, messages: list[dict], **kwargs) -> ProviderResponse:
        client = self._get_client()
        response = client.messages.create(
            model=self.model,
            max_tokens=kwargs.get("max_tokens", 4096),
            system=system,
            messages=messages,
        )
        return ProviderResponse(
            text=response.content[0].text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            provider=self.name,
        )

    def is_available(self) -> bool:
        return bool(self.api_key)


# ═══════════════════════════════════════════════════════════════════════
# OPENAI (GPT-4o, etc.)
# ═══════════════════════════════════════════════════════════════════════

class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, model: str = "gpt-4o", api_key: Optional[str] = None):
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def chat(self, system: str, messages: list[dict], **kwargs) -> ProviderResponse:
        client = self._get_client()
        full_messages = [{"role": "system", "content": system}] + messages
        response = client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            max_tokens=kwargs.get("max_tokens", 4096),
        )
        choice = response.choices[0]
        usage = response.usage
        return ProviderResponse(
            text=choice.message.content,
            model=response.model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            provider=self.name,
        )

    def is_available(self) -> bool:
        return bool(self.api_key)


# ═══════════════════════════════════════════════════════════════════════
# OPENAI-COMPATIBLE (llama.cpp, LM Studio, vLLM, text-gen-webui, etc.)
# ═══════════════════════════════════════════════════════════════════════

class OpenAICompatibleProvider(BaseProvider):
    """
    Works with any server exposing an OpenAI-compatible /v1/chat/completions endpoint.

    Examples:
      - llama.cpp server:  base_url="http://localhost:8080/v1"
      - LM Studio:         base_url="http://localhost:1234/v1"
      - vLLM:              base_url="http://localhost:8000/v1"
      - text-gen-webui:    base_url="http://localhost:5000/v1"
    """
    name = "openai_compatible"

    def __init__(
        self,
        base_url: str = "http://localhost:8080/v1",
        model: str = "local-model",
        api_key: str = "not-needed",
        name_override: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        if name_override:
            self.name = name_override
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def chat(self, system: str, messages: list[dict], **kwargs) -> ProviderResponse:
        client = self._get_client()
        full_messages = [{"role": "system", "content": system}] + messages
        response = client.chat.completions.create(
            model=self.model,
            messages=full_messages,
            max_tokens=kwargs.get("max_tokens", 4096),
        )
        choice = response.choices[0]
        usage = response.usage
        return ProviderResponse(
            text=choice.message.content or "",
            model=self.model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            provider=self.name,
        )

    def is_available(self) -> bool:
        """Try to reach the endpoint."""
        import urllib.request
        try:
            # Quick check — hit /v1/models
            req = urllib.request.Request(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            urllib.request.urlopen(req, timeout=3)
            return True
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════════
# OLLAMA (native REST API — no OpenAI shim needed)
# ═══════════════════════════════════════════════════════════════════════

class OllamaProvider(BaseProvider):
    """
    Direct Ollama REST API client. No extra dependencies.

    Supports grammar-constrained JSON output via Ollama's `format` parameter,
    guaranteeing valid structured MIDI/param data at inference level.

    Default model: llama3.2 (selected via autodetect benchmarking).
    Qwen3 thinking mode is disabled when detected (think: false) and
    any <think> leakage is stripped from output.
    """
    name = "ollama"

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
        num_ctx: int = 1024,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx

    def chat(self, system: str, messages: list[dict], **kwargs) -> ProviderResponse:
        """
        Send a chat request to Ollama.

        Keyword args:
            max_tokens: Max tokens to generate (default 4096)
            json_schema: JSON schema dict for grammar-constrained output.
                         When provided, Ollama forces the model to produce
                         valid JSON matching this schema.
            temperature: Sampling temperature (default: model's default)
        """
        import urllib.request

        ollama_messages = [{"role": "system", "content": system}]
        for msg in messages:
            ollama_messages.append({"role": msg["role"], "content": msg["content"]})

        request_body = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
            "options": {
                "num_predict": kwargs.get("max_tokens", 512),
                "num_ctx": self.num_ctx,
            },
        }

        # Qwen3 models default to "thinking" mode which wastes tokens on
        # internal chain-of-thought.  Disable it for predictable, fast output.
        # NOTE: `think` must be a TOP-LEVEL param, not inside `options` (Ollama 0.7+)
        model_lower = self.model.lower().split(":")[0]   # e.g. "qwen3" from "qwen3:8b"
        if model_lower.startswith("qwen3"):
            request_body["think"] = False

        # Grammar-constrained JSON: pass schema via Ollama's format parameter
        json_schema = kwargs.get("json_schema")
        if json_schema:
            request_body["format"] = json_schema

        # Optional sampling parameter overrides
        if "temperature" in kwargs:
            request_body["options"]["temperature"] = kwargs["temperature"]
        if "repeat_penalty" in kwargs:
            request_body["options"]["repeat_penalty"] = kwargs["repeat_penalty"]
        if "top_p" in kwargs:
            request_body["options"]["top_p"] = kwargs["top_p"]
        if "top_k" in kwargs:
            request_body["options"]["top_k"] = kwargs["top_k"]

        payload = json.dumps(request_body).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        text = data.get("message", {}).get("content", "")
        eval_count = data.get("eval_count")
        prompt_eval_count = data.get("prompt_eval_count")

        # Strip qwen3 thinking-mode leakage: the model sometimes embeds
        # chain-of-thought in content despite think:false.  Remove any
        # <think>…</think> blocks and common CoT preamble patterns.
        if model_lower.startswith("qwen3") and text:
            import re as _re
            text = _re.sub(r"<think>.*?</think>\s*", "", text, flags=_re.DOTALL)
            # Strip "Hmm, the user is asking…" / "Okay, let me think…" preambles
            text = _re.sub(
                r"^(?:Hmm|Okay|Alright|Let me)[^\n]*(?:think|asking|recall|consider)[^\n]*\n*",
                "", text, flags=_re.IGNORECASE,
            ).lstrip()

        return ProviderResponse(
            text=text,
            model=self.model,
            input_tokens=prompt_eval_count,
            output_tokens=eval_count,
            provider=self.name,
        )

    def is_available(self) -> bool:
        import urllib.request
        try:
            urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=3)
            return True
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """List models available in Ollama."""
        import urllib.request
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []


# ═══════════════════════════════════════════════════════════════════════
# CIRCUIT BREAKER (minimal — ported from IRIS failover pattern)
# ═══════════════════════════════════════════════════════════════════════

import time
from collections import deque


@dataclass
class ProviderHealth:
    """Lightweight health state per provider. Inspired by IRIS health monitor
    but stripped to the minimum needed for a local music production tool."""

    # Circuit breaker state
    consecutive_failures: int = 0
    state: str = "closed"           # closed (healthy) | open (tripped) | half_open (testing)
    tripped_at: float = 0.0         # timestamp when circuit opened

    # Response time tracking — small sliding window
    response_times: deque = field(default_factory=lambda: deque(maxlen=20))
    error_count: int = 0
    success_count: int = 0

    @property
    def avg_response_ms(self) -> float:
        return sum(self.response_times) / len(self.response_times) if self.response_times else 0.0

    @property
    def health_score(self) -> int:
        """0-100 score. Simple: penalise for errors and slow responses."""
        total = self.error_count + self.success_count
        if total == 0:
            return 100
        error_rate = self.error_count / total
        # Slow response penalty: every 1s avg = -10 points
        speed_penalty = min(self.avg_response_ms / 1000 * 10, 40)
        score = max(0, int(100 - (error_rate * 60) - speed_penalty))
        return score


class CircuitBreaker:
    """
    Minimal circuit breaker + health tracker.
    Ported from IRIS smart-failover.ts — just the core pattern.

    - Tracks consecutive failures per provider
    - Opens circuit after `failure_threshold` consecutive errors
    - Auto-recovers after `recovery_seconds` (half-open → test one call)
    - Records response times for health scoring
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_seconds: float = 60.0,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds
        self._health: dict[str, ProviderHealth] = {}

    def _get(self, name: str) -> ProviderHealth:
        if name not in self._health:
            self._health[name] = ProviderHealth()
        return self._health[name]

    # ── State checks ─────────────────────────────────────────────────

    def is_available(self, name: str) -> bool:
        """Can we send a request to this provider right now?"""
        h = self._get(name)
        if h.state == "closed":
            return True
        if h.state == "open":
            # Check if recovery window has passed → transition to half_open
            if time.time() - h.tripped_at >= self.recovery_seconds:
                h.state = "half_open"
                logger.info(f"⚡ Circuit half-open for {name} — testing recovery")
                return True
            return False
        # half_open — allow one attempt
        return True

    def why_unavailable(self, name: str) -> str:
        """Human-readable reason (for status display in M4L)."""
        h = self._get(name)
        if h.state == "open":
            remaining = self.recovery_seconds - (time.time() - h.tripped_at)
            return f"circuit open ({h.consecutive_failures} failures, retry in {max(0, int(remaining))}s)"
        return ""

    # ── Recording outcomes ───────────────────────────────────────────

    def record_success(self, name: str, response_ms: float):
        h = self._get(name)
        h.response_times.append(response_ms)
        h.success_count += 1
        h.consecutive_failures = 0
        if h.state == "half_open":
            h.state = "closed"
            logger.info(f"✓ Circuit closed for {name} — recovered")

    def record_failure(self, name: str, response_ms: float = 0):
        h = self._get(name)
        if response_ms > 0:
            h.response_times.append(response_ms)
        h.error_count += 1
        h.consecutive_failures += 1

        if h.state == "half_open":
            # Failed during recovery test — back to open
            h.state = "open"
            h.tripped_at = time.time()
            logger.warning(f"✗ Circuit re-opened for {name} — recovery failed")
        elif h.consecutive_failures >= self.failure_threshold:
            h.state = "open"
            h.tripped_at = time.time()
            logger.warning(
                f"⚡ Circuit OPEN for {name} — "
                f"{h.consecutive_failures} consecutive failures, "
                f"cooling off {self.recovery_seconds}s"
            )

    def reset(self, name: str):
        """Manual reset (from M4L cmd or API)."""
        if name in self._health:
            self._health[name] = ProviderHealth()
            logger.info(f"Circuit reset for {name}")

    # ── Health info ──────────────────────────────────────────────────

    def get_health(self, name: str) -> dict:
        h = self._get(name)
        return {
            "state": h.state,
            "health_score": h.health_score,
            "consecutive_failures": h.consecutive_failures,
            "avg_response_ms": round(h.avg_response_ms, 1),
            "total_success": h.success_count,
            "total_errors": h.error_count,
        }

    def get_all_health(self) -> dict[str, dict]:
        return {name: self.get_health(name) for name in self._health}


# ═══════════════════════════════════════════════════════════════════════
# PROVIDER REGISTRY (with circuit breaker integration)
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class ProviderRegistry:
    """
    Manages available providers, the active one, and circuit breaker state.
    When the active provider's circuit trips, auto-failover finds the next
    healthy provider so your session doesn't stall.
    """
    providers: dict[str, BaseProvider] = field(default_factory=dict)
    active_name: str = ""
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)
    _fallback_order: list[str] = field(default_factory=list)

    def register(self, provider: BaseProvider, set_active: bool = False):
        self.providers[provider.name] = provider
        self._fallback_order.append(provider.name)
        if set_active or not self.active_name:
            self.active_name = provider.name
        logger.info(f"Registered provider: {provider}")

    @property
    def active(self) -> BaseProvider:
        if self.active_name not in self.providers:
            raise ValueError(f"No active provider. Available: {list(self.providers.keys())}")
        return self.providers[self.active_name]

    def switch(self, name: str) -> BaseProvider:
        if name not in self.providers:
            raise ValueError(f"Unknown provider '{name}'. Available: {list(self.providers.keys())}")
        self.active_name = name
        logger.info(f"Switched to provider: {self.providers[name]}")
        return self.providers[name]

    def chat_with_failover(
        self, system: str, messages: list[dict], **kwargs
    ) -> ProviderResponse:
        """
        Send a chat request through the active provider.
        If it fails and circuit trips, auto-failover to the next healthy provider.
        """
        # Build attempt order: active first, then fallbacks
        attempt_order = [self.active_name] + [
            n for n in self._fallback_order if n != self.active_name
        ]

        last_error = None
        for name in attempt_order:
            if not self.breaker.is_available(name):
                reason = self.breaker.why_unavailable(name)
                logger.debug(f"Skipping {name}: {reason}")
                continue

            provider = self.providers[name]
            start = time.time()
            try:
                response = provider.chat(system, messages, **kwargs)
                elapsed_ms = (time.time() - start) * 1000
                self.breaker.record_success(name, elapsed_ms)

                # If we failed over, let the caller know
                if name != self.active_name:
                    logger.info(f"Failover success: {self.active_name} → {name}")
                    response.provider = f"{name} (failover)"

                return response

            except Exception as e:
                elapsed_ms = (time.time() - start) * 1000
                self.breaker.record_failure(name, elapsed_ms)
                last_error = e
                logger.warning(f"Provider {name} failed ({elapsed_ms:.0f}ms): {e}")
                continue

        # All providers exhausted
        raise RuntimeError(
            f"All providers failed. Last error: {last_error}. "
            f"Health: {self.breaker.get_all_health()}"
        )

    def list_available(self) -> list[dict]:
        result = []
        for name, provider in self.providers.items():
            health = self.breaker.get_health(name)
            result.append({
                "name": name,
                "model": getattr(provider, "model", "?"),
                "active": name == self.active_name,
                "available": provider.is_available(),
                **health,
            })
        return result


def build_default_registry() -> ProviderRegistry:
    """
    Auto-configure providers based on available API keys and local services.
    Uses autodetect to select the best Ollama model for the system.

    Also registers a separate "ollama_generate" provider optimized for
    MIDI JSON generation (uses a model benchmarked for raw JSON output).
    """
    from autodetect import (
        find_best_available_model, find_best_generate_model,
        recommend_model_tier, get_available_ram_gb,
    )

    registry = ProviderRegistry()

    # Anthropic — register if key exists
    if os.getenv("ANTHROPIC_API_KEY"):
        registry.register(AnthropicProvider(), set_active=True)

    # OpenAI — register if key exists
    if os.getenv("OPENAI_API_KEY"):
        registry.register(OpenAIProvider())

    # Ollama — auto-detect best model for this system (chat)
    available_gb = get_available_ram_gb()
    best_model = find_best_available_model(available_gb=available_gb)
    recommended = recommend_model_tier(available_gb)

    if best_model:
        ollama_model = best_model
        logger.info(f"Auto-detected Ollama model: {best_model} (RAM: {available_gb:.1f}GB free)")
    else:
        ollama_model = recommended.model
        logger.info(f"Recommended Ollama model: {recommended.model} ({recommended.quality})")

    ollama = OllamaProvider(model=ollama_model)
    registry.register(ollama)

    # Ollama generate — separate model optimized for raw JSON output
    gen_model = find_best_generate_model()
    if gen_model and gen_model != ollama_model:
        ollama_gen = OllamaProvider(model=gen_model, num_ctx=2048)
        ollama_gen.name = "ollama_generate"
        registry.register(ollama_gen)
        logger.info(f"Generate model: {gen_model} (optimized for MIDI JSON)")
    else:
        logger.info(f"Generate model: same as chat ({ollama_model})")

    # LM Studio — common local setup
    lm_studio = OpenAICompatibleProvider(
        base_url="http://localhost:1234/v1",
        model="local-model",
        name_override="lm_studio",
    )
    registry.register(lm_studio)

    # If nothing else is active, try Ollama
    if not registry.active_name:
        if ollama.is_available():
            registry.switch("ollama")

    return registry
