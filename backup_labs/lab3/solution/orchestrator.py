"""
Lab 3 — orchestrator.py  (SOLUTION)
=====================================
Complete multi-agent orchestration with fan-out, timeout, and arbitration.
Do not share with students before the lab exercise.
"""

from __future__ import annotations
import asyncio
import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

_this = Path(__file__).resolve().parent
for _p in [_this.parent.parent / "lab1" / "solution",   # running from lab3/solution/
            _this.parent / "lab1" / "solution"]:          # running from lab3/ (if copied)
    if _p.exists():
        sys.path.insert(0, str(_p))
        break
for _p in [_this.parent.parent / "lab2" / "solution",   # running from lab3/solution/
            _this.parent / "lab2" / "solution"]:          # running from lab3/ (if copied)
    if _p.exists():
        sys.path.insert(0, str(_p))
        break

from gate import GateOrchestrator, GateConfig, ActionProposal
from diagnostic_agent import (
    PrometheusClient, MetricsReader, ConfidenceScorer,
    detect_symptom_class, symptom_to_action, autonomy_level, SIMULATED_METRICS,
)


@dataclass
class AgentFinding:
    agent_name:    str
    symptom_class: str
    action_id:     str
    confidence:    float
    evidence:      dict
    duration_ms:   int
    timed_out:     bool = False
    error:         Optional[str] = None


@dataclass
class ArbitrationResult:
    agreed_symptom_class: str
    agreed_action_id:     str
    final_confidence:     float
    findings:             list[AgentFinding]
    rules_applied:        list[str] = field(default_factory=list)


SIMULATED_METRICS_FINDING = {
    "symptom_class": "MEMORY_PRESSURE",
    "action_id":     "pod.rolling_restart",
    "confidence":    0.84,
    "evidence":      {"memory_usage_ratio": 0.91, "restart_count_1h": 2},
}

SIMULATED_LOGS_FINDING = {
    "symptom_class": "MEMORY_PRESSURE",
    "action_id":     "pod.rolling_restart",
    "confidence":    0.76,
    "evidence":      {"log_pattern": "OOMKilled", "occurrences_1h": 3},
}

ACTION_MAP = {
    "MEMORY_PRESSURE":       "pod.rolling_restart",
    "DB_BOTTLENECK":         "db.connection_reset",
    "DEPLOYMENT_REGRESSION": "config.rollback",
    "CAPACITY_EXHAUSTION":   "scale.horizontal_add",
    "DEPENDENCY_FAILURE":    "circuit.reset",
}


class MetricsAgent:
    WEIGHT = 1.5

    def __init__(self, client: Optional[PrometheusClient], simulate: bool = False):
        self.client   = client
        self.simulate = simulate

    async def diagnose(self, deployment: str, namespace: str) -> AgentFinding:
        t0 = time.monotonic()

        if self.simulate:
            await asyncio.sleep(0.02)
            d = SIMULATED_METRICS_FINDING
            return AgentFinding(
                agent_name="metrics",
                symptom_class=d["symptom_class"],
                action_id=d["action_id"],
                confidence=d["confidence"],
                evidence=d["evidence"],
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        loop    = asyncio.get_event_loop()
        metrics = await loop.run_in_executor(
            None,
            lambda: MetricsReader(self.client, deployment, namespace).read(),
        )

        symptom_class = detect_symptom_class(metrics)
        action_id     = ACTION_MAP.get(symptom_class, "escalate_human")
        scorer        = ConfidenceScorer()
        scores        = scorer.compute(metrics, symptom_class, action_id)

        return AgentFinding(
            agent_name="metrics",
            symptom_class=symptom_class,
            action_id=action_id,
            confidence=scores["C"],
            evidence={k: v for k, v in metrics.items()
                      if k in ("memory_usage_ratio", "error_rate_ratio",
                               "p99_latency_ms", "restart_count_1h")},
            duration_ms=int((time.monotonic() - t0) * 1000),
        )


class LogsAgent:
    WEIGHT = 1.2

    LOG_PATTERNS = {
        "OOMKilled":           "MEMORY_PRESSURE",
        "out of memory":       "MEMORY_PRESSURE",
        "connection refused":  "DEPENDENCY_FAILURE",
        "FATAL":               "DEPLOYMENT_REGRESSION",
        "panic":               "DEPLOYMENT_REGRESSION",
        "too many connections": "DB_BOTTLENECK",
    }

    def __init__(self, simulate: bool = False):
        self.simulate = simulate

    async def diagnose(self, deployment: str, namespace: str) -> AgentFinding:
        t0 = time.monotonic()

        if self.simulate:
            await asyncio.sleep(0.03)
            d = SIMULATED_LOGS_FINDING
            return AgentFinding(
                agent_name="logs",
                symptom_class=d["symptom_class"],
                action_id=d["action_id"],
                confidence=d["confidence"],
                evidence=d["evidence"],
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        loop = asyncio.get_event_loop()
        try:
            proc = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["kubectl", "logs", f"deployment/{deployment}",
                     "-n", namespace, "--since=10m"],
                    capture_output=True, text=True, timeout=10,
                ),
            )
            log_text = proc.stdout
        except Exception as e:
            return AgentFinding(
                agent_name="logs", symptom_class="UNKNOWN",
                action_id="escalate_human", confidence=0.0, evidence={},
                duration_ms=int((time.monotonic() - t0) * 1000), error=str(e),
            )

        counts: dict[str, int] = {}
        for pattern, symptom in self.LOG_PATTERNS.items():
            c = log_text.count(pattern)
            if c > 0:
                counts[symptom] = counts.get(symptom, 0) + c

        if not counts:
            return AgentFinding(
                agent_name="logs", symptom_class="UNKNOWN",
                action_id="escalate_human", confidence=0.30,
                evidence={"patterns_found": 0},
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

        best_symptom = max(counts, key=lambda k: counts[k])
        best_count   = counts[best_symptom]
        confidence   = 0.90 if best_count >= 5 else (0.75 if best_count >= 3 else 0.60)
        action_id    = ACTION_MAP.get(best_symptom, "escalate_human")

        return AgentFinding(
            agent_name="logs",
            symptom_class=best_symptom,
            action_id=action_id,
            confidence=confidence,
            evidence={"pattern_counts": counts, "top_match_count": best_count},
            duration_ms=int((time.monotonic() - t0) * 1000),
        )


AGENT_WEIGHTS = {"metrics": 1.5, "logs": 1.2}


def arbitrate(findings: list[AgentFinding]) -> ArbitrationResult:
    rules_applied: list[str] = []

    # Rule 1 — TIMEOUT PENALTY
    for f in findings:
        if f.timed_out:
            f.confidence = max(0.0, f.confidence - 0.05)
            rules_applied.append(f"timeout_penalty: {f.agent_name}")

    # Rule 2 / Rule 3 — CONTRADICTION PENALTY / CORROBORATION BONUS
    active = [f for f in findings if not f.timed_out]
    if len(active) >= 2:
        classes = {f.symptom_class for f in active}
        if len(classes) == 1:
            # All agree
            for f in active:
                f.confidence = min(1.0, f.confidence + 0.10)
            rules_applied.append(f"corroboration: {next(iter(classes))}")
        else:
            # Contradiction
            for f in active:
                f.confidence = max(0.0, f.confidence - 0.15)
            rules_applied.append(
                f"contradiction: "
                + " vs ".join(f"{f.agent_name}={f.symptom_class}" for f in active)
            )

    # Rule 4 — CASCADE OVERRIDE
    for f in findings:
        if f.symptom_class == "DEPLOYMENT_REGRESSION" and f.confidence >= 0.80:
            rules_applied.append("cascade_override: DEPLOYMENT_REGRESSION")
            return ArbitrationResult(
                agreed_symptom_class="DEPLOYMENT_REGRESSION",
                agreed_action_id="config.rollback",
                final_confidence=f.confidence,
                findings=findings,
                rules_applied=rules_applied,
            )

    # Rule 5 — WEIGHTED CONSENSUS
    class_scores: dict[str, float] = {}
    class_max: dict[str, float] = {}
    for f in findings:
        if f.timed_out:
            continue
        w = AGENT_WEIGHTS.get(f.agent_name, 1.0)
        class_scores[f.symptom_class] = (
            class_scores.get(f.symptom_class, 0.0) + w * f.confidence
        )
        class_max[f.symptom_class] = max(
            class_max.get(f.symptom_class, 0.0), f.confidence
        )

    if not class_scores:
        # All agents timed out
        return ArbitrationResult(
            agreed_symptom_class="UNKNOWN",
            agreed_action_id="escalate_human",
            final_confidence=0.0,
            findings=findings,
            rules_applied=rules_applied + ["all_agents_timed_out"],
        )

    best_class = max(class_scores, key=lambda k: class_scores[k])
    final_conf = round(class_max[best_class], 4)
    action_id  = ACTION_MAP.get(best_class, "escalate_human")

    rules_applied.append(
        f"weighted_consensus: {best_class} "
        f"(score={class_scores[best_class]:.4f})"
    )

    return ArbitrationResult(
        agreed_symptom_class=best_class,
        agreed_action_id=action_id,
        final_confidence=final_conf,
        findings=findings,
        rules_applied=rules_applied,
    )


class ActionAgent:
    def __init__(self, simulate: bool = False, dry_run: bool = False):
        self.simulate = simulate
        self.dry_run  = dry_run

    async def act(self, result: ArbitrationResult, deployment: str, namespace: str) -> dict:
        proposal = ActionProposal(
            action_id=result.agreed_action_id,
            deployment=deployment,
            namespace=namespace,
            estimated_cost_per_hour_usd=50.0,
            blast_radius_fraction=0.05,
            reversal_time_seconds=120,
            symptom_class=result.agreed_symptom_class,
            confidence_score=result.final_confidence,
        )
        gate_result = GateOrchestrator(GateConfig()).evaluate(proposal)

        if not gate_result.approved:
            return {
                "outcome":         "GATE_BLOCKED",
                "gate_blocked_by": gate_result.blocked_by,
                "gate_reasons":    gate_result.reasons,
            }

        if result.final_confidence < 0.60:
            return {"outcome": "ESCALATED", "confidence": result.final_confidence}

        commands = {
            "pod.rolling_restart":  ["kubectl", "rollout", "restart",
                                     f"deployment/{deployment}", "-n", namespace],
            "db.connection_reset":  ["kubectl", "rollout", "restart",
                                     f"deployment/{deployment}", "-n", namespace],
            "config.rollback":      ["kubectl", "rollout", "undo",
                                     f"deployment/{deployment}", "-n", namespace],
            "scale.horizontal_add": ["kubectl", "scale", f"deployment/{deployment}",
                                     "--replicas=3", "-n", namespace],
        }
        cmd = commands.get(result.agreed_action_id)
        if cmd is None:
            return {
                "outcome":   "ESCALATED",
                "action_id": result.agreed_action_id,
                "reason":    f"no executor for {result.agreed_action_id}; escalating to a human",
            }

        if self.simulate:
            return {
                "outcome":    "EXECUTED",
                "action_id":  result.agreed_action_id,
                "deployment": deployment,
                "simulate":   True,
                "success":    True,
                "output":     f"[SIMULATE] {' '.join(cmd)}",
                "duration_ms": 38,
            }

        if self.dry_run:
            return {
                "outcome":    "EXECUTED",
                "action_id":  result.agreed_action_id,
                "dry_run":    True,
                "output":     f"[DRY RUN] {' '.join(cmd)}",
                "duration_ms": 0,
            }

        loop = asyncio.get_event_loop()
        t0   = time.monotonic()
        proc = await loop.run_in_executor(
            None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        )
        return {
            "outcome":    "EXECUTED" if proc.returncode == 0 else "EXECUTION_FAILED",
            "action_id":  result.agreed_action_id,
            "deployment": deployment,
            "success":    proc.returncode == 0,
            "output":     proc.stdout.strip(),
            "error":      proc.stderr.strip(),
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }


class Orchestrator:
    FAN_OUT_TIMEOUT_S      = 0.050   # simulate mode: 50ms per agent
    REAL_FAN_OUT_TIMEOUT_S = 10.0    # real mode: Prometheus + kubectl logs need seconds, not ms

    def __init__(
        self,
        metrics_agent: MetricsAgent,
        logs_agent:    LogsAgent,
        action_agent:  ActionAgent,
    ):
        self.metrics_agent = metrics_agent
        self.logs_agent    = logs_agent
        self.action_agent  = action_agent

    async def run(self, deployment: str, namespace: str) -> dict:
        t0 = time.monotonic()

        async def safe(coro, name: str) -> AgentFinding:
            try:
                return await asyncio.wait_for(coro, timeout=self.FAN_OUT_TIMEOUT_S)
            except asyncio.TimeoutError:
                return AgentFinding(
                    agent_name=name,
                    symptom_class="UNKNOWN",
                    action_id="escalate_human",
                    confidence=0.0,
                    evidence={},
                    duration_ms=int(self.FAN_OUT_TIMEOUT_S * 1000),
                    timed_out=True,
                )

        findings = list(await asyncio.gather(
            safe(self.metrics_agent.diagnose(deployment, namespace), "metrics"),
            safe(self.logs_agent.diagnose(deployment, namespace),    "logs"),
        ))

        for f in findings:
            icon = "⏱" if f.timed_out else "✅"
            print(f"  {icon}  {f.agent_name:10s} → {f.symptom_class:25s} "
                  f"C={f.confidence:.2f} ({f.duration_ms}ms)")

        arbitration = arbitrate(findings)
        print(f"\nArbitration rules: {arbitration.rules_applied}")
        print(f"Consensus:  {arbitration.agreed_symptom_class}  "
              f"→  {arbitration.agreed_action_id}  "
              f"C={arbitration.final_confidence:.4f}")
        print(f"Autonomy:   {autonomy_level(arbitration.final_confidence)}")

        action_result = await self.action_agent.act(
            arbitration, deployment, namespace
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        print(f"\nAction: {action_result.get('outcome')} "
              f"(total wall time {elapsed_ms}ms)")

        return {
            "run_id":          datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "deployment":      deployment,
            "namespace":       namespace,
            "findings":        [
                {
                    "agent":        f.agent_name,
                    "symptom":      f.symptom_class,
                    "action":       f.action_id,
                    "confidence":   f.confidence,
                    "timed_out":    f.timed_out,
                    "duration_ms":  f.duration_ms,
                }
                for f in findings
            ],
            "arbitration":     {
                "agreed_symptom_class": arbitration.agreed_symptom_class,
                "agreed_action_id":     arbitration.agreed_action_id,
                "final_confidence":     arbitration.final_confidence,
                "rules_applied":        arbitration.rules_applied,
            },
            "action_result":   action_result,
            "total_ms":        elapsed_ms,
        }


async def main(prometheus_url: str, simulate: bool, dry_run: bool) -> None:
    deployment = "payment-service"
    namespace  = "default"

    print("\n" + "=" * 65)
    print("LAB 3 — MULTI-AGENT ORCHESTRATION  (SOLUTION)")
    print("=" * 65)

    client = None if simulate else PrometheusClient(prometheus_url)

    metrics_agent = MetricsAgent(client=client, simulate=simulate)
    logs_agent    = LogsAgent(simulate=simulate)
    action_agent  = ActionAgent(simulate=simulate, dry_run=dry_run)

    orchestrator = Orchestrator(metrics_agent, logs_agent, action_agent)
    if not simulate:
        orchestrator.FAN_OUT_TIMEOUT_S = Orchestrator.REAL_FAN_OUT_TIMEOUT_S

    print(f"\nFan-out timeout: {orchestrator.FAN_OUT_TIMEOUT_S*1000:.0f}ms per agent\n")
    result = await orchestrator.run(deployment, namespace)

    print("\n" + "─" * 65)
    print("Full result:")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate",   action="store_true")
    parser.add_argument("--dry-run",    action="store_true")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    args = parser.parse_args()
    asyncio.run(main(args.prometheus_url, args.simulate, args.dry_run))
