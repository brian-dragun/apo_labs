"""
Lab 1 — gate.py  (SOLUTION)
============================
Complete implementation of the four confidence gates.

Do not share this file with students before the lab exercise.
"""

from __future__ import annotations
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ActionProposal:
    action_id: str
    deployment: str
    namespace: str
    estimated_cost_per_hour_usd: float
    blast_radius_fraction: float
    reversal_time_seconds: int
    symptom_class: str
    confidence_score: float


@dataclass
class GateResult:
    approved: bool
    blocked_by: list[str] = field(default_factory=list)
    reasons: list[str]    = field(default_factory=list)

    def __str__(self) -> str:
        if self.approved:
            return "✅  GateResult(approved=True)"
        return f"🚫  GateResult(approved=False, blocked_by={self.blocked_by})"


@dataclass
class GateConfig:
    cost_per_hour_usd_max: float = 500.0
    blast_radius_max: float      = 0.10
    reversal_time_sec_max: int   = 600
    peak_hours_utc: tuple        = (8, 10)


class GateOrchestrator:

    def __init__(self, config: GateConfig | None = None):
        self.config = config or GateConfig()

    def evaluate(self, proposal: ActionProposal) -> GateResult:
        blocked_by: list[str] = []
        reasons: list[str]    = []

        gates = [
            ("cost_gate",        self._cost_gate),
            ("blast_radius_gate", self._blast_radius_gate),
            ("reversibility_gate", self._reversibility_gate),
            ("time_gate",         self._time_gate),
        ]

        for name, gate_fn in gates:
            passed, reason = gate_fn(proposal)
            if not passed:
                blocked_by.append(name)
                reasons.append(reason)

        return GateResult(
            approved=len(blocked_by) == 0,
            blocked_by=blocked_by,
            reasons=reasons,
        )

    def _cost_gate(self, proposal: ActionProposal) -> tuple[bool, str]:
        limit = self.config.cost_per_hour_usd_max
        actual = proposal.estimated_cost_per_hour_usd
        if actual <= limit:
            return True, ""
        return (
            False,
            f"cost ${actual:.0f}/hr exceeds limit ${limit:.0f}/hr"
        )

    def _blast_radius_gate(self, proposal: ActionProposal) -> tuple[bool, str]:
        limit  = self.config.blast_radius_max
        actual = proposal.blast_radius_fraction
        if actual <= limit:
            return True, ""
        return (
            False,
            f"blast radius {actual:.0%} exceeds limit {limit:.0%}"
        )

    def _reversibility_gate(self, proposal: ActionProposal) -> tuple[bool, str]:
        limit  = self.config.reversal_time_sec_max
        actual = proposal.reversal_time_seconds
        if actual <= limit:
            return True, ""
        return (
            False,
            f"reversal time {actual}s exceeds limit {limit}s"
        )

    def _time_gate(self, proposal: ActionProposal) -> tuple[bool, str]:
        start_h, end_h = self.config.peak_hours_utc
        current_h = datetime.now(timezone.utc).hour
        if start_h <= current_h < end_h:
            return (
                False,
                f"action blocked during peak hours "
                f"({start_h:02d}:00–{end_h:02d}:00 UTC), current hour={current_h:02d}"
            )
        return True, ""


# ── Test harness (same as starter) ───────────────────────────────────────────

def _run_tests(config: GateConfig) -> None:
    orchestrator = GateOrchestrator(config)

    test_cases = [
        (
            "Normal pod restart — should PASS all gates",
            ActionProposal("pod.rolling_restart", "payment-service", "default",
                           50.0, 0.05, 120, "MEMORY_PRESSURE", 0.84),
            True,
        ),
        (
            "Cost gate FAIL — $1200/hr exceeds $500 limit",
            ActionProposal("scale.horizontal_add", "payment-service", "default",
                           1200.0, 0.05, 120, "CAPACITY_EXHAUSTION", 0.78),
            False,
        ),
        (
            "Blast radius FAIL — 15% > 10% lab limit",
            ActionProposal("pod.rolling_restart", "payment-service", "default",
                           50.0, 0.15, 120, "MEMORY_PRESSURE", 0.84),
            False,
        ),
        (
            "Reversibility FAIL — 900s > 600s limit",
            ActionProposal("config.rollback", "payment-service", "default",
                           50.0, 0.05, 900, "DEPLOYMENT_REGRESSION", 0.76),
            False,
        ),
    ]

    print("\n" + "=" * 65)
    print("LAB 1 — GATE TESTS (SOLUTION)")
    print("=" * 65)

    passed = 0
    for desc, proposal, expected_approve in test_cases:
        result = orchestrator.evaluate(proposal)
        ok = result.approved == expected_approve
        status = "✅ PASS" if ok else "❌ FAIL"
        if ok:
            passed += 1
        print(f"\n{status}  {desc}")
        print(f"       {result}")
        for r in result.reasons:
            print(f"       reason: {r}")

    print("\n" + "─" * 65)
    print(f"Results: {passed}/{len(test_cases)} tests passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args()
    _run_tests(GateConfig())
