"""
Lab 2 — autonomous_agent.py  (STARTER)
========================================
Extend Lab 1 to build a fully autonomous single-agent remediation loop:
  detect → diagnose → gate → execute → verify → audit

New in Lab 2 vs Lab 1
----------------------
1. Load action catalog from action_catalog.yaml
2. Execute the approved action via kubectl rollout restart
3. Verification loop — poll P90 latency until it drops to 1.5× baseline
4. JSONL audit log — append one record per run (full telemetry → score → gate → outcome)

Run (simulate mode — no cluster needed):
    python autonomous_agent_starter.py --simulate

Run (real mode — requires k3d + Prometheus):
    kubectl port-forward -n monitoring svc/prometheus 9090:9090 &
    python autonomous_agent_starter.py --prometheus-url http://localhost:9090
"""

# ─────────────────────────────────────────────────────────────────────────────
# HOW TO READ THIS FILE
#   Code between  >>>>> MY CODE ... START  and  <<<<< MY CODE ... END  markers
#   is what I wrote for the lab. Everything else is the instructor's original.
#   Original copy:  git show workshop-original:lab2/autonomous_agent_starter.py
#   See every change:  git diff workshop-original -- lab2/autonomous_agent_starter.py
# ─────────────────────────────────────────────────────────────────────────────

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
        """
        Execute the action and return a result dict:
            {
              "action_id":    str,
              "deployment":   str,
              "namespace":    str,
              "executed_at":  ISO-8601 string,
              "dry_run":      bool,
              "simulate":     bool,
              "success":      bool,
              "output":       str,   # stdout / simulated message
              "error":        str,   # stderr / "" on success
              "duration_ms":  int,
            }

        For pod.rolling_restart: run
            kubectl rollout restart deployment/{deployment} -n {namespace}

        Steps:
        1. Record start time
        2. If self.simulate: return success with fake output, skip kubectl
        3. If self.dry_run:  return success with "[DRY RUN]" output, skip kubectl
        4. Build the command for the action_id (see COMMANDS below)
        5. Run with subprocess.run(..., capture_output=True, text=True, timeout=30)
        6. Record end time
        7. Return result dict

        COMMANDS = {
            "pod.rolling_restart": [
                "kubectl", "rollout", "restart",
                f"deployment/{deployment}", "-n", namespace
            ],
            "db.connection_reset": [
                "kubectl", "rollout", "restart",
                f"deployment/{deployment}", "-n", namespace
            ],
            "config.rollback": [
                "kubectl", "rollout", "undo",
                f"deployment/{deployment}", "-n", namespace
            ],
        }
        """
        # >>>>> MY CODE START — Lab 2 Step 3: ActionExecutor.execute()
        #       (replaced the instructor's "# TODO: implement this method" stub)
        start       = time.monotonic()
        executed_at = datetime.now(timezone.utc).isoformat()

        def result(success: bool, output: str, error: str = "") -> dict:
            # One place builds the 10-key dict, so all four exits return the same shape
            return {
                "action_id":   action_id,
                "deployment":  deployment,
                "namespace":   namespace,
                "executed_at": executed_at,
                "dry_run":     self.dry_run and not self.simulate,
                "simulate":    self.simulate,
                "success":     success,
                "output":      output,
                "error":       error,
                "duration_ms": int((time.monotonic() - start) * 1000),
            }

        if self.simulate:
            return result(True, f"[SIMULATE] {action_id} on deployment/{deployment} -n {namespace}")

        COMMANDS = {
            "pod.rolling_restart": ["kubectl", "rollout", "restart", f"deployment/{deployment}", "-n", namespace],
            "db.connection_reset": ["kubectl", "rollout", "restart", f"deployment/{deployment}", "-n", namespace],
            "config.rollback":     ["kubectl", "rollout", "undo",    f"deployment/{deployment}", "-n", namespace],
        }
        cmd = COMMANDS.get(action_id)
        if cmd is None:
            # Beyond the README spec: an unknown action fails cleanly instead of a KeyError
            return result(False, "", f"no command mapped for action '{action_id}'")

        if self.dry_run:
            return result(True, "[DRY RUN] " + " ".join(cmd))

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            # Beyond the README spec: kubectl missing or hung → failed result, run still gets audited
            return result(False, "", f"{type(exc).__name__}: {exc}")
        return result(proc.returncode == 0, proc.stdout.strip(), proc.stderr.strip())
        # <<<<< MY CODE END
        # Instructor's original stub below never runs now (my code returns above).
        raise NotImplementedError("Implement ActionExecutor.execute()")


# ── TODO: Implement VerificationLoop ─────────────────────────────────────────


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
        """
        Poll until P90 latency < 1.5× baseline OR timeout.

        Returns:
            {
              "recovered":           bool,
              "final_p90_ms":        float,
              "threshold_ms":        float,   # 1.5 × baseline
              "elapsed_seconds":     float,
              "polls":               int,
            }

        Polling logic:
        1. Record start time
        2. Loop until elapsed >= timeout_seconds:
           a. If simulate: use SIMULATED_POST_RESTART["p99_latency_ms"] as p90
           b. Else: query Prometheus for
              histogram_quantile(0.90,
                rate(http_request_duration_seconds_bucket{deployment="..."}[1m])
              ) * 1000
           c. If p90 < threshold: return recovered=True immediately
           d. Sleep poll_interval seconds
        3. If loop exits without recovery: return recovered=False with final values

        Hint: threshold_ms = baseline_p90_ms * 1.5
        """
        # >>>>> MY CODE START — Lab 2 Step 4: VerificationLoop.wait_for_recovery()
        #       (replaced the instructor's "# TODO: implement this method" stub)
        threshold_ms = baseline_p90_ms * 1.5

        def result(recovered: bool, p90: float | None, elapsed: float, polls: int) -> dict:
            return {
                "recovered":       recovered,
                "final_p90_ms":    p90,     # None = Prometheus never returned a value
                "threshold_ms":    threshold_ms,
                "elapsed_seconds": round(elapsed, 1),
                "polls":           polls,
            }

        # Part A — simulate: one poll against the canned post-restart metrics
        if self.simulate:
            time.sleep(self.poll_interval)
            p90 = SIMULATED_POST_RESTART["p99_latency_ms"]
            return result(p90 < threshold_ms, p90, float(self.poll_interval), 1)

        # Part B — real: poll Prometheus until P90 < threshold or timeout.
        # Beyond the README query: sum by (le) gives one service-wide P90 instead of
        # one series per pod, and ">= 0" drops the NaN Prometheus returns with no traffic.
        promql = (
            f'histogram_quantile(0.90, sum by (le) ('
            f'rate(http_request_duration_seconds_bucket{{deployment="{deployment}"}}[1m])'
            f')) * 1000 >= 0'
        )
        start = time.monotonic()
        polls, p90 = 0, None
        while time.monotonic() - start < self.timeout:
            p90 = self.client.query(promql)
            polls += 1
            # None = no data (Prometheus down, or new pods not scraped yet) → keep polling
            if p90 is not None and p90 < threshold_ms:
                return result(True, p90, time.monotonic() - start, polls)
            time.sleep(self.poll_interval)
        return result(False, p90, time.monotonic() - start, polls)
        # <<<<< MY CODE END
        # Instructor's original stub below never runs now (my code returns above).
        raise NotImplementedError("Implement VerificationLoop.wait_for_recovery()")


# ── TODO: Implement AuditLogger ───────────────────────────────────────────────


class AuditLogger:
    def __init__(self, path: Path = AUDIT_LOG_PATH):
        self.path = path

    def append(self, record: dict) -> None:
        """
        Append `record` as a single JSON line to self.path.

        Hint:
            with open(self.path, "a") as f:
                f.write(json.dumps(record) + "\n")
        """
        # >>>>> MY CODE START — Lab 2 Step 5: AuditLogger.append()
        #       (replaced the instructor's "# TODO: implement this method" stub)
        # "a" appends, so earlier runs are never overwritten; one record per line = JSONL
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return
        # <<<<< MY CODE END
        # Instructor's original stub below never runs now (my code returns above).
        raise NotImplementedError("Implement AuditLogger.append()")


# ── Main ──────────────────────────────────────────────────────────────────────


class _SafeLogger:
    """Lets run() finish before AuditLogger.append() is implemented."""
    def __init__(self, inner: AuditLogger):
        self.inner, self.written = inner, False
    def append(self, record: dict) -> None:
        try:
            self.inner.append(record)
            self.written = True
        except NotImplementedError:
            print("AuditLogger.append() not yet implemented — audit record not written")


def run(prometheus_url: str, simulate: bool, dry_run: bool) -> None:
    deployment = "payment-service"
    namespace  = "default"
    run_id     = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print("\n" + "=" * 65)
    print("LAB 2 — AUTONOMOUS REMEDIATION AGENT")
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

    logger = _SafeLogger(AuditLogger())

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
    try:
        exec_result = executor.execute(action_id, deployment, namespace)
    except NotImplementedError:
        print("\nActionExecutor.execute() not yet implemented — skipping execution")
        return

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

    try:
        verify_result = verifier.wait_for_recovery(deployment, baseline_p90)
    except NotImplementedError:
        print("VerificationLoop.wait_for_recovery() not yet implemented")
        return

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
    if logger.written:
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
