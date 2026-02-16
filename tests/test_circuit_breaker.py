"""
test_circuit_breaker.py -- Tests for CircuitBreaker and ProviderHealth.

Covers:
  - State transitions: closed -> open after N failures
  - Recovery: open -> half_open after timeout
  - half_open -> closed on success
  - half_open -> open on failure
  - Health score calculation (error rate + speed penalty)
  - Manual reset
  - is_available() respects states
  - why_unavailable() messages
  - Response time sliding window (deque maxlen=20)
  - Time mocking for the 60 s cooldown
"""

import time
from collections import deque
from unittest.mock import patch

import pytest

from providers import CircuitBreaker, ProviderHealth


# ═══════════════════════════════════════════════════════════════════════════
# ProviderHealth dataclass
# ═══════════════════════════════════════════════════════════════════════════

class TestProviderHealth:
    """Unit tests for the ProviderHealth dataclass and its properties."""

    def test_default_values(self):
        h = ProviderHealth()
        assert h.consecutive_failures == 0
        assert h.state == "closed"
        assert h.tripped_at == 0.0
        assert h.error_count == 0
        assert h.success_count == 0
        assert len(h.response_times) == 0

    def test_avg_response_ms_empty(self):
        h = ProviderHealth()
        assert h.avg_response_ms == 0.0

    def test_avg_response_ms_with_data(self):
        h = ProviderHealth()
        h.response_times.extend([100.0, 200.0, 300.0])
        assert h.avg_response_ms == 200.0

    def test_health_score_no_requests(self):
        """No traffic yet -- score should be 100 (benefit of the doubt)."""
        h = ProviderHealth()
        assert h.health_score == 100

    def test_health_score_all_success_fast(self):
        """All successes with fast responses -- near-perfect score."""
        h = ProviderHealth()
        h.success_count = 20
        h.error_count = 0
        # all responses under 100 ms
        h.response_times.extend([50.0] * 20)
        # speed_penalty = min(50/1000 * 10, 40) = 0.5
        # error_rate = 0
        # score = 100 - 0 - 0.5 = 99
        assert h.health_score == 99

    def test_health_score_all_errors(self):
        """100% error rate should drive score to near zero."""
        h = ProviderHealth()
        h.error_count = 10
        h.success_count = 0
        # error_rate = 1.0 -> penalty = 60
        # no response times -> speed_penalty = 0
        # score = max(0, 100 - 60 - 0) = 40
        assert h.health_score == 40

    def test_health_score_mixed_errors_and_slow(self):
        """50% error rate + slow responses -> significant penalty."""
        h = ProviderHealth()
        h.error_count = 5
        h.success_count = 5
        # avg = 3000 ms -> speed_penalty = min(3.0 * 10, 40) = 30
        h.response_times.extend([3000.0] * 10)
        # error_rate = 0.5 -> error_penalty = 30
        # score = max(0, 100 - 30 - 30) = 40
        assert h.health_score == 40

    def test_health_score_slow_capped_penalty(self):
        """Speed penalty is capped at 40 even for very slow responses."""
        h = ProviderHealth()
        h.success_count = 1
        h.error_count = 0
        h.response_times.append(10_000.0)  # 10 seconds
        # speed_penalty = min(10.0 * 10, 40) = 40
        # score = 100 - 0 - 40 = 60
        assert h.health_score == 60

    def test_response_times_maxlen(self):
        """The sliding window only keeps the most recent 20 entries."""
        h = ProviderHealth()
        assert h.response_times.maxlen == 20
        for i in range(30):
            h.response_times.append(float(i))
        assert len(h.response_times) == 20
        # Oldest should be 10 (entries 0..9 were evicted)
        assert h.response_times[0] == 10.0
        assert h.response_times[-1] == 29.0


# ═══════════════════════════════════════════════════════════════════════════
# CircuitBreaker -- State Transitions
# ═══════════════════════════════════════════════════════════════════════════

class TestCircuitBreakerTransitions:
    """State machine: closed -> open -> half_open -> closed / open."""

    def test_initial_state_is_closed(self, fresh_breaker):
        assert fresh_breaker.is_available("test")
        h = fresh_breaker._get("test")
        assert h.state == "closed"

    def test_closed_to_open_after_threshold(self, fresh_breaker):
        """Three consecutive failures should trip the circuit."""
        for _ in range(3):
            fresh_breaker.record_failure("svc")
        h = fresh_breaker._get("svc")
        assert h.state == "open"
        assert h.consecutive_failures == 3

    def test_remains_closed_below_threshold(self, fresh_breaker):
        fresh_breaker.record_failure("svc")
        fresh_breaker.record_failure("svc")
        h = fresh_breaker._get("svc")
        assert h.state == "closed"

    def test_success_resets_consecutive_counter(self, fresh_breaker):
        """A success in the middle resets the consecutive-failure count."""
        fresh_breaker.record_failure("svc")
        fresh_breaker.record_failure("svc")
        fresh_breaker.record_success("svc", 100.0)
        fresh_breaker.record_failure("svc")
        fresh_breaker.record_failure("svc")
        h = fresh_breaker._get("svc")
        assert h.state == "closed"
        assert h.consecutive_failures == 2

    def test_open_to_half_open_after_timeout(self, fresh_breaker):
        """After recovery_seconds elapse the circuit goes half_open."""
        for _ in range(3):
            fresh_breaker.record_failure("svc")

        h = fresh_breaker._get("svc")
        assert h.state == "open"

        # Simulate time advancing past the 60 s cooldown
        with patch("providers.time") as mock_time:
            mock_time.time.return_value = h.tripped_at + 61.0
            available = fresh_breaker.is_available("svc")
            assert available is True
            assert h.state == "half_open"

    def test_stays_open_before_timeout(self, fresh_breaker):
        """Circuit must stay open if recovery time hasn't elapsed."""
        for _ in range(3):
            fresh_breaker.record_failure("svc")
        h = fresh_breaker._get("svc")

        with patch("providers.time") as mock_time:
            mock_time.time.return_value = h.tripped_at + 30.0  # only 30 s
            assert fresh_breaker.is_available("svc") is False
            assert h.state == "open"

    def test_half_open_to_closed_on_success(self, fresh_breaker):
        """A successful call while half_open should close the circuit."""
        for _ in range(3):
            fresh_breaker.record_failure("svc")
        h = fresh_breaker._get("svc")
        h.state = "half_open"  # simulate timer elapsing

        fresh_breaker.record_success("svc", 200.0)
        assert h.state == "closed"
        assert h.consecutive_failures == 0

    def test_half_open_to_open_on_failure(self, fresh_breaker):
        """A failure while half_open should re-open the circuit immediately."""
        for _ in range(3):
            fresh_breaker.record_failure("svc")
        h = fresh_breaker._get("svc")
        h.state = "half_open"

        fresh_breaker.record_failure("svc")
        assert h.state == "open"
        assert h.tripped_at > 0


# ═══════════════════════════════════════════════════════════════════════════
# CircuitBreaker -- is_available()
# ═══════════════════════════════════════════════════════════════════════════

class TestCircuitBreakerAvailability:

    def test_available_when_closed(self, fresh_breaker):
        assert fresh_breaker.is_available("new_provider") is True

    def test_unavailable_when_open(self, fresh_breaker):
        for _ in range(3):
            fresh_breaker.record_failure("svc")
        # Still within cooldown window
        assert fresh_breaker.is_available("svc") is False

    def test_available_when_half_open(self, fresh_breaker):
        h = fresh_breaker._get("svc")
        h.state = "half_open"
        assert fresh_breaker.is_available("svc") is True

    def test_open_transitions_to_half_open_when_time_elapsed(self, fresh_breaker):
        for _ in range(3):
            fresh_breaker.record_failure("svc")
        h = fresh_breaker._get("svc")
        tripped = h.tripped_at

        with patch("providers.time") as mock_time:
            mock_time.time.return_value = tripped + 60.0
            assert fresh_breaker.is_available("svc") is True
            assert h.state == "half_open"


# ═══════════════════════════════════════════════════════════════════════════
# CircuitBreaker -- why_unavailable()
# ═══════════════════════════════════════════════════════════════════════════

class TestCircuitBreakerWhyUnavailable:

    def test_empty_string_when_healthy(self, fresh_breaker):
        assert fresh_breaker.why_unavailable("svc") == ""

    def test_reason_when_open(self, fresh_breaker):
        for _ in range(3):
            fresh_breaker.record_failure("svc")

        with patch("providers.time") as mock_time:
            mock_time.time.return_value = fresh_breaker._get("svc").tripped_at + 10.0
            reason = fresh_breaker.why_unavailable("svc")
            assert "circuit open" in reason
            assert "3 failures" in reason
            assert "retry in" in reason

    def test_empty_string_when_half_open(self, fresh_breaker):
        h = fresh_breaker._get("svc")
        h.state = "half_open"
        assert fresh_breaker.why_unavailable("svc") == ""

    def test_countdown_decreases(self, fresh_breaker):
        for _ in range(3):
            fresh_breaker.record_failure("svc")
        tripped = fresh_breaker._get("svc").tripped_at

        with patch("providers.time") as mock_time:
            mock_time.time.return_value = tripped + 10.0
            reason_early = fresh_breaker.why_unavailable("svc")

        with patch("providers.time") as mock_time:
            mock_time.time.return_value = tripped + 50.0
            reason_late = fresh_breaker.why_unavailable("svc")

        # "retry in 50s" vs "retry in 10s"
        assert "50" in reason_early
        assert "10" in reason_late


# ═══════════════════════════════════════════════════════════════════════════
# CircuitBreaker -- Manual Reset
# ═══════════════════════════════════════════════════════════════════════════

class TestCircuitBreakerReset:

    def test_reset_clears_state(self, fresh_breaker):
        for _ in range(3):
            fresh_breaker.record_failure("svc")
        assert fresh_breaker._get("svc").state == "open"

        fresh_breaker.reset("svc")
        h = fresh_breaker._get("svc")
        assert h.state == "closed"
        assert h.consecutive_failures == 0
        assert h.error_count == 0
        assert h.success_count == 0

    def test_reset_unknown_provider_is_noop(self, fresh_breaker):
        """Resetting a provider that was never registered does nothing."""
        fresh_breaker.reset("nonexistent")  # should not raise
        assert "nonexistent" not in fresh_breaker._health


# ═══════════════════════════════════════════════════════════════════════════
# CircuitBreaker -- get_health / get_all_health
# ═══════════════════════════════════════════════════════════════════════════

class TestCircuitBreakerHealth:

    def test_get_health_returns_expected_keys(self, fresh_breaker):
        fresh_breaker.record_success("svc", 150.0)
        info = fresh_breaker.get_health("svc")
        expected_keys = {
            "state",
            "health_score",
            "consecutive_failures",
            "avg_response_ms",
            "total_success",
            "total_errors",
        }
        assert set(info.keys()) == expected_keys

    def test_get_health_values(self, fresh_breaker):
        fresh_breaker.record_success("svc", 100.0)
        fresh_breaker.record_success("svc", 200.0)
        fresh_breaker.record_failure("svc", 300.0)
        info = fresh_breaker.get_health("svc")
        assert info["state"] == "closed"
        assert info["total_success"] == 2
        assert info["total_errors"] == 1
        assert info["consecutive_failures"] == 1
        assert info["avg_response_ms"] == 200.0

    def test_get_all_health_multiple_providers(self, fresh_breaker):
        fresh_breaker.record_success("alpha", 50.0)
        fresh_breaker.record_failure("beta")
        all_health = fresh_breaker.get_all_health()
        assert "alpha" in all_health
        assert "beta" in all_health
        assert all_health["alpha"]["total_success"] == 1
        assert all_health["beta"]["total_errors"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# CircuitBreaker -- Response Time Sliding Window
# ═══════════════════════════════════════════════════════════════════════════

class TestCircuitBreakerResponseTimes:

    def test_response_times_recorded_on_success(self, fresh_breaker):
        fresh_breaker.record_success("svc", 123.4)
        h = fresh_breaker._get("svc")
        assert list(h.response_times) == [123.4]

    def test_response_times_recorded_on_failure_with_ms(self, fresh_breaker):
        fresh_breaker.record_failure("svc", 456.7)
        h = fresh_breaker._get("svc")
        assert list(h.response_times) == [456.7]

    def test_response_times_not_recorded_on_failure_zero_ms(self, fresh_breaker):
        fresh_breaker.record_failure("svc", 0)
        h = fresh_breaker._get("svc")
        assert len(h.response_times) == 0

    def test_sliding_window_evicts_old_entries(self, fresh_breaker):
        for i in range(25):
            fresh_breaker.record_success("svc", float(i * 10))
        h = fresh_breaker._get("svc")
        assert len(h.response_times) == 20
        # Oldest remaining should be entry 5 (50 ms)
        assert h.response_times[0] == 50.0


# ═══════════════════════════════════════════════════════════════════════════
# CircuitBreaker -- Custom Thresholds
# ═══════════════════════════════════════════════════════════════════════════

class TestCircuitBreakerCustomThresholds:

    def test_custom_failure_threshold(self):
        cb = CircuitBreaker(failure_threshold=5, recovery_seconds=30.0)
        for _ in range(4):
            cb.record_failure("svc")
        assert cb._get("svc").state == "closed"
        cb.record_failure("svc")
        assert cb._get("svc").state == "open"

    def test_custom_recovery_seconds(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_seconds=10.0)
        cb.record_failure("svc")
        h = cb._get("svc")
        assert h.state == "open"

        with patch("providers.time") as mock_time:
            mock_time.time.return_value = h.tripped_at + 11.0
            assert cb.is_available("svc") is True
            assert h.state == "half_open"
