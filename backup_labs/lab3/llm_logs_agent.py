"""
Lab 3 take-home — llm_logs_agent.py
====================================
Swap the brain, keep the harness.

This file replaces the rule-based LogsAgent from Lab 3 with one that asks a
Claude model to read the logs. Nothing else in the pipeline changes: the same
orchestrator, the same arbitration rules, the same gates and the same
AgentFinding interface. That is the point of the exercise. The model is the
replaceable part; the deterministic harness around it is what keeps you safe.

Three rules this agent follows, and why:
  1. The model names a symptom class and cites log lines. It does NOT set the
     confidence score. Confidence comes from how many of the cited lines
     really appear in the logs, checked by code.
  2. Any failure (no API key, network error, timeout, invalid output) falls
     back to the rule-based LogsAgent, so the pipeline never breaks.
  3. Simulate mode is the default. No API call is made unless you pass --llm.

Usage (from the repo root):
  python3 lab3/llm_logs_agent.py                 # simulated logs, rule-based (no API call)
  python3 lab3/llm_logs_agent.py --llm           # simulated logs, read by Claude
  python3 lab3/llm_logs_agent.py --llm --real    # real cluster logs, read by Claude

--llm needs:   pip install anthropic   and   export ANTHROPIC_API_KEY=...
Optional:      export APO_LLM_MODEL=claude-haiku-5-5   (a small, fast model suits diagnosis)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here / "solution"))

from orchestrator import (  # noqa: E402  (path set above)
    ACTION_MAP, ActionAgent, AgentFinding, LogsAgent, MetricsAgent, Orchestrator,
    PrometheusClient,
)

SYMPTOM_CLASSES = sorted(ACTION_MAP.keys()) + ["UNKNOWN"]
DEFAULT_MODEL = "claude-haiku-5-5"
LLM_TIMEOUT_S = 10.0
LLM_FAN_OUT_TIMEOUT_S = 15.0   # a model call takes hundreds of ms to seconds, not 50 ms

# Ten minutes of logs from a payment-service that is struggling. None of these
# lines contains a phrase in LogsAgent.LOG_PATTERNS, so the rule-based agent
# finds nothing. A model reading them sees a dependency failing upstream.
SIMULATED_LOG_TEXT = """\
2026-11-19T14:02:03Z level=info  service=payment-service msg="GC pause 410ms" heap_pct=87
2026-11-19T14:02:07Z level=error service=payment-service msg="upstream order-service returned 503 Service Unavailable" trace_id=7f3a91
2026-11-19T14:02:07Z level=warn  service=payment-service msg="retrying call to order-service (attempt 3/3)" trace_id=7f3a91
2026-11-19T14:02:09Z level=error service=payment-service msg="deadline exceeded waiting for order-service after 2000ms" trace_id=c21d08
2026-11-19T14:02:11Z level=warn  service=payment-service msg="worker pool saturated: 190/200 threads blocked on outbound calls"
2026-11-19T14:02:12Z level=error service=payment-service msg="upstream order-service returned 503 Service Unavailable" trace_id=e90b44
2026-11-19T14:02:15Z level=info  service=payment-service msg="GC pause 380ms" heap_pct=88
2026-11-19T14:02:18Z level=error service=payment-service msg="deadline exceeded waiting for order-service after 2000ms" trace_id=4aa7d2
"""

SYSTEM_PROMPT = f"""You are the logs specialist in an incident-response system.
Read the service logs and classify the most likely root cause into exactly one
of these symptom classes: {", ".join(SYMPTOM_CLASSES)}.

Rules:
- Prefer the root cause over a downstream symptom. High GC or memory caused by
  threads stuck waiting on a failing dependency is DEPENDENCY_FAILURE.
- Cite evidence by copying up to 8 complete log lines exactly as they appear.
- If the logs do not support any class, answer UNKNOWN with no evidence.
- Do not estimate a confidence score. The calling system computes it."""

FINDING_TOOL = {
    "name": "report_finding",
    "description": "Report the diagnosis for these logs.",
    "input_schema": {
        "type": "object",
        "properties": {
            "symptom_class": {"type": "string", "enum": SYMPTOM_CLASSES},
            "evidence_lines": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
            "rationale": {"type": "string", "description": "One or two sentences."},
        },
        "required": ["symptom_class", "evidence_lines", "rationale"],
    },
}


def confidence_from_evidence(verified: int) -> float:
    """Same bands as the rule-based LogsAgent: evidence count, not model opinion."""
    if verified >= 5:
        return 0.90
    if verified >= 3:
        return 0.75
    if verified >= 1:
        return 0.60
    return 0.30


class LLMLogsAgent(LogsAgent):
    """Drop-in replacement for LogsAgent. Same weight, same agent_name, same output."""

    def __init__(self, simulate: bool = True, use_llm: bool = False, model: str | None = None):
        super().__init__(simulate=simulate)
        self.use_llm = use_llm
        self.model = model or os.environ.get("APO_LLM_MODEL", DEFAULT_MODEL)
        self.last_evidence: dict = {}

    async def diagnose(self, deployment: str, namespace: str) -> AgentFinding:
        t0 = time.monotonic()
        loop = asyncio.get_event_loop()

        if self.simulate:
            log_text = SIMULATED_LOG_TEXT
        else:
            try:
                proc = await loop.run_in_executor(None, lambda: subprocess.run(
                    ["kubectl", "logs", f"deployment/{deployment}", "-n", namespace, "--since=10m"],
                    capture_output=True, text=True, timeout=10))
                log_text = proc.stdout
            except Exception as e:  # noqa: BLE001
                return self._finding("UNKNOWN", 0.0, {}, t0, error=f"kubectl logs failed: {e}")

        if not self.use_llm:
            return self._rule_based(log_text, t0, reason="rule-based (run with --llm to use a model)")

        try:
            data = await loop.run_in_executor(None, lambda: self._ask_model(deployment, log_text))
        except Exception as e:  # noqa: BLE001  any failure degrades to the rules
            return self._rule_based(log_text, t0, reason=f"model unavailable, fell back to rules: {e}")

        symptom = data.get("symptom_class")
        if symptom not in SYMPTOM_CLASSES:
            return self._rule_based(log_text, t0, reason=f"invalid model output: {symptom!r}")

        # Verify every cited line really exists in the logs. Invented evidence counts for nothing.
        lines = {ln.strip() for ln in log_text.splitlines() if ln.strip()}
        cited = [c.strip() for c in data.get("evidence_lines", []) if isinstance(c, str)]
        verified = [c for c in dict.fromkeys(cited) if c in lines]

        if symptom == "UNKNOWN" or not verified:
            symptom, confidence = "UNKNOWN", 0.30
        else:
            confidence = confidence_from_evidence(len(verified))

        evidence = {
            "source": f"model:{self.model}",
            "cited_lines": len(cited),
            "verified_lines": len(verified),
            "rationale": data.get("rationale", ""),
            "examples": verified[:3],
        }
        return self._finding(symptom, confidence, evidence, t0)

    def _ask_model(self, deployment: str, log_text: str) -> dict:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        import anthropic  # imported here so simulate mode needs no extra package

        client = anthropic.Anthropic(timeout=LLM_TIMEOUT_S)
        resp = client.messages.create(
            model=self.model,
            max_tokens=2000,   # room for the model to reason before it answers
            system=SYSTEM_PROMPT + "\nAlways answer by calling the report_finding tool.",
            tools=[FINDING_TOOL],
            tool_choice={"type": "auto"},   # a forced tool choice is rejected when the model thinks first
            messages=[{"role": "user",
                       "content": f"Logs for {deployment}, last 10 minutes:\n\n{log_text[-12000:]}"}],
        )
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                return dict(block.input)
        types = [getattr(b, "type", "?") for b in resp.content]
        raise RuntimeError(f"model returned no structured finding "
                           f"(stop_reason={getattr(resp, 'stop_reason', None)}, blocks={types})")

    def _rule_based(self, log_text: str, t0: float, reason: str) -> AgentFinding:
        counts: dict[str, int] = {}
        for pattern, symptom in self.LOG_PATTERNS.items():
            c = log_text.count(pattern)
            if c:
                counts[symptom] = counts.get(symptom, 0) + c
        if not counts:
            return self._finding("UNKNOWN", 0.30, {"source": "rules", "patterns_found": 0, "note": reason}, t0)
        best = max(counts, key=lambda k: counts[k])
        n = counts[best]
        conf = 0.90 if n >= 5 else (0.75 if n >= 3 else 0.60)
        return self._finding(best, conf, {"source": "rules", "occurrences": n, "note": reason}, t0)

    def _finding(self, symptom, confidence, evidence, t0, error=None) -> AgentFinding:
        self.last_evidence = dict(evidence, error=error) if error else dict(evidence)
        return AgentFinding(
            agent_name="logs",
            symptom_class=symptom,
            action_id=ACTION_MAP.get(symptom, "escalate_human"),
            confidence=confidence,
            evidence=evidence,
            duration_ms=int((time.monotonic() - t0) * 1000),
            error=error,
        )


async def main(use_llm: bool, real: bool, dry_run: bool, prometheus_url: str) -> None:
    simulate = not real
    print("\n" + "=" * 65)
    print("LAB 3 TAKE-HOME — SWAP THE BRAIN, KEEP THE HARNESS")
    print("=" * 65)
    brain = f"Claude ({os.environ.get('APO_LLM_MODEL', DEFAULT_MODEL)})" if use_llm else "rules"
    print(f"Logs: {'simulated' if simulate else 'kubectl'}   ·   LogsAgent brain: {brain}")

    client = None if simulate else PrometheusClient(prometheus_url)
    logs_agent = LLMLogsAgent(simulate=simulate, use_llm=use_llm)
    orchestrator = Orchestrator(
        MetricsAgent(client=client, simulate=simulate),
        logs_agent,
        ActionAgent(simulate=simulate, dry_run=dry_run),
    )
    if real:
        orchestrator.FAN_OUT_TIMEOUT_S = Orchestrator.REAL_FAN_OUT_TIMEOUT_S
    if use_llm:
        orchestrator.FAN_OUT_TIMEOUT_S = max(orchestrator.FAN_OUT_TIMEOUT_S, LLM_FAN_OUT_TIMEOUT_S)
    print(f"Fan-out timeout: {orchestrator.FAN_OUT_TIMEOUT_S * 1000:.0f}ms per agent\n")

    result = await orchestrator.run("payment-service", "default")
    print("\nLogsAgent evidence:")
    print(json.dumps(logs_agent.last_evidence, indent=2, default=str))
    print("\n" + "─" * 65)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Lab 3 take-home: LogsAgent backed by a Claude model.")
    p.add_argument("--llm", action="store_true", help="read logs with a Claude model (needs ANTHROPIC_API_KEY)")
    p.add_argument("--real", action="store_true", help="use the real cluster instead of simulated data")
    p.add_argument("--dry-run", action="store_true", help="real mode: print kubectl commands without running them")
    p.add_argument("--prometheus-url", default="http://localhost:9090")
    a = p.parse_args()
    asyncio.run(main(a.llm, a.real, a.dry_run, a.prometheus_url))
