"""
Lab 2 — autonomous_agent.py  (SOLUTION)
=========================================
Complete autonomous single-agent remediation loop.
Do not share with students before the lab exercise.
"""

from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

_this = Path(__file__).resolve().parent
for _p in [_this.parent.parent / "lab1" / "solution",   # running from lab2/solution/
            _this.parent / "lab1" / "solution"]:          # running from lab2/ (if copied)
    if _p.exists():
        sys.path.insert(0, str(_p))
        break

from gate import GateOrchestrator, GateConfig, ActionProposal, GateResult
from diagnostic_agent import (
    PrometheusClient, MetricsReader, ConfidenceScorer,
    detect_symptom_class, symptom_to_action, autonomy_level,
    SIMULATED_METRICS,
)

AUDIT_LOG_PATH = Path("apo_audit.jsonl")

SIMULATED_POST_RESTART = {
    "memory_usage_ratio":    0.42,
    "error_rate_ratio":      0.004,
    "p99_latency_ms":        55,
    "p99_baseline_ms":       45,
    "restart_count_1h":      0,
    "last_scrape_age_seconds": 14,
}


def load_action_catalog(path: str = "action_catalog.yaml") -> dict[str, dict]:
    catalog_path = Path(__file__).parent.parent / path
    if not catalog_path.exists():
        catalog_path = Path(path)
    if not catalog_path.exists():
        return {}
    if not HAS_YAML:
        return {}
    with open(catalog_path) as f:
        raw = yaml.safe_load(f)
    return {a["action_id"]: a for a in raw.get("actions", [])}


class ActionExecutor:
    COMMANDS: dict[str, list[str]] = {}  # filled in execute()

    def __init__(self, dry_run: bool = False, simulate: bool = False):
        self.dry_run  = dry_run
        self.simulate = simulate

    def execute(self, action_id: str, deployment: str, namespace: str) -> dict:
        started_at = time.monotonic()
        ts = datetime.now(timezone.utc).isoformat()

        if self.simulate:
            elapsed = int((time.monotonic() - started_at) * 1000)
            return {
                "action_id":   action_id,
                "deployment":  deployment,
                "namespace":   namespace,
                "executed_at": ts,
                "dry_run":     False,
                "simulate":    True,
                "success":     True,
                "output":      f"[SIMULATE] kubectl rollout restart deployment/{deployment} -n {namespace}",
                "error":       "",
                "duration_ms": elapsed,
            }

        commands: dict[str, list[str]] = {
            "pod.rolling_restart": [
                "kubectl", "rollout", "restart",
                f"deployment/{deployment}", "-n", namespace,
            ],
            "db.connection_reset": [
                "kubectl", "rollout", "restart",
                f"deployment/{deployment}", "-n", namespace,
            ],
            "config.rollback": [
                "kubectl", "rollout", "undo",
                f"deployment/{deployment}", "-n", namespace,
            ],
            "scale.horizontal_add": [
                "kubectl", "scale", f"deployment/{deployment}",
                "--replicas=3", "-n", namespace,
            ],
        }

        cmd = commands.get(action_id)
        if cmd is None:
            return {
                "action_id":   action_id,
                "deployment":  deployment,
                "namespace":   namespace,
                "executed_at": ts,
                "dry_run":     self.dry_run,
                "simulate":    False,
                "success":     False,
                "output":      "",
                "error":       f"No command mapping for action_id={action_id}",
                "duration_ms": 0,
            }

        if self.dry_run:
            elapsed = int((time.monotonic() - started_at) * 1000)
            return {
                "action_id":   action_id,
                "deployment":  deployment,
                "namespace":   namespace,
                "executed_at": ts,
                "dry_run":     True,
                "simulate":    False,
                "success":     True,
                "output":      f"[DRY RUN] Would execute: {' '.join(cmd)}",
                "error":       "",
                "duration_ms": elapsed,
            }

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30
            )
            elapsed = int((time.monotonic() - started_at) * 1000)
            return {
                "action_id":   action_id,
                "deployment":  deployment,
                "namespace":   namespace,
                "executed_at": ts,
                "dry_run":     False,
                "simulate":    False,
                "success":     proc.returncode == 0,
                "output":      proc.stdout.strip(),
                "error":       proc.stderr.strip(),
                "duration_ms": elapsed,
            }
        except subprocess.TimeoutExpired:
            return {
                "action_id":   action_id,
                "deployment":  deployment,
                "namespace":   namespace,
                "executed_at": ts,
                "dry_run":     False,
                "simulate":    False,
                "success":     False,
                "output":      "",
                "error":       "kubectl timed out after 30s",
                "duration_ms": 30000,
            }


class VerificationLoop:
    def __init__(
        self,
        client: PrometheusClient | None,
        simulate: bool = False,
        poll_interval_seconds: int = 10,
        timeout_seconds: int = 120,
    ):
        self.client        = client
        self.simulate      = simulate
        self.poll_interval = poll_interval_seconds
        self.timeout       = timeout_seconds

    def wait_for_recovery(
        self,
        deployment: str,
        baseline_p90_ms: float,
    ) -> dict:
        threshold_ms = baseline_p90_ms * 1.5
        start        = time.monotonic()
        polls        = 0
        final_p90    = 0.0

        while True:
            elapsed = time.monotonic() - start

            if self.simulate:
                # Simulate: returns healthy after first poll interval
                if elapsed >= self.poll_interval:
                    final_p90 = SIMULATED_POST_RESTART["p99_latency_ms"]
                else:
                    final_p90 = 350.0  # still recovering
            else:
                promql = (
                    f'histogram_quantile(0.90,'
                    f'  rate(http_request_duration_seconds_bucket'
                    f'{{deployment="{deployment}"}}[1m])'
                    f') * 1000'
                )
                result   = self.client.query(promql) if self.client else None
                final_p90 = result if result is not None else threshold_ms + 1

            polls += 1

            if final_p90 < threshold_ms:
                return {
                    "recovered":        True,
                    "final_p90_ms":     round(final_p90, 1),
                    "threshold_ms":     round(threshold_ms, 1),
                    "elapsed_seconds":  round(elapsed, 1),
                    "polls":            polls,
                }

            if elapsed >= self.timeout:
                return {
                    "recovered":        False,
                    "final_p90_ms":     round(final_p90, 1),
                    "threshold_ms":     round(threshold_ms, 1),
                    "elapsed_seconds":  round(elapsed, 1),
                    "polls":            polls,
                }

            time.sleep(self.poll_interval)


class AuditLogger:
    def __init__(self, path: Path = AUDIT_LOG_PATH):
        self.path = path

    def append(self, record: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")


def run(prometheus_url: str, simulate: bool, dry_run: bool) -> None:
    deployment = "payment-service"
    namespace  = "default"
    run_id     = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print("\n" + "=" * 65)
    print("LAB 2 — AUTONOMOUS REMEDIATION AGENT  (SOLUTION)")
    print("=" * 65)
    print(f"Run ID: {run_id}")

    catalog = load_action_catalog()
    print(f"\nAction catalog: {len(catalog)} actions loaded")

    if simulate:
        print("\n[SIMULATE] MEMORY_PRESSURE scenario")
        metrics = SIMULATED_METRICS
        client  = None
    else:
        if not HAS_REQUESTS:
            print("ERROR: requests not installed")
            sys.exit(1)
        client  = PrometheusClient(prometheus_url)
        reader  = MetricsReader(client, deployment, namespace)
        metrics = reader.read()

    print(f"\nMetrics:")
    for k, v in metrics.items():
        print(f"  {k:<35} {v}")

    symptom_class = detect_symptom_class(metrics)
    action_id     = symptom_to_action(symptom_class)
    scorer        = ConfidenceScorer()
    scores        = scorer.compute(metrics, symptom_class, action_id)

    print(f"\nDiagnosis:  {symptom_class}  →  {action_id}")
    print(f"Confidence: C={scores['C']:.4f}  ({autonomy_level(scores['C'])})")

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
    print(f"\nGate: {gate_result}")

    logger = AuditLogger()

    if not gate_result.approved:
        print("\n🚫  BLOCKED")
        logger.append(_build_record(run_id, deployment, namespace, symptom_class,
                                    action_id, scores, gate_result,
                                    None, None, "GATE_BLOCKED"))
        return

    if scores["C"] < 0.60:
        print(f"\n🔺  ESCALATE — C={scores['C']:.4f}")
        logger.append(_build_record(run_id, deployment, namespace, symptom_class,
                                    action_id, scores, gate_result,
                                    None, None, "ESCALATED"))
        return

    executor    = ActionExecutor(dry_run=dry_run, simulate=simulate)
    exec_result = executor.execute(action_id, deployment, namespace)

    label = "[DRY RUN] " if dry_run else ("[SIMULATE] " if simulate else "")
    if exec_result["success"]:
        print(f"\n✅  {label}Executed {action_id} in {exec_result['duration_ms']}ms")
    else:
        print(f"\n❌  Execution FAILED: {exec_result['error']}")
        logger.append(_build_record(run_id, deployment, namespace, symptom_class,
                                    action_id, scores, gate_result,
                                    exec_result, None, "EXECUTION_FAILED"))
        return

    baseline_p90 = metrics.get("p99_baseline_ms", 50.0)
    verifier     = VerificationLoop(client=client, simulate=simulate)
    print(f"\nVerifying recovery (threshold: P90 < {baseline_p90 * 1.5:.0f}ms) …")

    verify_result = verifier.wait_for_recovery(deployment, baseline_p90)

    if verify_result["recovered"]:
        print(f"✅  Recovered in {verify_result['elapsed_seconds']:.0f}s "
              f"— P90={verify_result['final_p90_ms']:.0f}ms")
        outcome = "RECOVERED"
    else:
        print(f"⚠️  NOT recovered after {verifier.timeout}s")
        outcome = "ESCALATED"

    logger.append(_build_record(run_id, deployment, namespace, symptom_class,
                                action_id, scores, gate_result,
                                exec_result, verify_result, outcome))
    print(f"\nAudit written to {AUDIT_LOG_PATH}")
    print()


def _build_record(run_id, deployment, namespace, symptom_class, action_id,
                  scores, gate_result, exec_result, verify_result, outcome):
    return {
        "run_id":          run_id,
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "deployment":      deployment,
        "namespace":       namespace,
        "symptom_class":   symptom_class,
        "action_id":       action_id,
        "confidence":      scores,
        "gate_approved":   gate_result.approved,
        "gate_blocked_by": gate_result.blocked_by,
        "execution":       exec_result,
        "verification":    verify_result,
        "outcome":         outcome,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate",   action="store_true")
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    args = parser.parse_args()
    run(args.prometheus_url, args.simulate, args.dry_run)
