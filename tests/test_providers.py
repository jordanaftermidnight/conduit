"""
test_providers.py -- Tests for provider implementations and ProviderRegistry.

Covers:
  - AnthropicProvider.is_available() with / without API key
  - OpenAIProvider.is_available() with / without API key
  - OllamaProvider.is_available() with / without running server (mocked HTTP)
  - OpenAICompatibleProvider.is_available() with / without running server
  - Mocked chat() calls for each provider
  - ProviderRegistry: register, switch, list_available
  - chat_with_failover: primary succeeds, primary fails + fallback, all fail
  - build_default_registry auto-detection

All external calls are mocked -- no real HTTP or SDK traffic.
"""

import json
import os
import sys
from unittest.mock import patch, MagicMock, PropertyMock

import pytest

from providers import (
    AnthropicProvider,
    OpenAIProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    ProviderResponse,
    ProviderRegistry,
    CircuitBreaker,
    build_default_registry,
)


# ═══════════════════════════════════════════════════════════════════════════
# AnthropicProvider
# ═══════════════════════════════════════════════════════════════════════════

class TestAnthropicProvider:

    def test_is_available_with_key(self):
        p = AnthropicProvider(api_key="sk-ant-test-key")
        assert p.is_available() is True

    def test_is_available_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            p = AnthropicProvider(api_key="")
            assert p.is_available() is False

    def test_is_available_from_env(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-env-key"}):
            p = AnthropicProvider()
            assert p.is_available() is True

    def test_default_model(self):
        p = AnthropicProvider(api_key="test")
        assert p.model == "claude-sonnet-4-20250514"

    def test_custom_model(self):
        p = AnthropicProvider(model="claude-opus-4", api_key="test")
        assert p.model == "claude-opus-4"

    def test_name(self):
        p = AnthropicProvider(api_key="test")
        assert p.name == "anthropic"

    def test_repr(self):
        p = AnthropicProvider(api_key="test")
        r = repr(p)
        assert "AnthropicProvider" in r
        assert "claude-sonnet-4-20250514" in r

    def test_chat_mocked(self):
        p = AnthropicProvider(api_key="sk-test")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Hello from Claude")]
        mock_response.model = "claude-sonnet-4-20250514"
        mock_response.usage.input_tokens = 25
        mock_response.usage.output_tokens = 10
        mock_client.messages.create.return_value = mock_response
        p._client = mock_client

        result = p.chat("system prompt", [{"role": "user", "content": "hi"}])
        assert isinstance(result, ProviderResponse)
        assert result.text == "Hello from Claude"
        assert result.model == "claude-sonnet-4-20250514"
        assert result.input_tokens == 25
        assert result.output_tokens == 10
        assert result.provider == "anthropic"
        mock_client.messages.create.assert_called_once()

    def test_chat_passes_max_tokens(self):
        p = AnthropicProvider(api_key="sk-test")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        mock_response.model = "claude-sonnet-4-20250514"
        mock_response.usage.input_tokens = 1
        mock_response.usage.output_tokens = 1
        mock_client.messages.create.return_value = mock_response
        p._client = mock_client

        p.chat("sys", [{"role": "user", "content": "x"}], max_tokens=2048)
        call_kwargs = mock_client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 2048


# ═══════════════════════════════════════════════════════════════════════════
# OpenAIProvider
# ═══════════════════════════════════════════════════════════════════════════

class TestOpenAIProvider:

    def test_is_available_with_key(self):
        p = OpenAIProvider(api_key="sk-openai-test")
        assert p.is_available() is True

    def test_is_available_without_key(self):
        with patch.dict(os.environ, {}, clear=True):
            p = OpenAIProvider(api_key="")
            assert p.is_available() is False

    def test_is_available_from_env(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env-key"}):
            p = OpenAIProvider()
            assert p.is_available() is True

    def test_default_model(self):
        p = OpenAIProvider(api_key="test")
        assert p.model == "gpt-4o"

    def test_name(self):
        p = OpenAIProvider(api_key="test")
        assert p.name == "openai"

    def test_chat_mocked(self):
        p = OpenAIProvider(api_key="sk-test")
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Hello from GPT"
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 30
        mock_usage.completion_tokens = 15
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage
        mock_response.model = "gpt-4o"
        mock_client.chat.completions.create.return_value = mock_response
        p._client = mock_client

        result = p.chat("system", [{"role": "user", "content": "hi"}])
        assert result.text == "Hello from GPT"
        assert result.model == "gpt-4o"
        assert result.input_tokens == 30
        assert result.output_tokens == 15
        assert result.provider == "openai"

    def test_chat_prepends_system_message(self):
        p = OpenAIProvider(api_key="sk-test")
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "ok"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = None
        mock_response.model = "gpt-4o"
        mock_client.chat.completions.create.return_value = mock_response
        p._client = mock_client

        p.chat("You are helpful.", [{"role": "user", "content": "q"}])
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert messages[0] == {"role": "system", "content": "You are helpful."}
        assert messages[1] == {"role": "user", "content": "q"}

    def test_chat_no_usage(self):
        """Handles response.usage being None gracefully."""
        p = OpenAIProvider(api_key="sk-test")
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "response"
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = None
        mock_response.model = "gpt-4o"
        mock_client.chat.completions.create.return_value = mock_response
        p._client = mock_client

        result = p.chat("sys", [{"role": "user", "content": "x"}])
        assert result.input_tokens is None
        assert result.output_tokens is None


# ═══════════════════════════════════════════════════════════════════════════
# OllamaProvider
# ═══════════════════════════════════════════════════════════════════════════

class TestOllamaProvider:

    def test_name(self):
        p = OllamaProvider()
        assert p.name == "ollama"

    def test_default_model(self):
        p = OllamaProvider()
        assert p.model == "qwen3:8b"

    def test_is_available_server_running(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock()
            mock_urlopen.return_value.__exit__ = MagicMock()
            p = OllamaProvider()
            assert p.is_available() is True

    def test_is_available_server_not_running(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
            p = OllamaProvider()
            assert p.is_available() is False

    def test_is_available_timeout(self):
        from urllib.error import URLError
        with patch("urllib.request.urlopen", side_effect=URLError("timeout")):
            p = OllamaProvider()
            assert p.is_available() is False

    def test_chat_mocked(self):
        p = OllamaProvider(model="mistral:7b")
        response_data = json.dumps({
            "message": {"content": "Hello from Ollama"},
            "eval_count": 42,
            "prompt_eval_count": 18,
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = p.chat("system", [{"role": "user", "content": "hi"}])
            assert result.text == "Hello from Ollama"
            assert result.model == "mistral:7b"
            assert result.input_tokens == 18
            assert result.output_tokens == 42
            assert result.provider == "ollama"

    def test_chat_constructs_correct_payload(self):
        p = OllamaProvider(model="codellama:13b", base_url="http://myhost:11434")

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "message": {"content": "ok"},
        }).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.Request") as mock_request_cls, \
             patch("urllib.request.urlopen", return_value=mock_resp):
            p.chat("sys prompt", [{"role": "user", "content": "go"}], max_tokens=512)
            call_args = mock_request_cls.call_args
            assert "http://myhost:11434/api/chat" == call_args.args[0]
            payload = json.loads(call_args.kwargs["data"].decode("utf-8"))
            assert payload["model"] == "codellama:13b"
            assert payload["stream"] is False
            assert payload["options"]["num_predict"] == 512
            assert payload["messages"][0]["role"] == "system"

    def test_list_models_success(self):
        p = OllamaProvider()
        response_data = json.dumps({
            "models": [
                {"name": "llama3.1:8b"},
                {"name": "mistral:7b"},
                {"name": "codellama:13b"},
            ]
        }).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            models = p.list_models()
            assert models == ["llama3.1:8b", "mistral:7b", "codellama:13b"]

    def test_list_models_failure(self):
        p = OllamaProvider()
        with patch("urllib.request.urlopen", side_effect=ConnectionError):
            models = p.list_models()
            assert models == []


# ═══════════════════════════════════════════════════════════════════════════
# OpenAICompatibleProvider
# ═══════════════════════════════════════════════════════════════════════════

class TestOpenAICompatibleProvider:

    def test_name_default(self):
        p = OpenAICompatibleProvider()
        assert p.name == "openai_compatible"

    def test_name_override(self):
        p = OpenAICompatibleProvider(name_override="lm_studio")
        assert p.name == "lm_studio"

    def test_base_url_trailing_slash_stripped(self):
        p = OpenAICompatibleProvider(base_url="http://localhost:8080/v1/")
        assert p.base_url == "http://localhost:8080/v1"

    def test_is_available_server_reachable(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = MagicMock()
            p = OpenAICompatibleProvider()
            assert p.is_available() is True

    def test_is_available_server_unreachable(self):
        with patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
            p = OpenAICompatibleProvider()
            assert p.is_available() is False

    def test_is_available_checks_models_endpoint(self):
        """Should hit /v1/models to check availability."""
        with patch("urllib.request.Request") as mock_req_cls, \
             patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = MagicMock()
            p = OpenAICompatibleProvider(base_url="http://localhost:1234/v1")
            p.is_available()
            call_args = mock_req_cls.call_args
            assert "http://localhost:1234/v1/models" == call_args.args[0]

    def test_chat_mocked(self):
        p = OpenAICompatibleProvider(
            base_url="http://localhost:8080/v1",
            model="local-llama",
            name_override="my_llm",
        )
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Hello from local LLM"
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 12
        mock_usage.completion_tokens = 8
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage
        mock_response.model = "local-llama"
        mock_client.chat.completions.create.return_value = mock_response
        p._client = mock_client

        result = p.chat("sys", [{"role": "user", "content": "hi"}])
        assert result.text == "Hello from local LLM"
        assert result.model == "local-llama"
        assert result.provider == "my_llm"

    def test_chat_empty_content_returns_empty_string(self):
        p = OpenAICompatibleProvider()
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = None  # some servers return None
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 5
        mock_usage.completion_tokens = 0
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage
        mock_client.chat.completions.create.return_value = mock_response
        p._client = mock_client

        result = p.chat("sys", [{"role": "user", "content": "x"}])
        assert result.text == ""


# ═══════════════════════════════════════════════════════════════════════════
# ProviderRegistry -- Registration & Switching
# ═══════════════════════════════════════════════════════════════════════════

class TestProviderRegistryBasics:

    def test_register_single_provider(self, mock_success_provider):
        reg = ProviderRegistry()
        reg.register(mock_success_provider)
        assert "mock_success" in reg.providers
        # First registered provider becomes active
        assert reg.active_name == "mock_success"

    def test_register_set_active_explicit(self, mock_success_provider, mock_fail_provider):
        reg = ProviderRegistry()
        reg.register(mock_fail_provider)
        reg.register(mock_success_provider, set_active=True)
        assert reg.active_name == "mock_success"

    def test_active_property(self, mock_success_provider):
        reg = ProviderRegistry()
        reg.register(mock_success_provider)
        assert reg.active is mock_success_provider

    def test_active_raises_when_empty(self):
        reg = ProviderRegistry()
        with pytest.raises(ValueError, match="No active provider"):
            _ = reg.active

    def test_switch_success(self, mock_success_provider, mock_fail_provider):
        reg = ProviderRegistry()
        reg.register(mock_success_provider, set_active=True)
        reg.register(mock_fail_provider)
        switched = reg.switch("mock_fail")
        assert reg.active_name == "mock_fail"
        assert switched is mock_fail_provider

    def test_switch_unknown_raises(self, mock_success_provider):
        reg = ProviderRegistry()
        reg.register(mock_success_provider)
        with pytest.raises(ValueError, match="Unknown provider"):
            reg.switch("nonexistent")

    def test_list_available(self, mock_success_provider):
        reg = ProviderRegistry()
        reg.register(mock_success_provider)
        listing = reg.list_available()
        assert len(listing) == 1
        entry = listing[0]
        assert entry["name"] == "mock_success"
        assert entry["active"] is True
        assert entry["available"] is True
        assert "health_score" in entry
        assert "state" in entry

    def test_list_available_multiple(self, mock_success_provider, mock_fail_provider):
        reg = ProviderRegistry()
        reg.register(mock_success_provider, set_active=True)
        reg.register(mock_fail_provider)
        listing = reg.list_available()
        assert len(listing) == 2
        names = [e["name"] for e in listing]
        assert "mock_success" in names
        assert "mock_fail" in names

    def test_fallback_order(self, mock_success_provider, mock_fail_provider):
        reg = ProviderRegistry()
        reg.register(mock_fail_provider)
        reg.register(mock_success_provider)
        assert reg._fallback_order == ["mock_fail", "mock_success"]


# ═══════════════════════════════════════════════════════════════════════════
# ProviderRegistry -- chat_with_failover
# ═══════════════════════════════════════════════════════════════════════════

class TestChatWithFailover:

    def test_primary_succeeds(self, registry_with_success):
        result = registry_with_success.chat_with_failover(
            "system", [{"role": "user", "content": "test"}]
        )
        assert result.text == "Mock success response."
        assert result.provider == "mock_success"

    def test_primary_fails_fallback_succeeds(self, registry_with_failover):
        """Primary (mock_fail) fails -> falls over to mock_success."""
        result = registry_with_failover.chat_with_failover(
            "system", [{"role": "user", "content": "test"}]
        )
        assert result.text == "Mock success response."
        assert "failover" in result.provider

    def test_all_providers_fail(self, mock_fail_provider):
        reg = ProviderRegistry()
        fail2 = MagicMock()
        fail2.name = "fail2"
        fail2.chat.side_effect = RuntimeError("also broken")
        fail2.is_available.return_value = True

        reg.register(mock_fail_provider, set_active=True)
        reg.register(fail2)

        with pytest.raises(RuntimeError, match="All providers failed"):
            reg.chat_with_failover("sys", [{"role": "user", "content": "x"}])

    def test_failover_records_circuit_breaker_success(self, registry_with_failover):
        registry_with_failover.chat_with_failover(
            "sys", [{"role": "user", "content": "x"}]
        )
        health = registry_with_failover.breaker.get_health("mock_success")
        assert health["total_success"] >= 1

    def test_failover_records_circuit_breaker_failure(self, registry_with_failover):
        registry_with_failover.chat_with_failover(
            "sys", [{"role": "user", "content": "x"}]
        )
        health = registry_with_failover.breaker.get_health("mock_fail")
        assert health["total_errors"] >= 1

    def test_skips_provider_with_open_circuit(self):
        """Provider whose circuit is open should be skipped entirely."""
        from tests.conftest import MockSuccessProvider, MockFailProvider

        reg = ProviderRegistry()
        primary = MockFailProvider()
        fallback = MockSuccessProvider()
        reg.register(primary, set_active=True)
        reg.register(fallback)

        # Trip the circuit for primary
        for _ in range(3):
            reg.breaker.record_failure("mock_fail")

        result = reg.chat_with_failover("sys", [{"role": "user", "content": "go"}])
        assert result.text == "Mock success response."
        # primary should NOT have been called again (circuit open)
        # check that mock_fail error count stayed at 3 (not 4)
        assert reg.breaker.get_health("mock_fail")["total_errors"] == 3


# ═══════════════════════════════════════════════════════════════════════════
# build_default_registry
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildDefaultRegistry:
    """Tests for build_default_registry().

    The on-disk version imports autodetect functions (find_best_available_model,
    recommend_model_tier, get_available_ram_gb) inside the function body.
    We mock those to avoid loading the autodetect module (which may have
    unrelated issues) and to control the model-selection logic.
    """

    @staticmethod
    def _mock_autodetect():
        """Return a context manager that patches the autodetect imports."""
        mock_tier = MagicMock()
        mock_tier.model = "qwen3:8b"
        mock_tier.quality = "Full capability (default)"
        return patch.dict(
            "sys.modules",
            {
                "autodetect": MagicMock(
                    find_best_available_model=MagicMock(return_value=None),
                    recommend_model_tier=MagicMock(return_value=mock_tier),
                    get_available_ram_gb=MagicMock(return_value=8.0),
                )
            },
        )

    def test_anthropic_registered_when_key_present(self):
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict(os.environ, env, clear=True), \
             self._mock_autodetect(), \
             patch("providers.OllamaProvider.is_available", return_value=False):
            reg = build_default_registry()
            assert "anthropic" in reg.providers
            assert reg.active_name == "anthropic"

    def test_openai_registered_when_key_present(self):
        env = {"OPENAI_API_KEY": "sk-oai-test"}
        with patch.dict(os.environ, env, clear=True), \
             self._mock_autodetect(), \
             patch("providers.OllamaProvider.is_available", return_value=False):
            reg = build_default_registry()
            assert "openai" in reg.providers

    def test_ollama_always_registered(self):
        with patch.dict(os.environ, {}, clear=True), \
             self._mock_autodetect(), \
             patch("providers.OllamaProvider.is_available", return_value=False):
            reg = build_default_registry()
            assert "ollama" in reg.providers

    def test_lm_studio_always_registered(self):
        with patch.dict(os.environ, {}, clear=True), \
             self._mock_autodetect(), \
             patch("providers.OllamaProvider.is_available", return_value=False):
            reg = build_default_registry()
            assert "lm_studio" in reg.providers

    def test_no_keys_ollama_available_becomes_active(self):
        with patch.dict(os.environ, {}, clear=True), \
             self._mock_autodetect(), \
             patch("providers.OllamaProvider.is_available", return_value=True):
            reg = build_default_registry()
            assert reg.active_name == "ollama"

    def test_no_keys_nothing_available(self):
        with patch.dict(os.environ, {}, clear=True), \
             self._mock_autodetect(), \
             patch("providers.OllamaProvider.is_available", return_value=False):
            reg = build_default_registry()
            # ollama was registered first when no API keys -> it gets set active
            # by the register() logic (first registered becomes active)
            assert reg.active_name == "ollama"

    def test_anthropic_takes_priority(self):
        env = {"ANTHROPIC_API_KEY": "sk-ant-test", "OPENAI_API_KEY": "sk-oai-test"}
        with patch.dict(os.environ, env, clear=True), \
             self._mock_autodetect(), \
             patch("providers.OllamaProvider.is_available", return_value=True):
            reg = build_default_registry()
            assert reg.active_name == "anthropic"

    def test_registry_has_four_providers(self):
        env = {"ANTHROPIC_API_KEY": "sk-ant", "OPENAI_API_KEY": "sk-oai"}
        with patch.dict(os.environ, env, clear=True), \
             self._mock_autodetect(), \
             patch("providers.OllamaProvider.is_available", return_value=False):
            reg = build_default_registry()
            assert len(reg.providers) == 4
