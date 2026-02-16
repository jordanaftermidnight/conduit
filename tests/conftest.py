"""
conftest.py -- Shared fixtures for the Conduit test suite.

Provides mock providers, sample data, and reusable test scaffolding.
All external calls (HTTP, SDK clients) are mocked -- no real API traffic.
"""

import sys
import os
import pytest
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Make the server package importable from tests
# ---------------------------------------------------------------------------
SERVER_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "server")
sys.path.insert(0, os.path.abspath(SERVER_DIR))

from providers import (
    BaseProvider,
    ProviderResponse,
    ProviderRegistry,
    CircuitBreaker,
    ProviderHealth,
)


# ═══════════════════════════════════════════════════════════════════════════
# Mock Providers
# ═══════════════════════════════════════════════════════════════════════════

class MockSuccessProvider(BaseProvider):
    """A provider that always returns a successful response."""

    name = "mock_success"

    def __init__(self, model: str = "mock-model-ok"):
        self.model = model

    def chat(self, system: str, messages: list[dict], **kwargs) -> ProviderResponse:
        return ProviderResponse(
            text="Mock success response.",
            model=self.model,
            input_tokens=10,
            output_tokens=5,
            provider=self.name,
        )

    def is_available(self) -> bool:
        return True


class MockFailProvider(BaseProvider):
    """A provider that always raises an exception."""

    name = "mock_fail"

    def __init__(self, model: str = "mock-model-fail"):
        self.model = model

    def chat(self, system: str, messages: list[dict], **kwargs) -> ProviderResponse:
        raise ConnectionError("Simulated provider failure")

    def is_available(self) -> bool:
        return True


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures -- providers
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_success_provider():
    """Provider that always succeeds."""
    return MockSuccessProvider()


@pytest.fixture
def mock_fail_provider():
    """Provider that always raises."""
    return MockFailProvider()


@pytest.fixture
def fresh_breaker():
    """A fresh CircuitBreaker with default thresholds (3 failures, 60 s)."""
    return CircuitBreaker(failure_threshold=3, recovery_seconds=60.0)


@pytest.fixture
def registry_with_success(mock_success_provider):
    """ProviderRegistry containing one always-successful provider."""
    reg = ProviderRegistry()
    reg.register(mock_success_provider, set_active=True)
    return reg


@pytest.fixture
def registry_with_failover(mock_success_provider, mock_fail_provider):
    """Registry where primary fails and secondary succeeds."""
    reg = ProviderRegistry()
    reg.register(mock_fail_provider, set_active=True)
    reg.register(mock_success_provider)
    return reg


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures -- sample data
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def sample_conversation_history():
    """Minimal conversation for tests that need message history."""
    return [
        {"role": "user", "content": "Give me a kick pattern at 140 BPM."},
        {
            "role": "assistant",
            "content": (
                'Here is a four-on-the-floor kick pattern:\n\n```json\n'
                '{"midi_notes": [\n'
                '  {"pitch": 36, "velocity": 110, "start_beat": 0.0, "duration_beats": 0.25},\n'
                '  {"pitch": 36, "velocity": 110, "start_beat": 1.0, "duration_beats": 0.25},\n'
                '  {"pitch": 36, "velocity": 110, "start_beat": 2.0, "duration_beats": 0.25},\n'
                '  {"pitch": 36, "velocity": 110, "start_beat": 3.0, "duration_beats": 0.25}\n'
                ']}\n```'
            ),
        },
    ]


@pytest.fixture
def sample_session_context():
    """Session context dict as it would arrive from M4L."""
    return {
        "bpm": 140.0,
        "time_signature": "4/4",
        "key": "C minor",
        "selected_track": "1-Drums",
        "track_names": ["1-Drums", "2-Bass", "3-Pad", "4-Lead"],
        "playing": False,
        "song_time": 0.0,
        "extra": {"scale": "natural minor"},
    }


@pytest.fixture
def sample_midi_json_block():
    """A well-formed MIDI JSON block for validation tests."""
    return {
        "midi_notes": [
            {"pitch": 60, "velocity": 100, "start_beat": 0.0, "duration_beats": 0.5},
            {"pitch": 62, "velocity": 90, "start_beat": 0.5, "duration_beats": 0.5},
            {"pitch": 64, "velocity": 80, "start_beat": 1.0, "duration_beats": 1.0},
        ]
    }


@pytest.fixture
def sample_param_json_block():
    """A well-formed param-change JSON block."""
    return {
        "params": [
            {"track": 1, "device": "Wavetable", "param": "Filter Freq", "value": 0.75},
            {"track": "2-Bass", "device": "Saturator", "param": "Drive", "value": 0.6},
        ]
    }


@pytest.fixture
def sample_response_with_json():
    """An LLM response string containing embedded JSON blocks."""
    return (
        "Sure! Here is a hi-hat pattern:\n\n"
        "```json\n"
        '{"midi_notes": [\n'
        '  {"pitch": 42, "velocity": 80, "start_beat": 0.0, "duration_beats": 0.25},\n'
        '  {"pitch": 42, "velocity": 60, "start_beat": 0.5, "duration_beats": 0.25}\n'
        "]}\n"
        "```\n\n"
        "And a parameter tweak:\n\n"
        "```json\n"
        '{"params": [{"track": 1, "device": "Utility", "param": "Gain", "value": -3.0}]}\n'
        "```\n"
    )
