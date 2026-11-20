"""
Lab 3 — orchestrator.py  (STARTER)
=====================================
Build a multi-agent diagnosis and orchestration system.

Architecture
------------
                    ┌─────────────────────────────┐
   incident         │        Orchestrator          │
   trigger  ───────►│  fan-out → arbitrate → act  │
                    └──────┬──────────┬────────────┘
                           │          │
                   ┌───────┴──┐  ┌───┴──────┐
                   │ Metrics  │  │  Logs    │
                   │  Agent   │  │  Agent   │
                   └──────────┘  └──────────┘
                           │          │
                    ┌──────┴──────────┴──────┐
                    │      ActionAgent        │
                    └────────────────────────┘

Agents run CONCURRENTLY using asyncio.gather (fan-out pattern).
The orchestrator applies deterministic arbitration rules — no ML, no voting.

Run (simulate mode — no cluster needed):
    python orchestrator_starter.py --simulate

Run (real mode):
    kubectl port-forward -n monitoring svc/prometheus 9090:9090 &
    python orchestrator_starter.py --prometheus-url http://localhost:9090
"""

from __future__ import annotations
import asyncio
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

sys.path.insert(0, str(Path(__file__).parent.parent / "lab1" / "solution"))
sys.path.insert(0, str(Path(__file__).parent.parent / "lab2"))

# >>>>> MY CODE START — bug fix: import the modules that actually exist
#       The instructor's block imported gate_starter / diagnostic_agent_starter, but the
#       path above only adds lab1/solution (gate.py, diagnostic_agent.py), and lab2 has
#       neither. The try/except ImportError: pass then hid the failure, so nothing was
#       imported: ActionAgent returned IMPORT_ERROR and real mode hit NameError on
#       PrometheusClient. Same modules Lab 2 and lab3/solution use. No try/except now —
#       a missing module should fail loudly at startup, not silently mid-incident.
#       Instructor's original:
#         try:
#             from gate_starter import GateOrchestrator, GateConfig, ActionProposal
#             from diagnostic_agent_starter import (
#                 PrometheusClient, ConfidenceScorer, autonomy_level, SIMULATED_METRICS)
#         except ImportError:
#             pass
from gate import GateOrchestrator, GateConfig, ActionProposal
from diagnostic_agent import (
    PrometheusClient, MetricsReader, ConfidenceScorer,
    detect_symptom_class, symptom_to_action, autonomy_level, SIMULATED_METRICS,
)
# <<<<< MY CODE END


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class AgentFinding:
    """
    Returned by each specialist agent (MetricsAgent, LogsAgent, etc.)
    """
    agent_name:      str
    symptom_class:   str           # e.g. "MEMORY_PRESSURE"
    action_id:       str           # e.g. "pod.rolling_restart"
    confidence:      float         # composite C score, 0–1
    evidence:        dict          # raw metrics / log excerpts
    duration_ms:     int
    timed_out:       bool = False
    error:           Optional[str] = None


@dataclass
class ArbitrationResult:
    """
    Final decision produced by the arbitrator from all AgentFindings.
    """
    agreed_symptom_class: str
    agreed_action_id:     str
    final_confidence:     float
    findings:             list[AgentFinding]
    rules_applied:        list[str] = field(default_factory=list)


# ── Simulated agent data ──────────────────────────────────────────────────────

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


# ── TODO: Implement MetricsAgent ──────────────────────────────────────────────

class MetricsAgent:
    """
    Queries Prometheus RED metrics and returns a diagnosis.
    Weight in arbitration: 1.5 (highest — metrics are most reliable)
    """

    WEIGHT = 1.5

    def __init__(self, client: Optional[PrometheusClient], simulate: bool = False):
        self.client   = client
        self.simulate = simulate

    async def diagnose(self, deployment: str, namespace: str) -> AgentFinding:
        """
        1. If simulate: sleep 0.02s (simulated latency), return SIMULATED_METRICS_FINDING
        2. Else: query Prometheus for memory_usage_ratio, error_rate_ratio, restarts
        3. Use detect_symptom_class() and ConfidenceScorer to build the finding
        4. Return AgentFinding(agent_name="metrics", ...)

        Note: This is an async method. Use `await asyncio.sleep(0)` to yield
        control back to the event loop periodically.
        """
        # TODO: implement this method
        raise NotImplementedError("Implement MetricsAgent.diagnose()")


# ── TODO: Implement LogsAgent ─────────────────────────────────────────────────

class LogsAgent:
    """
    Analyses log patterns (OOMKilled, error spikes, panic lines).
    Weight in arbitration: 1.2
    """

    WEIGHT = 1.2

    # Log patterns and the symptom classes they indicate
    LOG_PATTERNS = {
        "OOMKilled":           "MEMORY_PRESSURE",
        "out of memory":       "MEMORY_PRESSURE",
        "connection refused":  "DEPENDENCY_FAILURE",
        "FATAL":               "DEPLOYMENT_REGRESSION",
        "panic":               "DEPLOYMENT_REGRESSION",
        "too many connections": "DB_BOTTLENECK",
    }

    def __init__(self, kubectl_logs_cmd: Optional[list[str]] = None,
                 simulate: bool = False):
        self.kubectl_logs_cmd = kubectl_logs_cmd
        self.simulate         = simulate

    async def diagnose(self, deployment: str, namespace: str) -> AgentFinding:
        """
        1. If simulate: sleep 0.03s, return SIMULATED_LOGS_FINDING
        2. Else: run `kubectl logs deployment/{deployment} -n {namespace} --since=10m`
        3. Scan output for patterns in LOG_PATTERNS
        4. Count occurrences of each pattern
        5. Map most-frequent pattern to symptom_class
        6. Set confidence based on occurrence count:
               count >= 5 → 0.90
               count >= 3 → 0.75
               count >= 1 → 0.60
               else       → 0.30 (no pattern found)
        7. Return AgentFinding(agent_name="logs", ...)

        Tip: use asyncio.create_subprocess_exec or run subprocess in executor
        to avoid blocking the event loop.
        """
        # TODO: implement this method
        raise NotImplementedError("Implement LogsAgent.diagnose()")


# ── TODO: Implement arbitration rules ─────────────────────────────────────────

# ── PROVIDED: Arbitration helpers ────────────────────────────────────────────
# Already defined here — reference these in your arbitrate() implementation.

AGENT_WEIGHTS = {
    "metrics": MetricsAgent.WEIGHT,   # 1.5
    "logs":    LogsAgent.WEIGHT,      # 1.2
}

ACTION_MAP = {
    "MEMORY_PRESSURE":       "pod.rolling_restart",
    "DB_BOTTLENECK":         "db.connection_reset",
    "DEPLOYMENT_REGRESSION": "config.rollback",
    "CAPACITY_EXHAUSTION":   "scale.horizontal_add",
    "DEPENDENCY_FAILURE":    "circuit.reset",
}


def arbitrate(findings: list[AgentFinding]) -> ArbitrationResult:
    """
    Apply deterministic arbitration rules to reach a consensus.

    Rules (apply in order):
    ─────────────────────────────────────────────────────────────────
    Rule 1 — TIMEOUT PENALTY
        For each agent that timed out, reduce its confidence by 0.05.
        Append: "timeout_penalty: {agent_name}"

    Rule 2 — CONTRADICTION PENALTY
        If MetricsAgent and LogsAgent disagree on symptom_class,
        reduce BOTH confidence scores by 0.15.
        Append: "contradiction: metrics={...} logs={...}"

    Rule 3 — CORROBORATION BONUS
        If MetricsAgent and LogsAgent agree on symptom_class,
        increase BOTH confidence scores by 0.10 (cap at 1.0).
        Append: "corroboration: {symptom_class}"

    Rule 4 — CASCADE OVERRIDE
        If ANY agent finds DEPLOYMENT_REGRESSION with confidence >= 0.80,
        override the agreed symptom_class to DEPLOYMENT_REGRESSION and
        set action_id to "config.rollback" regardless of other agents.
        Append: "cascade_override: DEPLOYMENT_REGRESSION"

    Rule 5 — WEIGHTED CONSENSUS
        Compute: agreed_symptom_class = argmax of sum(weight × confidence)
        for each symptom_class across all non-timed-out agents.
        final_confidence = max(adjusted finding confidence) of winning class.

    Agent weights: MetricsAgent=1.5, LogsAgent=1.2, (others=1.0)
    ─────────────────────────────────────────────────────────────────

    Returns ArbitrationResult with the agreed action and final confidence.
    """
    # TODO: implement this function
    raise NotImplementedError("Implement arbitrate()")


# ── ActionAgent ───────────────────────────────────────────────────────────────
# Provided — no student implementation needed.

class ActionAgent:
    """
    Takes the ArbitrationResult, evaluates gates, and executes the action.
    Re-uses Lab 2's GateOrchestrator and ActionExecutor.
    """

    def __init__(self, simulate: bool = False, dry_run: bool = False):
        self.simulate = simulate
        self.dry_run  = dry_run

    async def act(
        self,
        result: ArbitrationResult,
        deployment: str,
        namespace:  str,
    ) -> dict:
        try:
            from gate import GateOrchestrator, GateConfig, ActionProposal  # MY CODE: bug fix (was gate_starter, not on path)
        except ImportError:
            return {"outcome": "IMPORT_ERROR"}

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
                "outcome":        "GATE_BLOCKED",
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
                "outcome":      "EXECUTED",
                "action_id":    result.agreed_action_id,
                "deployment":   deployment,
                "simulate":     True,
                "success":      True,
                "output":       f"[SIMULATE] {' '.join(cmd)}",
                "duration_ms":  42,
            }

        # Real execution (requires kubectl)
        import subprocess
        if self.dry_run:
            return {
                "outcome":    "EXECUTED",
                "action_id":  result.agreed_action_id,
                "dry_run":    True,
                "output":     f"[DRY RUN] {' '.join(cmd)}",
                "duration_ms": 0,
            }
        t0   = time.monotonic()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {
            "outcome":     "EXECUTED" if proc.returncode == 0 else "EXECUTION_FAILED",
            "action_id":   result.agreed_action_id,
            "deployment":  deployment,
            "success":     proc.returncode == 0,
            "output":      proc.stdout.strip(),
            "error":       proc.stderr.strip(),
            "duration_ms": int((time.monotonic() - t0) * 1000),
        }


# ── TODO: Implement Orchestrator ──────────────────────────────────────────────

class Orchestrator:
    """
    Fan-out: runs MetricsAgent and LogsAgent concurrently with a timeout.
    Arbitrates the results.
    Hands off to ActionAgent.
    """

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
        """
        1. Fan out: gather MetricsAgent.diagnose() and LogsAgent.diagnose()
           concurrently, each wrapped in asyncio.wait_for(coro, timeout=FAN_OUT_TIMEOUT_S)
        2. For any agent that times out: create a timed_out=True AgentFinding
        3. Arbitrate findings
        4. Act via ActionAgent
        5. Return result dict

        Hint for fan-out with per-agent timeout:
            async def safe(coro, name):
                try:
                    return await asyncio.wait_for(coro, timeout=self.FAN_OUT_TIMEOUT_S)
                except asyncio.TimeoutError:
                    return AgentFinding(agent_name=name, ..., timed_out=True,
                                        confidence=0.0, symptom_class="UNKNOWN",
                                        action_id="escalate_human",
                                        evidence={}, duration_ms=50)

            findings = await asyncio.gather(
                safe(self.metrics_agent.diagnose(deployment, namespace), "metrics"),
                safe(self.logs_agent.diagnose(deployment, namespace), "logs"),
            )
        """
        # TODO: implement this method
        raise NotImplementedError("Implement Orchestrator.run()")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(prometheus_url: str, simulate: bool, dry_run: bool) -> None:
    deployment = "payment-service"
    namespace  = "default"

    print("\n" + "=" * 65)
    print("LAB 3 — MULTI-AGENT ORCHESTRATION")
    print("=" * 65)

    if not simulate and not HAS_REQUESTS:
        print("ERROR: requests not installed. Run: pip install requests")
        sys.exit(1)

    client = None if simulate else PrometheusClient(prometheus_url)

    metrics_agent = MetricsAgent(client=client, simulate=simulate)
    logs_agent    = LogsAgent(simulate=simulate)
    action_agent  = ActionAgent(simulate=simulate, dry_run=dry_run)

    orchestrator = Orchestrator(metrics_agent, logs_agent, action_agent)
    if not simulate:
        orchestrator.FAN_OUT_TIMEOUT_S = Orchestrator.REAL_FAN_OUT_TIMEOUT_S

    try:
        result = await orchestrator.run(deployment, namespace)
    except NotImplementedError as e:
        print(f"\nNot implemented: {e}")
        print("Implement the TODO methods above and re-run.")
        return

    print(f"\nFinal result:")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Lab 3 — Multi-Agent Orchestrator")
    parser.add_argument("--simulate",   action="store_true",
                        help="Use simulated metrics and logs (no cluster needed)")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Go through gate + execution but don't run kubectl")
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    args = parser.parse_args()
    asyncio.run(main(args.prometheus_url, args.simulate, args.dry_run))
