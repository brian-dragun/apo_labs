"""
Lab 1 — diagnostic_agent.py  (SOLUTION)
=========================================
Complete implementation of the diagnostic agent.
Do not share with students before the lab exercise.
"""

# ─────────────────────────────────────────────────────────────────────────────
# HOW TO READ THIS FILE
#   Code between  >>>>> MY CODE ... START  and  <<<<< MY CODE ... END  markers
#   is my bug fix to the instructor's reference (MetricsReader.read() queries).
#   Lab 2 and Lab 3 import this file, so the fix applies to them too.
#   Original copy:  git show workshop-original:lab1/solution/diagnostic_agent.py
#   See every change:  git diff workshop-original -- lab1/solution/diagnostic_agent.py
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
import argparse
import sys
from datetime import datetime, timezone

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from gate import GateOrchestrator, GateConfig, ActionProposal, GateResult


SIMULATED_METRICS = {
    "memory_usage_ratio": 0.91,
    "error_rate_ratio": 0.038,
    "p99_latency_ms": 312,
    "p99_baseline_ms": 45,
    "restart_count_1h": 2,
    "last_scrape_age_seconds": 12,
}


class PrometheusClient:
    def __init__(self, base_url: str = "http://localhost:9090"):
        self.base_url = base_url.rstrip("/")

    def query(self, promql: str) -> float | None:
        endpoint = f"{self.base_url}/api/v1/query"
        params = {"query": promql}
        try:
            response = requests.get(endpoint, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            result = data["data"]["result"]
            if not result:
                return None
            return float(result[0]["value"][1])
        except Exception:
            return None


class MetricsReader:
    def __init__(self, client: PrometheusClient, deployment: str, namespace: str):
        self.client     = client
        self.deployment = deployment
        self.namespace  = namespace

    def read(self) -> dict[str, float]:
        d = self.deployment

        # >>>>> MY CODE START — bug fix: aggregate across all pods
        #       The deployment runs 2 pods, so each query returns one series per pod and
        #       query() keeps only result[0]. Lab 2 port-forwards to ONE pod, so that first
        #       series was often the idle pod (memory 0.0) and the fault was never seen.
        #       Instructor's original queries, for comparison:
        #         memory:   process_memory_usage_ratio{deployment="..."}
        #         errors:   rate(5xx[2m]) / rate(all[2m])      (per pod, NaN with no 5xx)
        #         p99:      histogram_quantile(0.99, rate(bucket[2m])) * 1000   (per pod)
        #         restarts: increase(container_restart_total[1h])               (per pod)
        #         freshness: time() - max(scrape_time{job="..."})  (metric doesn't exist,
        #                    so it always fell back to 15.0)
        memory = self.client.query(
            f'max(process_memory_usage_ratio{{deployment="{d}"}})'          # worst pod
        ) or 0.0

        error_rate = self.client.query(
            f'(sum(rate(http_requests_total{{deployment="{d}",status_code=~"5.."}}[2m]))'
            f' or vector(0))'                                               # no 5xx yet → 0
            f' / (sum(rate(http_requests_total{{deployment="{d}"}}[2m])) > 0)'  # no traffic → None, not NaN
        ) or 0.0

        p99 = self.client.query(
            f'histogram_quantile(0.99,'
            f'  sum by (le) (rate(http_request_duration_seconds_bucket{{deployment="{d}"}}[2m]))'
            f') * 1000 >= 0'                                                # ">= 0" drops NaN (no traffic)
        ) or 0.0

        restarts = self.client.query(
            f'sum(increase(container_restart_total{{deployment="{d}"}}[1h]))'
        ) or 0.0

        # Freshness: seconds since the newest successful scrape of any pod
        last_scrape = self.client.query(
            f'time() - max(timestamp(up{{job="{d}"}}) and (up{{job="{d}"}} == 1))'
        )
        if last_scrape is None:
            last_scrape = float("inf")   # no successful scrape → data is stale (F = 0.40)
        # <<<<< MY CODE END

        return {
            "memory_usage_ratio":      round(memory, 4),
            "error_rate_ratio":        round(error_rate, 4),
            "p99_latency_ms":          round(p99, 2),
            "p99_baseline_ms":         45.0,   # stored baseline
            "restart_count_1h":        round(restarts, 1),
            "last_scrape_age_seconds": round(last_scrape, 1),
        }


class ConfidenceScorer:
    HISTORICAL_MATCH = {
        ("MEMORY_PRESSURE",       "pod.rolling_restart"):  0.78,
        ("DB_BOTTLENECK",         "db.connection_reset"):  0.65,
        ("DEPLOYMENT_REGRESSION", "config.rollback"):      0.91,
        ("CAPACITY_EXHAUSTION",   "scale.horizontal_add"): 0.72,
        ("DEPENDENCY_FAILURE",    "circuit.reset"):        0.60,
    }

    REMEDIATION_CONFIDENCE = {
        "pod.rolling_restart":  0.90,
        "db.connection_reset":  0.75,
        "config.rollback":      0.95,
        "scale.horizontal_add": 0.80,
        "circuit.reset":        0.65,
    }

    def compute(
        self,
        metrics: dict[str, float],
        symptom_class: str,
        action_id: str,
    ) -> dict[str, float]:
        mem = metrics.get("memory_usage_ratio", 0.0)
        if mem > 0.90:
            S = 0.95
        elif mem > 0.85:
            S = 0.80
        elif mem > 0.75:
            S = 0.60
        else:
            S = 0.40

        H = self.HISTORICAL_MATCH.get((symptom_class, action_id), 0.50)
        R = self.REMEDIATION_CONFIDENCE.get(action_id, 0.50)

        age = metrics.get("last_scrape_age_seconds", 60.0)
        if age < 30:
            F = 0.95
        elif age < 60:
            F = 0.80
        elif age < 120:
            F = 0.65
        else:
            F = 0.40

        C = round(0.35 * S + 0.25 * H + 0.25 * R + 0.15 * F, 4)
        return {"S": S, "H": H, "R": R, "F": F, "C": C}


def detect_symptom_class(metrics: dict[str, float]) -> str:
    if metrics.get("memory_usage_ratio", 0) > 0.85:
        return "MEMORY_PRESSURE"
    if metrics.get("p99_latency_ms", 0) > 500 and metrics.get("error_rate_ratio", 0) > 0.05:
        return "DB_BOTTLENECK"
    if metrics.get("error_rate_ratio", 0) > 0.10:
        return "DEPLOYMENT_REGRESSION"
    if metrics.get("restart_count_1h", 0) > 5:
        return "CAPACITY_EXHAUSTION"
    return "UNKNOWN"


def symptom_to_action(symptom_class: str) -> str:
    return {
        "MEMORY_PRESSURE":       "pod.rolling_restart",
        "DB_BOTTLENECK":         "db.connection_reset",
        "DEPLOYMENT_REGRESSION": "config.rollback",
        "CAPACITY_EXHAUSTION":   "scale.horizontal_add",
    }.get(symptom_class, "escalate_human")


def autonomy_level(c: float) -> str:
    if c >= 0.90: return "L5 — act silently"
    if c >= 0.80: return "L4 — act + notify"
    if c >= 0.70: return "L3 — act + approval"
    if c >= 0.60: return "L2 — suggest"
    return          "L1 — escalate to human"


def run(prometheus_url: str, simulate: bool) -> None:
    deployment = "payment-service"
    namespace  = "default"

    print("\n" + "=" * 65)
    print("LAB 1 — DIAGNOSTIC AGENT  (SOLUTION)")
    print("=" * 65)

    if simulate:
        print("\n[SIMULATE] MEMORY_PRESSURE scenario")
        metrics = SIMULATED_METRICS
    else:
        if not HAS_REQUESTS:
            print("ERROR: 'requests' not installed.")
            sys.exit(1)
        client  = PrometheusClient(prometheus_url)
        reader  = MetricsReader(client, deployment, namespace)
        metrics = reader.read()

    print(f"\nMetrics for {deployment}:")
    for k, v in metrics.items():
        print(f"  {k:<35} {v}")

    symptom_class = detect_symptom_class(metrics)
    action_id     = symptom_to_action(symptom_class)
    print(f"\nSymptom class:   {symptom_class}")
    print(f"Proposed action: {action_id}")

    scorer = ConfidenceScorer()
    scores = scorer.compute(metrics, symptom_class, action_id)
    print(f"\nConfidence scores:")
    print(f"  S: {scores['S']:.2f}  H: {scores['H']:.2f}  "
          f"R: {scores['R']:.2f}  F: {scores['F']:.2f}")
    print(f"  C = {scores['C']:.4f}  →  {autonomy_level(scores['C'])}")

    proposal = ActionProposal(
        action_id=action_id,
        deployment=deployment,
        namespace=namespace,
        estimated_cost_per_hour_usd=50.0,
        blast_radius_fraction=0.05,
        reversal_time_seconds=120,
        symptom_class=symptom_class,
        confidence_score=scores["C"],
    )

    orchestrator = GateOrchestrator(GateConfig())
    gate_result  = orchestrator.evaluate(proposal)

    print(f"\n{gate_result}")
    for r in gate_result.reasons:
        print(f"  reason: {r}")

    print("\n" + "─" * 65)
    if gate_result.approved and scores["C"] >= 0.60:
        print(f"✅  DECISION: Execute {action_id} on {deployment}")
        print(f"   C={scores['C']:.4f} → {autonomy_level(scores['C'])}")
    elif not gate_result.approved:
        print(f"🚫  DECISION: Block — gate(s) failed: {gate_result.blocked_by}")
    else:
        print(f"🔺  DECISION: Escalate — C={scores['C']:.4f} below 0.60 threshold")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    args = parser.parse_args()
    run(args.prometheus_url, args.simulate)
