"""Offline unit tests for core organizer pieces.

- Config._deep_update: nested merge semantics.
- RateLimiter (shared base in organizer/ratelimit.py): construction with
  custom limits and token/slot math. No test here may sleep: only
  non-blocking methods are called (can_make_request, wait_for_slot,
  get_backoff_delay, record_success/record_failure, is_circuit_open);
  acquire() is deliberately never called.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from organizer.config import Config
from organizer.ratelimit import RateLimiter


def _bare_config():
    """Config instance without running __init__ (no file I/O)."""
    return Config.__new__(Config)


# ---------------------------------------------------------------------------
# Config._deep_update
# ---------------------------------------------------------------------------

class TestDeepUpdate:
    def test_nested_merge_keeps_untouched_keys(self):
        cfg = _bare_config()
        base = {"a": {"x": 1, "y": 2}, "b": 1}
        cfg._deep_update(base, {"a": {"y": 20, "z": 30}, "c": 3})
        assert base == {"a": {"x": 1, "y": 20, "z": 30}, "b": 1, "c": 3}

    def test_scalar_overwrites_dict_and_vice_versa(self):
        cfg = _bare_config()
        base = {"a": {"x": 1}, "b": 5}
        cfg._deep_update(base, {"a": 9, "b": {"y": 2}})
        assert base == {"a": 9, "b": {"y": 2}}

    def test_deeply_nested_merge(self):
        cfg = _bare_config()
        base = {"l1": {"l2": {"l3": {"keep": 1, "over": 1}}}}
        cfg._deep_update(base, {"l1": {"l2": {"l3": {"over": 2, "new": 3}}}})
        assert base == {"l1": {"l2": {"l3": {"keep": 1, "over": 2, "new": 3}}}}

    def test_empty_update_is_noop(self):
        cfg = _bare_config()
        base = {"a": 1}
        cfg._deep_update(base, {})
        assert base == {"a": 1}

    def test_update_dict_not_mutated(self):
        cfg = _bare_config()
        base, update = {"a": {"x": 1}}, {"a": {"y": 2}}
        cfg._deep_update(base, update)
        assert update == {"a": {"y": 2}}


# ---------------------------------------------------------------------------
# RateLimiter base
# ---------------------------------------------------------------------------

class _TestLimiter(RateLimiter):
    """Concrete subclass of the shared base for testing."""
    provider_name = "Test"
    config_section = "llama"      # Config always has self.llama
    guard_config_attr = True
    default_max_requests_per_minute = 10
    default_max_concurrent = 2
    default_failure_threshold = 2
    default_recovery_timeout = 30
    default_half_open_max_calls = 2


def _limiter():
    # Point at a nonexistent config file so only defaults are used.
    return _TestLimiter(Config(config_file="nonexistent-offline-test.json"))


class TestRateLimiterConstruction:
    def test_custom_limits_applied(self):
        rl = _limiter()
        assert rl.max_requests_per_minute == 10
        assert rl.max_concurrent == 2
        assert rl.failure_threshold == 2
        assert rl.recovery_timeout == 30

    def test_initial_state(self):
        rl = _limiter()
        assert rl.circuit_state == "CLOSED"
        assert rl.failure_count == 0
        assert rl.throttle_factor == 1.0
        assert rl.concurrent_requests == 0

    def test_base_get_optimal_batch_size_not_implemented(self):
        import pytest
        with pytest.raises(NotImplementedError):
            _limiter().get_optimal_batch_size()


class TestRateLimiterSlots:
    def test_can_make_request_when_fresh(self):
        assert _limiter().can_make_request() is True

    def test_can_make_request_false_when_window_full(self):
        rl = _limiter()
        now = time.time()
        with rl.lock:
            rl.request_times.extend([now] * 10)
        assert rl.can_make_request() is False

    def test_stale_entries_do_not_count(self):
        rl = _limiter()
        with rl.lock:
            rl.request_times.extend([time.time() - 120] * 50)  # older than 1 min
        assert rl.can_make_request() is True

    def test_wait_for_slot_zero_when_free(self):
        assert _limiter().wait_for_slot() == 0.0

    def test_wait_for_slot_positive_when_full_no_sleep(self):
        rl = _limiter()
        now = time.time()
        with rl.lock:
            rl.request_times.extend([now] * 10)
        wait = rl.wait_for_slot()  # computed only; must not block
        assert 0 < wait <= 60


class TestBackoffDelay:
    def test_no_jitter_returns_current_delay(self):
        rl = _limiter()
        rl.jitter = False
        rl.current_delay = 2.0
        assert rl.get_backoff_delay() == 2.0

    def test_jitter_stays_within_band(self):
        rl = _limiter()
        rl.jitter = True
        rl.current_delay = 4.0
        for _ in range(50):
            d = rl.get_backoff_delay()
            assert 3.0 <= d <= 5.0  # +/-25% of 4.0

    def test_delay_never_below_floor(self):
        rl = _limiter()
        rl.jitter = False
        rl.current_delay = 0.0
        assert rl.get_backoff_delay() == 0.1


class TestCircuitBreaker:
    def test_failures_open_circuit(self):
        rl = _limiter()
        rl.record_failure()
        assert rl.circuit_state == "CLOSED"
        rl.record_failure()  # hits failure_threshold=2
        assert rl.failure_count == 2
        assert rl.circuit_state == "OPEN"
        assert rl.is_circuit_open() is True

    def test_recovery_timeout_moves_to_half_open(self):
        rl = _limiter()
        rl.record_failure()
        rl.record_failure()
        rl.last_failure_time = time.time() - 31  # past recovery_timeout
        assert rl.is_circuit_open() is False
        assert rl.circuit_state == "HALF_OPEN"

    def test_half_open_closes_after_enough_successes(self):
        rl = _limiter()
        rl.circuit_state = "HALF_OPEN"
        rl.half_open_calls = 0
        rl.record_success()
        assert rl.circuit_state == "HALF_OPEN"
        rl.record_success()  # half_open_max_calls=2 reached
        assert rl.circuit_state == "CLOSED"
        assert rl.failure_count == 0

    def test_half_open_failure_reopens(self):
        rl = _limiter()
        rl.circuit_state = "HALF_OPEN"
        rl.record_failure()
        assert rl.circuit_state == "OPEN"

    def test_success_decrements_failure_count(self):
        rl = _limiter()
        rl.failure_count = 1
        rl.current_delay = 4.0
        rl.record_success()
        assert rl.failure_count == 0
