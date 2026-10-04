"""Unified rate limiting with circuit breaker and adaptive throttling.

``GeminiRateLimiter`` and ``LlamaRateLimiter`` were ~200 lines each and
near-identical. They now share the ``RateLimiter`` base class below; the two
provider subclasses only parameterize what truly differs (limits, backoff
aggressiveness, adaptive-tuning tiers, per-second tracking, log labels) plus a
genuinely different ``get_optimal_batch_size`` algorithm each.
"""
import time
import random
import threading
from collections import deque

from .common import logger
from .config import Config


class RateLimiter:
    """Shared rate limiter with circuit breaker and adaptive throttling."""

    # -- provider parameterization (overridden by subclasses) --
    provider_name = ""            # label used in log messages, e.g. "Gemini"
    config_section = ""           # config.<section> holds the 'performance' overrides
    guard_config_attr = False     # True: tolerate a config object lacking the section
    supports_burst_mode = False   # True: read the 'burst_mode' performance flag

    # Default limits (used when the config file does not override them)
    default_max_requests_per_minute = 2000
    default_max_concurrent = 3
    default_adaptive_rate_limiting = True
    default_failure_threshold = 5
    default_recovery_timeout = 30
    default_half_open_max_calls = 3
    default_initial_delay = 1.0
    default_max_delay = 60.0
    default_multiplier = 2.0
    default_jitter = True

    # Optional stricter per-second window (Gemini free tier); None disables it
    max_requests_per_second = None
    # Adaptive throttling starts here (Gemini starts more conservative)
    initial_throttle_factor = 1.0
    # Floor for computed wait times (Gemini never waits less than 0.1s)
    min_wait_time = 0.0
    # Backoff applies when failure_count exceeds this (Gemini: > 0, Llama: > 2)
    backoff_failure_threshold = 2
    # Cap on the backoff delay applied in acquire() (Llama caps at 0.5s)
    backoff_cap = None
    # Gemini logs rate-limit waits at debug level; Llama stays silent
    log_wait_debug = False

    # Adaptive tuning: only adjust once we have this many recent requests
    adaptive_min_data_points = 10
    # (error_rate_threshold, throttle_floor, throttle_factor) applied top-down
    adaptive_reduce_tiers = ((0.15, 0.3, 0.8), (0.05, 0.5, 0.9))
    # Recovery when the error rate drops below this
    adaptive_recover_below = 0.02
    adaptive_recover_cap = 1.0
    adaptive_recover_factor = 1.02

    def __init__(self, config: Config):
        self.config = config
        perf = self._load_perf(config)

        self.max_requests_per_minute = perf.get('max_requests_per_minute', self.default_max_requests_per_minute)
        self.max_concurrent = perf.get('max_concurrent_requests', self.default_max_concurrent)
        self.adaptive_rate_limiting = perf.get('adaptive_rate_limiting', self.default_adaptive_rate_limiting)
        if self.supports_burst_mode:
            self.burst_mode = perf.get('burst_mode', False)

        # Circuit breaker configuration
        cb_config = perf.get('circuit_breaker', {})
        self.failure_threshold = cb_config.get('failure_threshold', self.default_failure_threshold)
        self.recovery_timeout = cb_config.get('recovery_timeout', self.default_recovery_timeout)
        self.half_open_max_calls = cb_config.get('half_open_max_calls', self.default_half_open_max_calls)

        # Backoff strategy
        backoff_config = perf.get('backoff_strategy', {})
        self.initial_delay = backoff_config.get('initial_delay', self.default_initial_delay)
        self.max_delay = backoff_config.get('max_delay', self.default_max_delay)
        self.multiplier = backoff_config.get('multiplier', self.default_multiplier)
        self.jitter = backoff_config.get('jitter', self.default_jitter)

        # Rate limiting state
        self.request_times = deque()
        self.concurrent_requests = 0
        self.lock = threading.Lock()
        self.semaphore = threading.Semaphore(self.max_concurrent)

        # Adaptive throttling
        self.success_rate = 1.0
        self.recent_errors = deque()
        self.throttle_factor = self.initial_throttle_factor
        self.current_delay = self.initial_delay

        # Circuit breaker state
        self.circuit_state = "CLOSED"
        self.failure_count = 0
        self.last_failure_time = 0
        self.half_open_calls = 0

        # Per-second tracking (only enforced when max_requests_per_second is set)
        self.requests_this_second = deque()

    def _load_perf(self, config: Config) -> dict:
        """Read the provider's 'performance' config section."""
        if self.guard_config_attr and not hasattr(config, self.config_section):
            return {}
        return getattr(config, self.config_section).get('performance', {})

    def _tagged(self, message: str) -> str:
        """Prefix a message with the provider name ("Gemini ...") or capitalize it."""
        if self.provider_name:
            return f"{self.provider_name} {message}"
        return message[0].upper() + message[1:]

    def can_make_request(self) -> bool:
        """Check if we can make a request without hitting rate limits"""
        with self.lock:
            now = time.time()

            # Remove requests older than 1 minute
            while self.request_times and now - self.request_times[0] > 60:
                self.request_times.popleft()

            # Enforce the per-second limit first when configured (most restrictive)
            if self.max_requests_per_second is not None:
                # Remove requests older than 1 second
                while self.requests_this_second and now - self.requests_this_second[0] > 1:
                    self.requests_this_second.popleft()

                if len(self.requests_this_second) >= self.max_requests_per_second:
                    return False

            # Apply adaptive throttling to per-minute limit
            effective_limit = int(self.max_requests_per_minute * self.throttle_factor)

            return len(self.request_times) < effective_limit

    def wait_for_slot(self) -> float:
        """Calculate how long to wait for the next available slot"""
        with self.lock:
            now = time.time()

            # Check per-second limit first when configured
            if self.max_requests_per_second is not None:
                while self.requests_this_second and now - self.requests_this_second[0] > 1:
                    self.requests_this_second.popleft()

                if len(self.requests_this_second) >= self.max_requests_per_second:
                    # Wait until the oldest request in this second expires
                    oldest_this_second = self.requests_this_second[0]
                    wait_time = 1.0 - (now - oldest_this_second)
                    return max(self.min_wait_time, wait_time)

            # Check per-minute limit
            if not self.request_times:
                return 0.0

            oldest_request = self.request_times[0]
            effective_limit = int(self.max_requests_per_minute * self.throttle_factor)

            if len(self.request_times) >= effective_limit:
                wait_time = 60 - (now - oldest_request)
                return max(self.min_wait_time, wait_time)

            return 0.0

    def acquire(self):
        """Acquire permission to make a request with circuit breaker protection"""
        # Check circuit breaker first
        if self.is_circuit_open():
            wait_time = self.recovery_timeout - (time.time() - self.last_failure_time)
            if wait_time > 0:
                logger.info(self._tagged(f"circuit breaker OPEN - waiting {wait_time:.1f}s for recovery"))
                time.sleep(wait_time)
            if self.is_circuit_open():
                raise Exception(self._tagged("circuit breaker is OPEN - API unavailable"))

        # Apply backoff delay
        if self.failure_count > self.backoff_failure_threshold:
            backoff_delay = self.get_backoff_delay()
            if self.backoff_cap is not None:
                backoff_delay = min(self.backoff_cap, backoff_delay)
            if backoff_delay > 0.1:
                time.sleep(backoff_delay)

        # Wait for concurrent request slot
        self.semaphore.acquire()

        # Wait for rate limit slot
        wait_time = self.wait_for_slot()
        if wait_time > 0:
            if self.log_wait_debug:
                logger.debug(self._tagged(f"rate limit: waiting {wait_time:.1f}s"))
            time.sleep(wait_time)

        with self.lock:
            now = time.time()
            self.request_times.append(now)
            if self.max_requests_per_second is not None:
                self.requests_this_second.append(now)
            self.concurrent_requests += 1

    def release(self, success: bool = True):
        """Release request slot and update success metrics"""
        if success:
            self.record_success()
        else:
            self.record_failure()

        with self.lock:
            self.concurrent_requests -= 1

            # Update adaptive throttling
            if self.adaptive_rate_limiting:
                now = time.time()

                if not success:
                    self.recent_errors.append(now)

                # Remove old errors
                while self.recent_errors and now - self.recent_errors[0] > 300:
                    self.recent_errors.popleft()

                # Calculate error rate and adjust throttle
                total_recent = len([t for t in self.request_times if now - t < 300])
                if total_recent > self.adaptive_min_data_points:
                    error_rate = len(self.recent_errors) / total_recent

                    for threshold, floor, factor in self.adaptive_reduce_tiers:
                        if error_rate > threshold:
                            self.throttle_factor = max(floor, self.throttle_factor * factor)
                            break
                    else:
                        if error_rate < self.adaptive_recover_below:
                            self.throttle_factor = min(
                                self.adaptive_recover_cap,
                                self.throttle_factor * self.adaptive_recover_factor,
                            )

        self.semaphore.release()

    def get_optimal_batch_size(self) -> int:
        """Get optimal batch size based on current performance"""
        raise NotImplementedError("provider subclasses implement their own batch sizing")

    def is_circuit_open(self) -> bool:
        """Check if circuit breaker is open"""
        with self.lock:
            if self.circuit_state == "OPEN":
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self.circuit_state = "HALF_OPEN"
                    self.half_open_calls = 0
                    logger.info(self._tagged("circuit breaker transitioning to HALF_OPEN state"))
                    return False
                return True
            return False

    def record_success(self):
        """Record a successful API call"""
        with self.lock:
            if self.circuit_state == "HALF_OPEN":
                self.half_open_calls += 1
                if self.half_open_calls >= self.half_open_max_calls:
                    self.circuit_state = "CLOSED"
                    self.failure_count = 0
                    self.current_delay = self.initial_delay
                    logger.info(self._tagged("circuit breaker CLOSED - API recovered"))
            elif self.circuit_state == "CLOSED":
                if self.failure_count > 0:
                    self.failure_count = max(0, self.failure_count - 1)
                    self.current_delay = max(self.initial_delay, self.current_delay / self.multiplier)

    def record_failure(self):
        """Record a failed API call"""
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.time()

            self.current_delay = min(self.max_delay, self.current_delay * self.multiplier)

            if self.circuit_state == "HALF_OPEN":
                self.circuit_state = "OPEN"
                logger.warning(self._tagged("circuit breaker OPEN - API still failing"))
            elif self.failure_count >= self.failure_threshold:
                self.circuit_state = "OPEN"
                logger.warning(self._tagged(f"circuit breaker OPEN - {self.failure_count} consecutive failures"))

    def get_backoff_delay(self) -> float:
        """Get current backoff delay with jitter"""
        delay = self.current_delay
        if self.jitter:
            jitter_range = delay * 0.25
            delay += random.uniform(-jitter_range, jitter_range)
        return max(0.1, delay)


class GeminiRateLimiter(RateLimiter):
    """Conservative rate limiter for Gemini API with strict rate limiting"""

    provider_name = "Gemini"
    config_section = "gemini"
    guard_config_attr = True

    default_max_requests_per_minute = 1500  # Conservative (free tier: 1800)
    default_max_concurrent = 3  # Very conservative
    default_failure_threshold = 3  # Lower threshold - more sensitive
    default_recovery_timeout = 60  # Longer recovery
    default_half_open_max_calls = 2
    default_initial_delay = 2.0  # Start with longer delay
    default_max_delay = 120.0  # Longer max delay
    default_multiplier = 2.5  # More aggressive backoff

    # Gemini-specific: track requests per second (conservative free-tier limit)
    max_requests_per_second = 25
    initial_throttle_factor = 0.7  # Start at 70% capacity
    min_wait_time = 0.1
    backoff_failure_threshold = 0
    log_wait_debug = True

    # More aggressive adaptive tuning, caps recovery at 80%
    adaptive_min_data_points = 5
    adaptive_reduce_tiers = ((0.1, 0.2, 0.6), (0.05, 0.4, 0.8))
    adaptive_recover_below = 0.01
    adaptive_recover_cap = 0.8
    adaptive_recover_factor = 1.01

    def get_optimal_batch_size(self) -> int:
        """Get optimal batch size - very conservative for Gemini"""
        if self.circuit_state == "OPEN":
            return 1
        elif self.throttle_factor < 0.5:
            return 1
        elif self.throttle_factor < 0.7:
            return 2
        else:
            return 3  # Never go above 3 for Gemini free tier


class LlamaRateLimiter(RateLimiter):
    """Advanced rate limiter for Llama API with circuit breaker and adaptive throttling"""

    provider_name = ""
    config_section = "llama"
    guard_config_attr = False
    supports_burst_mode = True

    default_max_requests_per_minute = 2000
    default_max_concurrent = 15
    default_failure_threshold = 5
    default_recovery_timeout = 30
    default_half_open_max_calls = 3
    default_initial_delay = 1.0
    default_max_delay = 60.0
    default_multiplier = 2.0

    max_requests_per_second = None
    initial_throttle_factor = 1.0
    min_wait_time = 0.0
    backoff_failure_threshold = 2
    backoff_cap = 0.5
    log_wait_debug = False

    adaptive_min_data_points = 10
    adaptive_reduce_tiers = ((0.15, 0.3, 0.8), (0.05, 0.5, 0.9))
    adaptive_recover_below = 0.02
    adaptive_recover_cap = 1.0
    adaptive_recover_factor = 1.02

    def get_optimal_batch_size(self) -> int:
        """Get optimal batch size based on current performance"""
        base_batch_size = self.config.llama.get('performance', {}).get('optimal_batch_size', 2)

        if self.adaptive_rate_limiting:
            # Reduce batch size if we're having errors or circuit is open
            if self.circuit_state == "OPEN":
                return 1
            elif self.throttle_factor < 0.8:
                return max(1, base_batch_size // 2)
            elif self.throttle_factor > 0.95 and self.circuit_state == "CLOSED":
                return min(4, base_batch_size * 2)

        return base_batch_size
