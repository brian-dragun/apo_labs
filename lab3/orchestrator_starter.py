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

# ─────────────────────────────────────────────────────────────────────────────
# HOW TO READ THIS FILE
#   Code between  >>>>> MY CODE ... START  and  <<<<< MY CODE ... END  markers
#   is what I wrote for the lab. Everything else is the instructor's original.
#   Original copy:  git show workshop-original:lab3/orchestrator_starter.py
#   See every change:  git diff workshop-original -- lab3/orchestrator_starter.py
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations
import asyncio
import argparse
import json
import subprocess   # MY CODE (Lab 3 Step 3) — LogsAgent runs kubectl logs
import sys
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from dataclasses import replace   # MY CODE (Lab 3 Step 4) — adjust findings without mutating them
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
        # >>>>> MY CODE START — Lab 3 Step 2: MetricsAgent.diagnose()
        #       (replaced the instructor's "# TODO: implement this method" stub)
        start = time.monotonic()

        if self.simulate:
            await asyncio.sleep(0.02)                     # simulated Prometheus latency
            return AgentFinding(
                agent_name="metrics",
                **SIMULATED_METRICS_FINDING,              # symptom_class, action_id, confidence, evidence
                duration_ms=int((time.monotonic() - start) * 1000),
                timed_out=False,
            )

        # Real mode: same lab1/solution reader Lab 2 uses (with the multi-pod query fix),
        # imported at the top of the file.
        # MetricsReader.read() is synchronous (requests); run it in a worker thread so
        # the event loop keeps running LogsAgent concurrently.
        loop = asyncio.get_running_loop()
        metrics = await loop.run_in_executor(
            None, lambda: MetricsReader(self.client, deployment, namespace).read()
        )

        symptom_class = detect_symptom_class(metrics)
        action_id     = symptom_to_action(symptom_class)
        scores        = ConfidenceScorer().compute(metrics, symptom_class, action_id)

        return AgentFinding(
            agent_name="metrics",
            symptom_class=symptom_class,
            action_id=action_id,
            confidence=scores["C"],
            evidence={**metrics, "scores": scores},
            duration_ms=int((time.monotonic() - start) * 1000),
            timed_out=False,
        )
        # <<<<< MY CODE END
        # Instructor's original stub below never runs now (my code returns above).
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
        # >>>>> MY CODE START — Lab 3 Step 3: LogsAgent.diagnose()
        #       (replaced the instructor's "# TODO: implement this method" stub)
        start = time.monotonic()

        def finding(symptom_class: str, confidence: float, evidence: dict,
                    error: Optional[str] = None) -> AgentFinding:
            return AgentFinding(
                agent_name="logs",
                symptom_class=symptom_class,
                action_id=ACTION_MAP.get(symptom_class, "escalate_human"),
                confidence=confidence,
                evidence=evidence,
                duration_ms=int((time.monotonic() - start) * 1000),   # dict has no duration_ms
                timed_out=False,
                error=error,
            )

        if self.simulate:
            await asyncio.sleep(0.03)                     # simulated kubectl latency
            return finding(SIMULATED_LOGS_FINDING["symptom_class"],
                           SIMULATED_LOGS_FINDING["confidence"],
                           SIMULATED_LOGS_FINDING["evidence"])

        # Real mode: kubectl is synchronous, so run it in a worker thread.
        # Note: "deployment/<name>" makes kubectl read ONE pod of the deployment.
        cmd = self.kubectl_logs_cmd or [
            "kubectl", "logs", f"deployment/{deployment}", "-n", namespace, "--since=10m",
        ]
        loop = asyncio.get_running_loop()
        try:
            proc = await loop.run_in_executor(
                None, lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            # Beyond the README: no logs → low-confidence UNKNOWN, pipeline continues
            return finding("UNKNOWN", 0.30, {}, error=f"{type(exc).__name__}: {exc}")
        if proc.returncode != 0:
            return finding("UNKNOWN", 0.30, {}, error=proc.stderr.strip())

        output = proc.stdout
        counts = {pattern: output.count(pattern) for pattern in self.LOG_PATTERNS}
        # Most frequent pattern; ties go to the first pattern in LOG_PATTERNS (deterministic)
        top_pattern = max(counts, key=counts.get)
        occurrences = counts[top_pattern]

        if occurrences >= 5:
            confidence = 0.90
        elif occurrences >= 3:
            confidence = 0.75
        elif occurrences >= 1:
            confidence = 0.60
        else:
            confidence = 0.30

        symptom_class = self.LOG_PATTERNS[top_pattern] if occurrences else "UNKNOWN"
        evidence = {
            "log_pattern":     top_pattern if occurrences else None,
            "occurrences_10m": occurrences,
            "pattern_counts":  {p: n for p, n in counts.items() if n},
            "lines_scanned":   output.count("\n"),
        }
        return finding(symptom_class, confidence, evidence)
        # <<<<< MY CODE END
        # Instructor's original stub below never runs now (my code returns above).
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
    # >>>>> MY CODE START — Lab 3 Step 4: arbitrate()
    #       (replaced the instructor's "# TODO: implement this function" stub)
    # The caller's findings are never modified: each rule works on adjusted copies
    # (dataclasses.replace), so the raw agent findings stay intact for the audit trail
    # and the same input always produces the same output.
    rules_applied: list[str] = []

    def clamp(c: float) -> float:
        return round(min(1.0, max(0.0, c)), 4)

    # Rule 1 — timeout penalty
    adjusted: list[AgentFinding] = []
    for f in findings:
        if f.timed_out:
            f = replace(f, confidence=clamp(f.confidence - 0.05))
            rules_applied.append(f"timeout_penalty: {f.agent_name}")
        adjusted.append(f)

    # Rules 2 / 3 — contradiction penalty OR corroboration bonus (mutually exclusive).
    # Only between agents that actually answered: a timed-out agent's "UNKNOWN" is
    # an absence of evidence, not a disagreement.
    by_name = {f.agent_name: f for f in adjusted if not f.timed_out}
    metrics, logs = by_name.get("metrics"), by_name.get("logs")
    if metrics and logs:
        if metrics.symptom_class != logs.symptom_class:
            delta = -0.15
            rules_applied.append(
                f"contradiction: metrics={metrics.symptom_class} vs logs={logs.symptom_class}"
            )
        else:
            delta = +0.10
            rules_applied.append(f"corroboration: {metrics.symptom_class}")
        adjusted = [
            replace(f, confidence=clamp(f.confidence + delta))
            if f.agent_name in ("metrics", "logs") and not f.timed_out else f
            for f in adjusted
        ]

    # Rule 4 — cascade override: a confident DEPLOYMENT_REGRESSION wins outright
    for f in adjusted:
        if f.symptom_class == "DEPLOYMENT_REGRESSION" and f.confidence >= 0.80:
            return ArbitrationResult(
                agreed_symptom_class="DEPLOYMENT_REGRESSION",
                agreed_action_id="config.rollback",
                final_confidence=f.confidence,
                findings=adjusted,
                rules_applied=rules_applied + ["cascade_override: DEPLOYMENT_REGRESSION"],
            )

    # Rule 5 — weighted consensus over agents that answered
    scores: dict[str, float] = {}
    best_conf: dict[str, float] = {}
    for f in adjusted:
        if f.timed_out:
            continue
        weight = AGENT_WEIGHTS.get(f.agent_name, 1.0)
        scores[f.symptom_class] = scores.get(f.symptom_class, 0.0) + weight * f.confidence
        best_conf[f.symptom_class] = max(best_conf.get(f.symptom_class, 0.0), f.confidence)

    if not scores:
        # Beyond the README: every agent timed out → no evidence → escalate
        return ArbitrationResult(
            agreed_symptom_class="UNKNOWN",
            agreed_action_id="escalate_human",
            final_confidence=0.0,
            findings=adjusted,
            rules_applied=rules_applied + ["all_agents_timed_out"],
        )

    # max() keeps the first key on a tie; agents always report in the same order,
    # so ties resolve the same way on every run
    agreed = max(scores, key=scores.get)
    rules_applied.append(f"weighted_consensus: {agreed} (score={scores[agreed]:.4f})")
    return ArbitrationResult(
        agreed_symptom_class=agreed,
        agreed_action_id=ACTION_MAP.get(agreed, "escalate_human"),
        final_confidence=best_conf[agreed],
        findings=adjusted,
        rules_applied=rules_applied,
    )
    # <<<<< MY CODE END
    # Instructor's original stub below never runs now (my code returns above).
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
        # >>>>> MY CODE START — Lab 3 Step 5: Orchestrator.run()
        #       (replaced the instructor's "# TODO: implement this method" stub)
        start = time.monotonic()
        timeout_s = self.FAN_OUT_TIMEOUT_S

        async def safe(coro, name: str) -> AgentFinding:
            # Per-agent timeout: a slow agent becomes a timed-out finding instead of
            # holding up the incident response
            try:
                return await asyncio.wait_for(coro, timeout=timeout_s)
            except asyncio.TimeoutError:
                return AgentFinding(agent_name=name, symptom_class="UNKNOWN",
                                    action_id="escalate_human", confidence=0.0,
                                    evidence={}, duration_ms=int(timeout_s * 1000),
                                    timed_out=True)

        # Fan-out: both agents run concurrently → wall time ≈ the slowest agent, not the sum
        findings = list(await asyncio.gather(
            safe(self.metrics_agent.diagnose(deployment, namespace), "metrics"),
            safe(self.logs_agent.diagnose(deployment, namespace),    "logs"),
        ))
        for f in findings:
            icon = "⏱ " if f.timed_out else "✅"
            print(f"  {icon}  {f.agent_name:10s} → {f.symptom_class:25s} "
                  f"C={f.confidence:.2f} ({f.duration_ms}ms)")

        result = arbitrate(findings)
        print(f"\nArbitration rules: {result.rules_applied}")
        print(f"Consensus:  {result.agreed_symptom_class}  →  {result.agreed_action_id}  "
              f"C={result.final_confidence:.4f}")

        # The starter's act() signature is act(result, deployment, namespace) — the
        # README's keyword-argument version (action_id=..., symptom_class=...) would
        # raise TypeError. ActionAgent re-runs the gates before executing.
        action = await self.action_agent.act(result, deployment, namespace)

        wall_ms = int((time.monotonic() - start) * 1000)
        print(f"Action: {action.get('outcome')} (total wall time {wall_ms}ms)")

        return {
            "deployment":    deployment,
            "namespace":     namespace,
            "findings":      [
                {"agent": f.agent_name, "symptom": f.symptom_class, "action": f.action_id,
                 "confidence": f.confidence, "timed_out": f.timed_out,
                 "duration_ms": f.duration_ms, "error": f.error}
                for f in findings                      # raw findings, before arbitration
            ],
            "arbitration":   {
                "symptom_class": result.agreed_symptom_class,
                "action_id":     result.agreed_action_id,
                "confidence":    result.final_confidence,
                "rules_applied": result.rules_applied,
            },
            "action":        action,
            "wall_time_ms":  wall_ms,
        }
        # <<<<< MY CODE END
        # Instructor's original stub below never runs now (my code returns above).
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
