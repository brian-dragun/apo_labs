"""
Mock telemetry data for APO Workshop labs.

Simulates Prometheus metrics, Loki log excerpts, Jaeger trace data,
and Kubernetes events for five symptom classes.  All values are
deterministic so labs are reproducible.
"""

from dataclasses import dataclass, field
from typing import Any

# ── Symptom classes ──────────────────────────────────────────────────────────
SYMPTOM_CLASSES = [
    "MEMORY_PRESSURE",
    "DB_BOTTLENECK",
    "DEPLOYMENT_REGRESSION",
    "CAPACITY_EXHAUSTION",
    "DEPENDENCY_FAILURE",
]


@dataclass
class TelemetrySnapshot:
    """Everything APO sees at the moment it evaluates an incident."""
    incident_id: str
    symptom_class: str
    deployment: str
    namespace: str
    # Prometheus RED metrics
    metrics: dict[str, Any]
    # Loki structured log excerpts  (list of JSON-like dicts)
    logs: list[dict[str, Any]]
    # Jaeger / Tempo trace summary
    traces: dict[str, Any]
    # Kubernetes events
    k8s_events: list[dict[str, Any]]


# ── Per-symptom mock data ────────────────────────────────────────────────────

_SNAPSHOTS: dict[str, TelemetrySnapshot] = {

    "MEMORY_PRESSURE": TelemetrySnapshot(
        incident_id="inc-mem-001",
        symptom_class="MEMORY_PRESSURE",
        deployment="api-server",
        namespace="production",
        metrics={
            "memory_usage_ratio": 0.91,          # 91 % of limit
            "memory_working_set_bytes": 1_879_048_192,
            "memory_limit_bytes": 2_147_483_648,  # 2 GiB
            "oomkill_count_1h": 2,
            "restart_count": 3,
            "cpu_usage_ratio": 0.42,
            "request_rate_rps": 847,
            "error_rate_ratio": 0.004,
            "p99_latency_ms": 312,
        },
        logs=[
            {"ts": "2026-11-13T09:14:31Z", "level": "WARN",
             "msg": "Memory at 89% of limit", "deployment": "api-server"},
            {"ts": "2026-11-13T09:14:33Z", "level": "ERROR",
             "msg": "OOMKilled: container api-server in pod api-server-7d8f9b-xkq2p",
             "reason": "OOMKilled"},
            {"ts": "2026-11-13T09:14:35Z", "level": "WARN",
             "msg": "Memory now 91% — approaching limit"},
        ],
        traces={
            "p99_latency_ms": 312,
            "slow_span": "db.query",
            "slow_span_ms": 198,
            "error_fraction": 0.004,
        },
        k8s_events=[
            {"reason": "OOMKilling",   "count": 2, "last_seen": "9s ago",
             "message": "Memory limit exceeded"},
            {"reason": "BackOff",      "count": 1, "last_seen": "2s ago",
             "message": "Back-off restarting failed container"},
        ],
    ),

    "DB_BOTTLENECK": TelemetrySnapshot(
        incident_id="inc-db-001",
        symptom_class="DB_BOTTLENECK",
        deployment="payment-service",
        namespace="production",
        metrics={
            "db_query_latency_p99_ms": 2_340,
            "db_connection_pool_active": 98,
            "db_connection_pool_max": 100,
            "db_deadlock_count_5m": 7,
            "request_rate_rps": 230,
            "error_rate_ratio": 0.072,
            "p99_latency_ms": 2_890,
            "cpu_usage_ratio": 0.28,
            "memory_usage_ratio": 0.55,
        },
        logs=[
            {"ts": "2026-11-13T10:02:11Z", "level": "ERROR",
             "msg": "Deadlock detected on table payments", "query_ms": 2_340},
            {"ts": "2026-11-13T10:02:14Z", "level": "WARN",
             "msg": "Connection pool 98/100 — near exhaustion"},
            {"ts": "2026-11-13T10:02:17Z", "level": "ERROR",
             "msg": "Transaction timeout after 2000ms"},
        ],
        traces={
            "p99_latency_ms": 2_890,
            "slow_span": "db.transaction",
            "slow_span_ms": 2_340,
            "error_fraction": 0.072,
        },
        k8s_events=[
            {"reason": "Unhealthy", "count": 5, "last_seen": "10s ago",
             "message": "Readiness probe failed: connection refused"},
        ],
    ),

    "DEPLOYMENT_REGRESSION": TelemetrySnapshot(
        incident_id="inc-dep-001",
        symptom_class="DEPLOYMENT_REGRESSION",
        deployment="checkout-service",
        namespace="production",
        metrics={
            "error_rate_ratio": 0.183,            # 18 % error rate
            "error_rate_before_deploy_ratio": 0.003,
            "deploy_timestamp_minutes_ago": 8,
            "request_rate_rps": 410,
            "p99_latency_ms": 1_240,
            "p99_latency_before_deploy_ms": 145,
            "cpu_usage_ratio": 0.61,
            "memory_usage_ratio": 0.68,
            "restart_count": 0,
        },
        logs=[
            {"ts": "2026-11-13T11:45:02Z", "level": "ERROR",
             "msg": "NullPointerException in CheckoutController.processOrder",
             "stack": "java.lang.NullPointerException at CheckoutController.java:142"},
            {"ts": "2026-11-13T11:45:04Z", "level": "ERROR",
             "msg": "HTTP 500 rate jumped from 0.3% to 18.3% after deploy v2.4.1"},
        ],
        traces={
            "p99_latency_ms": 1_240,
            "slow_span": "checkout.process_order",
            "slow_span_ms": 1_100,
            "error_fraction": 0.183,
        },
        k8s_events=[
            {"reason": "Pulled", "count": 1, "last_seen": "8m ago",
             "message": "Successfully pulled image checkout-service:v2.4.1"},
            {"reason": "Started", "count": 1, "last_seen": "8m ago",
             "message": "Started container checkout-service"},
        ],
    ),

    "CAPACITY_EXHAUSTION": TelemetrySnapshot(
        incident_id="inc-cap-001",
        symptom_class="CAPACITY_EXHAUSTION",
        deployment="recommendation-engine",
        namespace="production",
        metrics={
            "cpu_usage_ratio": 0.97,
            "cpu_throttle_ratio": 0.42,
            "request_rate_rps": 3_200,
            "request_rate_1h_ago_rps": 1_800,
            "replica_count": 3,
            "replica_max": 10,
            "p99_latency_ms": 4_100,
            "error_rate_ratio": 0.028,
            "memory_usage_ratio": 0.73,
        },
        logs=[
            {"ts": "2026-11-13T14:10:03Z", "level": "WARN",
             "msg": "CPU throttling: 42% of CPU cycles throttled"},
            {"ts": "2026-11-13T14:10:07Z", "level": "WARN",
             "msg": "Request queue depth: 1200 — exceeds burst capacity"},
        ],
        traces={
            "p99_latency_ms": 4_100,
            "slow_span": "recommendation.score_items",
            "slow_span_ms": 3_800,
            "error_fraction": 0.028,
        },
        k8s_events=[
            {"reason": "SuccessfulRescale", "count": 0, "last_seen": "never",
             "message": "HPA at max replicas — cannot scale further"},
        ],
    ),

    "DEPENDENCY_FAILURE": TelemetrySnapshot(
        incident_id="inc-dep-fail-001",
        symptom_class="DEPENDENCY_FAILURE",
        deployment="order-service",
        namespace="production",
        metrics={
            "upstream_error_rate_ratio": 0.94,    # inventory-service failing
            "upstream_service": "inventory-service",
            "circuit_breaker_state": "OPEN",
            "request_rate_rps": 520,
            "error_rate_ratio": 0.89,
            "p99_latency_ms": 10_050,             # timeouts
            "cpu_usage_ratio": 0.21,
            "memory_usage_ratio": 0.44,
            "timeout_count_5m": 234,
        },
        logs=[
            {"ts": "2026-11-13T16:30:01Z", "level": "ERROR",
             "msg": "Circuit breaker OPEN: inventory-service unreachable",
             "upstream": "inventory-service"},
            {"ts": "2026-11-13T16:30:03Z", "level": "ERROR",
             "msg": "Timeout after 10000ms calling inventory-service:8080"},
        ],
        traces={
            "p99_latency_ms": 10_050,
            "slow_span": "inventory.check_stock",
            "slow_span_ms": 10_001,
            "error_fraction": 0.89,
        },
        k8s_events=[
            {"reason": "NetworkNotReady", "count": 12, "last_seen": "1s ago",
             "message": "inventory-service pod NotReady"},
        ],
    ),
}


def get_snapshot(symptom_class: str) -> TelemetrySnapshot:
    """Return the mock telemetry snapshot for a given symptom class."""
    if symptom_class not in _SNAPSHOTS:
        raise ValueError(
            f"Unknown symptom class: {symptom_class}. "
            f"Valid values: {SYMPTOM_CLASSES}"
        )
    return _SNAPSHOTS[symptom_class]


def list_snapshots() -> list[TelemetrySnapshot]:
    """Return all snapshots, ordered by SYMPTOM_CLASSES."""
    return [_SNAPSHOTS[s] for s in SYMPTOM_CLASSES]
