"""
payment-service — APO Workshop Lab 1 target service.

Endpoints
---------
GET  /checkout      Normal checkout response
POST /toggle_fault  Toggle fault injection (high latency + errors)
GET  /metrics       Prometheus metrics in text format (RED pattern)
GET  /health        Health check

Run locally:
    pip install fastapi uvicorn prometheus-client
    uvicorn app:app --port 8080

Deploy to k3d:
    docker build -t payment-service:lab .
    k3d image import payment-service:lab -c apo-lab
    kubectl apply -f deployment.yaml
"""

import time
import random
import threading
from fastapi import FastAPI, Response
from prometheus_client import (
    Counter, Histogram, Gauge,
    generate_latest, CONTENT_TYPE_LATEST
)

app = FastAPI(title="payment-service")

# ── Prometheus metrics (RED pattern) ────────────────────────────────────────
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["endpoint"],
    buckets=[.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10]
)
ERROR_RATE = Gauge(
    "http_error_rate",
    "Current 1-minute error rate (fraction 0–1)"
)
MEMORY_USAGE = Gauge(
    "process_memory_usage_ratio",
    "Memory usage as a fraction of container limit"
)
RESTART_COUNT = Counter(
    "container_restart_total",
    "Number of container restarts"
)

# ── Fault injection state ────────────────────────────────────────────────────
_fault_active = False
_fault_lock   = threading.Lock()

# Track recent errors for error_rate gauge
_recent_requests: list[bool] = []   # True = error
_recent_lock = threading.Lock()


def _record(is_error: bool) -> None:
    with _recent_lock:
        _recent_requests.append(is_error)
        if len(_recent_requests) > 100:
            _recent_requests.pop(0)
        errors = sum(_recent_requests)
        ERROR_RATE.set(errors / len(_recent_requests))


@app.get("/checkout")
async def checkout():
    start = time.time()
    with _fault_lock:
        fault = _fault_active

    if fault:
        # Simulate memory pressure + latency spike
        MEMORY_USAGE.set(0.91)
        # 15–25% chance of error
        if random.random() < 0.20:
            _record(True)
            REQUEST_COUNT.labels("GET", "/checkout", "500").inc()
            REQUEST_LATENCY.labels("/checkout").observe(time.time() - start)
            return Response(
                content='{"error":"payment processing failed"}',
                status_code=500,
                media_type="application/json"
            )
        # Inject latency: 300–600ms
        time.sleep(random.uniform(0.30, 0.60))
    else:
        MEMORY_USAGE.set(random.uniform(0.40, 0.55))
        time.sleep(random.uniform(0.005, 0.015))  # 5–15ms normal latency

    _record(False)
    REQUEST_COUNT.labels("GET", "/checkout", "200").inc()
    REQUEST_LATENCY.labels("/checkout").observe(time.time() - start)
    return {"status": "ok", "order_id": f"ord-{int(time.time() * 1000)}"}


@app.post("/toggle_fault")
async def toggle_fault():
    global _fault_active
    with _fault_lock:
        _fault_active = not _fault_active
        state = _fault_active
    return {"fault_active": state, "message": f"Fault injection {'ENABLED' if state else 'DISABLED'}"}


@app.get("/metrics")
async def metrics():
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.get("/health")
async def health():
    with _fault_lock:
        fault = _fault_active
    if fault and random.random() < 0.30:
        return Response(content='{"status":"degraded"}', status_code=503, media_type="application/json")
    return {"status": "healthy"}
