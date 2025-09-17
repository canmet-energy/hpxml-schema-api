"""Performance monitoring and analytics utilities.

The monitoring layer aggregates runtime telemetry so other components (REST
endpoints, GraphQL resolvers, validation engines, cache handlers) can record
lightweight events without embedding metric aggregation logic. Emphasis is on
low overhead, in-process statistics—no external backend is required.

Collected domains:
        * Cache performance (hit/miss ratio, evictions, memory footprint)
        * Endpoint latency & error rates (rolling sample window + aggregates)
        * System snapshot (uptime, active/peak connections, memory, CPU)
        * Temporal usage patterns (simple hour-of-day / day-of-week counters)
        * Recent activity (fixed-size deques for debugging / introspection)

Design principles:
        1. Thread safety via a shared re‑entrant lock (`RLock`).
        2. Non-blocking fast path: metric updates avoid heavy computation; derived
             insights (recommendations) are computed on demand.
        3. Serialization ready: summary outputs are primitive-only dictionaries for
             JSON encoding without custom encoders.

Example (recording API request)::

        from hpxml_schema_api.monitoring import get_monitor
        monitor = get_monitor()
        # After handling a request
        monitor.record_endpoint_request("/v1/validate", response_time=0.032, status_code=200)
        print(monitor.get_performance_summary()["api"]["total_requests"])  # -> 1

Example (cache instrumentation)::

        monitor.record_cache_hit(response_time=0.001)
        monitor.record_cache_miss(response_time=0.004)
        analytics = monitor.get_cache_analytics()
        print(analytics["performance"]["hit_rate_percent"])  # e.g., 50.0

Lifecycle integration:
        * Initialization is lazy via :func:`get_monitor` or explicit via
            :func:`initialize_monitor` (which can enable/disable detailed response
            time sampling).
        * Export with :meth:`PerformanceMonitor.export_metrics` for offline
            diagnostics or test artifacts.
        * Reset with :meth:`PerformanceMonitor.reset_metrics` during integration tests
            to guarantee clean baselines.

Future enhancements (roadmap notes):
        * Optional pluggable backend (Prometheus/OpenTelemetry exporter).
        * Percentile latency calculation (P95/P99) using reservoir sampling.
        * Adaptive recommendation thresholds based on historical baselines.
"""

from __future__ import annotations

import json
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class CacheMetrics:
    """Point-in-time and aggregate cache performance metrics.

    Attributes:
        hits: Number of successful cache lookups.
        misses: Number of lookups that required fallback computation.
        evictions: Entries removed due to capacity or TTL policy.
        total_requests: Aggregate hits + misses.
        hit_rate: Rolling hit ratio (0..1) updated per request.
        average_response_time: Mean time for cache operations (seconds).
        cache_size: Current number of entries.
        memory_usage_mb: Approximate memory consumption (MB) if known.
    """

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    total_requests: int = 0
    hit_rate: float = 0.0
    average_response_time: float = 0.0
    cache_size: int = 0
    memory_usage_mb: float = 0.0


@dataclass
class EndpointMetrics:
    """Aggregated metrics for a single logical endpoint.

    Attributes:
        total_requests: Count of invocations.
        total_response_time: Cumulative latency (seconds).
        average_response_time: Mean latency (seconds).
        error_count: Number of requests resulting in error (HTTP >= 400).
        error_rate: error_count / total_requests (0..1).
        last_accessed: Datetime of most recent invocation.
        response_times: Rolling window (deque) of recent latencies for ad‑hoc analysis.
    """

    total_requests: int = 0
    total_response_time: float = 0.0
    average_response_time: float = 0.0
    error_count: int = 0
    error_rate: float = 0.0
    last_accessed: Optional[datetime] = None
    response_times: deque = field(default_factory=lambda: deque(maxlen=100))


@dataclass
class SystemMetrics:
    """High-level process/system snapshot metrics.

    Attributes:
        uptime_seconds: Elapsed wall-clock time since monitor start.
        total_requests: Global aggregate of endpoint requests.
        active_connections: Currently open connection count (if tracked externally).
        peak_connections: Historical high-water mark for simultaneous connections.
        memory_usage_mb: Approximate working set size.
        cpu_usage_percent: Recent CPU usage sample in percent (0..100).
    """

    uptime_seconds: float = 0.0
    total_requests: int = 0
    active_connections: int = 0
    peak_connections: int = 0
    memory_usage_mb: float = 0.0
    cpu_usage_percent: float = 0.0


class PerformanceMonitor:
    """Central coordinator for recording and querying metrics.

    Thread-safe methods mutate internal counters while summary access methods
    consolidate data into JSON-ready dictionaries. Intended to be shared as a
    singleton within a process.
    """

    def __init__(self, enable_detailed_tracking: bool = True):
        """Initialize performance monitor.

        Args:
            enable_detailed_tracking: If False, skips per-request latency deque
                population to minimize overhead in high-throughput scenarios.
        """
        self.enable_detailed_tracking = enable_detailed_tracking
        self.start_time = datetime.now()

        # Thread-safe metrics storage
        self._lock = threading.RLock()

        # Cache metrics
        self.cache_metrics = CacheMetrics()

        # Endpoint metrics
        self.endpoint_metrics: Dict[str, EndpointMetrics] = defaultdict(EndpointMetrics)

        # System metrics
        self.system_metrics = SystemMetrics()

        # Recent activity tracking
        self.recent_requests: deque = deque(maxlen=1000)
        self.recent_errors: deque = deque(maxlen=100)

        # Performance patterns
        self.hourly_patterns: Dict[int, Dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self.daily_patterns: Dict[str, Dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )

    def record_cache_hit(self, response_time: float = 0.0) -> None:
        """Record a cache hit.

        Args:
            response_time: Time in seconds for the fulfilled lookup (optional).
        """
        with self._lock:
            self.cache_metrics.hits += 1
            self.cache_metrics.total_requests += 1
            self._update_cache_metrics(response_time)

    def record_cache_miss(self, response_time: float = 0.0) -> None:
        """Record a cache miss.

        Args:
            response_time: Time in seconds spent generating the fallback value.
        """
        with self._lock:
            self.cache_metrics.misses += 1
            self.cache_metrics.total_requests += 1
            self._update_cache_metrics(response_time)

    def record_cache_eviction(self) -> None:
        """Record eviction (capacity or TTL driven)."""
        with self._lock:
            self.cache_metrics.evictions += 1

    def _update_cache_metrics(self, response_time: float) -> None:
        """Update derived cache metrics (hit rate, average response time)."""
        total = self.cache_metrics.total_requests
        if total > 0:
            self.cache_metrics.hit_rate = self.cache_metrics.hits / total

        if response_time > 0:
            # Simple moving average for response time
            current_avg = self.cache_metrics.average_response_time
            self.cache_metrics.average_response_time = (
                current_avg * (total - 1) + response_time
            ) / total

    def record_endpoint_request(
        self, endpoint: str, response_time: float, status_code: int = 200
    ) -> None:
        """Record an API endpoint invocation.

        Args:
            endpoint: Logical endpoint name or path.
            response_time: Time in seconds for handling the request.
            status_code: HTTP status used to compute error rate (>=400 counts as error).
        """
        with self._lock:
            metrics = self.endpoint_metrics[endpoint]
            metrics.total_requests += 1
            metrics.total_response_time += response_time
            metrics.average_response_time = (
                metrics.total_response_time / metrics.total_requests
            )
            metrics.last_accessed = datetime.now()

            if self.enable_detailed_tracking:
                metrics.response_times.append(response_time)

            # Track errors
            if status_code >= 400:
                metrics.error_count += 1
                self.recent_errors.append(
                    {
                        "endpoint": endpoint,
                        "status_code": status_code,
                        "timestamp": datetime.now().isoformat(),
                        "response_time": response_time,
                    }
                )

            metrics.error_rate = metrics.error_count / metrics.total_requests

            # Track recent activity
            self.recent_requests.append(
                {
                    "endpoint": endpoint,
                    "timestamp": datetime.now().isoformat(),
                    "response_time": response_time,
                    "status_code": status_code,
                }
            )

            # Update system metrics
            self.system_metrics.total_requests += 1

            # Track usage patterns
            now = datetime.now()
            hour = now.hour
            day = now.strftime("%A")

            self.hourly_patterns[hour]["requests"] += 1
            self.daily_patterns[day]["requests"] += 1

    def update_system_metrics(
        self,
        active_connections: int = 0,
        memory_usage_mb: float = 0.0,
        cpu_usage_percent: float = 0.0,
    ) -> None:
        """Update instantaneous system metrics (external sampler hook)."""
        with self._lock:
            self.system_metrics.active_connections = active_connections
            self.system_metrics.peak_connections = max(
                self.system_metrics.peak_connections, active_connections
            )
            self.system_metrics.memory_usage_mb = memory_usage_mb
            self.system_metrics.cpu_usage_percent = cpu_usage_percent
            self.system_metrics.uptime_seconds = (
                datetime.now() - self.start_time
            ).total_seconds()

    def update_cache_size(self, cache_size: int, memory_usage_mb: float = 0.0) -> None:
        """Set current cache size and optional memory usage sample."""
        with self._lock:
            self.cache_metrics.cache_size = cache_size
            self.cache_metrics.memory_usage_mb = memory_usage_mb

    def get_performance_summary(self) -> Dict[str, Any]:
        """Return consolidated multi-domain performance snapshot.

        Returns:
            Dict[str, Any]: Nested structure containing cache, api, system, error and
            usage pattern sections.
        """
        with self._lock:
            # Top endpoints by request count
            top_endpoints = sorted(
                self.endpoint_metrics.items(),
                key=lambda x: x[1].total_requests,
                reverse=True,
            )[:10]

            # Slowest endpoints by average response time
            slowest_endpoints = sorted(
                [
                    (k, v)
                    for k, v in self.endpoint_metrics.items()
                    if v.total_requests > 0
                ],
                key=lambda x: x[1].average_response_time,
                reverse=True,
            )[:5]

            # Recent error summary
            recent_errors_summary = {}
            for error in list(self.recent_errors)[-20:]:  # Last 20 errors
                status = error["status_code"]
                if status not in recent_errors_summary:
                    recent_errors_summary[status] = 0
                recent_errors_summary[status] += 1

            return {
                "timestamp": datetime.now().isoformat(),
                "uptime_hours": round(self.system_metrics.uptime_seconds / 3600, 2),
                "cache": {
                    "hit_rate": round(self.cache_metrics.hit_rate * 100, 2),
                    "total_requests": self.cache_metrics.total_requests,
                    "hits": self.cache_metrics.hits,
                    "misses": self.cache_metrics.misses,
                    "evictions": self.cache_metrics.evictions,
                    "average_response_time_ms": round(
                        self.cache_metrics.average_response_time * 1000, 2
                    ),
                    "cache_size": self.cache_metrics.cache_size,
                    "memory_usage_mb": round(self.cache_metrics.memory_usage_mb, 2),
                },
                "api": {
                    "total_requests": self.system_metrics.total_requests,
                    "active_connections": self.system_metrics.active_connections,
                    "peak_connections": self.system_metrics.peak_connections,
                    "top_endpoints": [
                        {
                            "endpoint": endpoint,
                            "requests": metrics.total_requests,
                            "avg_response_time_ms": round(
                                metrics.average_response_time * 1000, 2
                            ),
                            "error_rate": round(metrics.error_rate * 100, 2),
                        }
                        for endpoint, metrics in top_endpoints
                    ],
                    "slowest_endpoints": [
                        {
                            "endpoint": endpoint,
                            "avg_response_time_ms": round(
                                metrics.average_response_time * 1000, 2
                            ),
                            "total_requests": metrics.total_requests,
                        }
                        for endpoint, metrics in slowest_endpoints
                    ],
                },
                "system": {
                    "memory_usage_mb": round(self.system_metrics.memory_usage_mb, 2),
                    "cpu_usage_percent": round(
                        self.system_metrics.cpu_usage_percent, 2
                    ),
                },
                "errors": {
                    "recent_errors_by_status": recent_errors_summary,
                    "total_recent_errors": len(self.recent_errors),
                },
                "usage_patterns": {
                    "busiest_hours": sorted(
                        self.hourly_patterns.items(),
                        key=lambda x: x[1]["requests"],
                        reverse=True,
                    )[:5],
                    "busiest_days": sorted(
                        self.daily_patterns.items(),
                        key=lambda x: x[1]["requests"],
                        reverse=True,
                    )[:3],
                },
            }

    def get_cache_analytics(self) -> Dict[str, Any]:
        """Return detailed cache analytics and recommendations.

        Returns:
            Dict[str, Any]: Performance, usage, memory and recommendations subsections.
        """
        with self._lock:
            return {
                "performance": {
                    "hit_rate_percent": round(self.cache_metrics.hit_rate * 100, 2),
                    "miss_rate_percent": round(
                        (1 - self.cache_metrics.hit_rate) * 100, 2
                    ),
                    "average_response_time_ms": round(
                        self.cache_metrics.average_response_time * 1000, 2
                    ),
                    "cache_efficiency": (
                        "excellent"
                        if self.cache_metrics.hit_rate > 0.9
                        else (
                            "good"
                            if self.cache_metrics.hit_rate > 0.8
                            else "fair" if self.cache_metrics.hit_rate > 0.6 else "poor"
                        )
                    ),
                },
                "usage": {
                    "total_requests": self.cache_metrics.total_requests,
                    "cache_hits": self.cache_metrics.hits,
                    "cache_misses": self.cache_metrics.misses,
                    "evictions": self.cache_metrics.evictions,
                },
                "memory": {
                    "cache_size_entries": self.cache_metrics.cache_size,
                    "memory_usage_mb": round(self.cache_metrics.memory_usage_mb, 2),
                    "memory_per_entry_kb": round(
                        (self.cache_metrics.memory_usage_mb * 1024)
                        / max(self.cache_metrics.cache_size, 1),
                        2,
                    ),
                },
                "recommendations": self._get_cache_recommendations(),
            }

    def _get_cache_recommendations(self) -> List[str]:
        """Generate context-aware cache tuning suggestions."""
        recommendations = []

        if self.cache_metrics.hit_rate < 0.8:
            recommendations.append(
                "Cache hit rate is below 80%. Consider increasing TTL or cache size."
            )

        if self.cache_metrics.evictions > self.cache_metrics.hits * 0.1:
            recommendations.append(
                "High eviction rate detected. Consider increasing cache memory allocation."
            )

        if self.cache_metrics.average_response_time > 0.010:  # 10ms
            recommendations.append(
                "Average response time is above 10ms. Check cache performance."
            )

        if self.cache_metrics.memory_usage_mb > 100:
            recommendations.append(
                "Cache memory usage is high. Monitor for memory leaks."
            )

        if not recommendations:
            recommendations.append(
                "Cache performance is optimal. No changes recommended."
            )

        return recommendations

    def export_metrics(self, file_path: Path) -> None:
        """Persist a structured metrics dump to disk.

        Args:
            file_path: Destination path for JSON output.
        """
        metrics_data = {
            "export_time": datetime.now().isoformat(),
            "summary": self.get_performance_summary(),
            "cache_analytics": self.get_cache_analytics(),
            "detailed_endpoints": {
                endpoint: {
                    "total_requests": metrics.total_requests,
                    "average_response_time": metrics.average_response_time,
                    "error_count": metrics.error_count,
                    "error_rate": metrics.error_rate,
                    "last_accessed": (
                        metrics.last_accessed.isoformat()
                        if metrics.last_accessed
                        else None
                    ),
                }
                for endpoint, metrics in self.endpoint_metrics.items()
            },
        }

        with open(file_path, "w") as f:
            json.dump(metrics_data, f, indent=2, default=str)

    def reset_metrics(self) -> None:
        """Reset all counters/state (primarily for tests or manual re-baselining)."""
        with self._lock:
            self.cache_metrics = CacheMetrics()
            self.endpoint_metrics.clear()
            self.system_metrics = SystemMetrics()
            self.recent_requests.clear()
            self.recent_errors.clear()
            self.hourly_patterns.clear()
            self.daily_patterns.clear()
            self.start_time = datetime.now()


# Global performance monitor instance
_monitor: Optional[PerformanceMonitor] = None


def get_monitor() -> PerformanceMonitor:
    """Return (and lazily initialize) process-wide performance monitor singleton."""
    global _monitor
    if _monitor is None:
        _monitor = PerformanceMonitor()
    return _monitor


def initialize_monitor(enable_detailed_tracking: bool = True) -> PerformanceMonitor:
    """Force initialization / re-initialization of the global monitor.

    Args:
        enable_detailed_tracking: Whether to maintain the rolling latency deque.
    """
    global _monitor
    _monitor = PerformanceMonitor(enable_detailed_tracking)
    return _monitor
