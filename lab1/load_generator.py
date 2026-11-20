#!/usr/bin/env python3
"""
load_generator.py — APO Workshop Lab 1
=======================================
Generates realistic HTTP traffic against payment-service so that
rate-based Prometheus metrics (error_rate, p99_latency) are non-zero.

Run automatically by setup.sh. You can also run it manually:

    python3 load_generator.py                          # default: 5 req/s
    python3 load_generator.py --rate 10                # 10 req/s
    python3 load_generator.py --url http://localhost:8080
    python3 load_generator.py --fault   # toggle fault on then generate traffic

Stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime


PAYMENT_SERVICE_URL = "http://localhost:8080"

# Realistic checkout payloads — varies the request so metrics reflect real variance
CHECKOUT_PAYLOADS = [
    {"order_id": "ord-001", "amount": 49.99,  "currency": "USD", "items": 1},
    {"order_id": "ord-002", "amount": 149.50, "currency": "USD", "items": 3},
    {"order_id": "ord-003", "amount": 9.99,   "currency": "USD", "items": 1},
    {"order_id": "ord-004", "amount": 299.00, "currency": "USD", "items": 5},
    {"order_id": "ord-005", "amount": 74.95,  "currency": "USD", "items": 2},
]


def _post(url: str, payload: dict) -> tuple[int, float]:
    """Send a checkout request; return (status_code, latency_ms)."""
    # >>>>> MY CODE START — bug fix: payment-service only serves GET /checkout
    #       (app.py: @app.get("/checkout")). The instructor's POST got 405 on every
    #       request, so no checkout metrics were ever recorded and the fault never fired.
    #       The app ignores the body, so the payload is no longer sent.
    #       Instructor's original:
    #           data = json.dumps(payload).encode()
    #           req  = urllib.request.Request(url, data=data,
    #                      headers={"Content-Type": "application/json"}, method="POST")
    req = urllib.request.Request(url, method="GET")
    # <<<<< MY CODE END
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    except Exception:
        status = 0
    latency_ms = (time.monotonic() - t0) * 1000
    return status, latency_ms


def _toggle_fault(base_url: str) -> bool:
    """POST /toggle_fault; return True on success."""
    req = urllib.request.Request(
        f"{base_url}/toggle_fault",
        data=b"",
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def _health(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def run(
    base_url: str = PAYMENT_SERVICE_URL,
    rate_per_second: float = 5.0,
    inject_fault: bool = False,
    quiet: bool = False,
) -> None:
    interval = 1.0 / max(rate_per_second, 0.1)

    # Wait for service to be reachable (up to 60 s)
    if not quiet:
        print(f"[load_generator] Connecting to {base_url} ...", flush=True)
    deadline = time.monotonic() + 60
    while not _health(base_url):
        if time.monotonic() > deadline:
            print("[load_generator] ERROR: payment-service not reachable after 60 s", flush=True)
            sys.exit(1)
        time.sleep(2)

    if inject_fault:
        ok = _toggle_fault(base_url)
        if not quiet:
            status = "ON" if ok else "FAILED"
            print(f"[load_generator] Fault injection toggled {status}", flush=True)

    total = ok_count = err_count = 0
    start = time.monotonic()

    if not quiet:
        print(
            f"[load_generator] Sending {rate_per_second} req/s to {base_url}/checkout "
            f"— press Ctrl-C to stop",
            flush=True,
        )

    try:
        while True:
            payload = random.choice(CHECKOUT_PAYLOADS).copy()
            payload["order_id"] = f"ord-{random.randint(1000, 9999)}"
            payload["amount"]   = round(random.uniform(5.0, 500.0), 2)

            status, latency = _post(f"{base_url}/checkout", payload)
            total += 1
            if 200 <= status < 300:
                ok_count += 1
            else:
                err_count += 1

            if not quiet and total % 50 == 0:
                elapsed = time.monotonic() - start
                rps     = total / elapsed
                err_pct = 100 * err_count / total
                ts      = datetime.now().strftime("%H:%M:%S")
                print(
                    f"[{ts}] {total} requests  "
                    f"{rps:.1f} req/s  "
                    f"{err_pct:.1f}% errors  "
                    f"last={latency:.0f}ms",
                    flush=True,
                )

            # Sleep the remainder of the interval
            time.sleep(max(0, interval - latency / 1000))

    except KeyboardInterrupt:
        elapsed = time.monotonic() - start
        print(
            f"\n[load_generator] Stopped. "
            f"{total} requests in {elapsed:.1f}s, "
            f"{err_count} errors ({100*err_count/max(total,1):.1f}%)",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="APO Lab 1 — load generator")
    parser.add_argument("--url",   default=PAYMENT_SERVICE_URL, help="payment-service base URL")
    parser.add_argument("--rate",  type=float, default=5.0,     help="requests per second (default 5)")
    parser.add_argument("--fault", action="store_true",          help="toggle fault injection before sending traffic")
    parser.add_argument("--quiet", action="store_true",          help="suppress periodic status lines")
    args = parser.parse_args()

    run(
        base_url=args.url,
        rate_per_second=args.rate,
        inject_fault=args.fault,
        quiet=args.quiet,
    )
