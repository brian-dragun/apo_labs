# Lab 3 — Multi-Agent Diagnosis and Orchestration

**Duration:** 45 minutes total · 20 min in-class · 25 min take-home  
**Builds on:** Labs 1 and 2  
**Goal:** Extend the single-agent system into a multi-agent architecture where specialist agents diagnose concurrently and an orchestrator resolves conflicts with deterministic arbitration rules.

---

## Prerequisites

Labs 1 and 2 must be complete (all simulate-mode checks pass).  
The Lab 1 cluster must be running, or `setup.sh` will start it for you.

```bash
# Quick verify before running setup.sh:
python3 ../lab1/gate_starter.py --simulate              # 4/4 passed
python3 ../lab1/diagnostic_agent_starter.py --simulate  # DECISION: Execute
python3 ../lab2/autonomous_agent_starter.py --simulate  # Recovered in Ns
```

---

## Step 1 — Start the lab environment

```bash
chmod +x setup.sh
./setup.sh
```

Reuses the Lab 1 cluster. Under 30 seconds if the cluster is already up.

---

## Architecture

```
incident trigger
      │
      ▼
┌─────────────────────────────────────────────┐
│              Orchestrator                    │
│  fan-out (asyncio) → arbitrate → ActionAgent│
└──────┬──────────────────────┬───────────────┘
       │                      │
  ┌────┴─────┐          ┌─────┴────┐
  │ Metrics  │          │  Logs    │
  │  Agent   │          │  Agent   │
  │ (w=1.5)  │          │ (w=1.2)  │
  └──────────┘          └──────────┘
```

**Key design principles:**

- Agents run **concurrently** (asyncio fan-out) — total wall-clock time is the slowest agent, not the sum. At the scale of a production orchestrator, this matters.
- Each agent has a **50ms timeout** — if an agent takes longer, the orchestrator marks it timed out, applies a confidence penalty, and proceeds. A slow metrics backend should not block the incident response.
- Arbitration is **deterministic** — same inputs always produce the same output. Any incident is replayable from its audit log.
- ActionAgent **re-evaluates gates before every execution** — the confidence score and gate checks from Labs 1 and 2 run again.

---

## What you'll implement

| Component | Status | Timing | Description |
|---|---|---|---|
| `MetricsAgent.diagnose()` | ✏️ **YOUR TASK** | In-class | Query Prometheus, score, return finding |
| `LogsAgent.diagnose()` | ✏️ **YOUR TASK** | In-class | Scan kubectl logs, pattern-match, return finding |
| `arbitrate(findings)` | ✏️ **YOUR TASK** | Take-home | Apply 5 deterministic rules, return consensus |
| `Orchestrator.run()` | ✏️ **YOUR TASK** | Take-home | asyncio fan-out + arbitrate + act |
| All dataclasses, constants, `ActionAgent` | 🟢 **PROVIDED** | — | Already in starter |

---

---

# 🏫 IN-CLASS — 20 minutes

---

## Step 2 — Implement `MetricsAgent.diagnose()` ✏️

Open `orchestrator_starter.py`. Search for `# ── TODO: Implement MetricsAgent` (around line 109). Inside the `MetricsAgent` class, fill in the `diagnose()` method — replace the `raise NotImplementedError` line with your implementation.

**Simulate mode (required):**
1. `await asyncio.sleep(0.02)` — simulated latency
2. Return an `AgentFinding` built from `SIMULATED_METRICS_FINDING`

`SIMULATED_METRICS_FINDING` is a dict already defined in the starter. Build the return value like this:
```python
return AgentFinding(
    agent_name="metrics",
    **SIMULATED_METRICS_FINDING,   # unpacks symptom_class, action_id, confidence, evidence, duration_ms
    timed_out=False,
)
```

**Real mode (optional — requires live cluster):**
1. Run `MetricsReader.read()` in an executor (non-blocking)
2. Apply `detect_symptom_class()` and `ConfidenceScorer`
3. Return `AgentFinding(agent_name="metrics", ...)`

```python
loop = asyncio.get_event_loop()
metrics = await loop.run_in_executor(
    None,
    lambda: MetricsReader(self.client, deployment, namespace).read()
)
```

`run_in_executor` is necessary because `MetricsReader.read()` uses the synchronous `requests` library. Calling it directly inside an async function would freeze the event loop and negate the benefit of concurrent fan-out.

> **🤖 Claude in production:** A specialist like MetricsAgent can be backed by a model such as Claude. It receives the raw metrics dict and proposes a symptom class by reasoning about what the numbers mean together: high memory AND elevated restarts AND stale scrape is a different prognosis than any one of those alone. The finding's confidence is still computed in code from evidence that can be checked, and arbitration and gates stay deterministic. The take-home `llm_logs_agent.py` shows this pattern for logs.

---

## Step 3 — Implement `LogsAgent.diagnose()` ✏️

Open `orchestrator_starter.py`. Search for `# ── TODO: Implement LogsAgent` (around line 137). Inside the `LogsAgent` class, fill in the `diagnose()` method — replace the `raise NotImplementedError` line with your implementation.

**Simulate mode (required):**
```python
await asyncio.sleep(0.03)
return AgentFinding(agent_name="logs", **SIMULATED_LOGS_FINDING, timed_out=False)
```

**Real mode:**
1. Run `kubectl logs deployment/{deployment} -n {namespace} --since=10m` via subprocess in an executor
2. Count occurrences of each `LOG_PATTERNS` entry in the output
3. Map the most-frequent pattern to a symptom class
4. Set confidence by occurrence count:

| occurrences | confidence |
|---|---|
| ≥ 5 | 0.90 |
| ≥ 3 | 0.75 |
| ≥ 1 | 0.60 |
| 0   | 0.30 |

> **🤖 Claude in production:** Pattern matching is brittle — it misses novel error messages. Claude does semantic log analysis: given the last 10 minutes of logs, it reasons about whether errors indicate a dependency failure, resource exhaustion, or code regression, without a pre-defined pattern list.

Smoke-test your two agents:

```bash
python3 -c "import asyncio, orchestrator_starter as o; print(asyncio.run(o.MetricsAgent(None, simulate=True).diagnose('payment-service', 'default')))"
python3 -c "import asyncio, orchestrator_starter as o; print(asyncio.run(o.LogsAgent(simulate=True).diagnose('payment-service', 'default')))"
# Expected: AgentFinding(agent_name='metrics', symptom_class='MEMORY_PRESSURE', ... confidence=0.84 ...)
#           AgentFinding(agent_name='logs',    symptom_class='MEMORY_PRESSURE', ... confidence=0.76 ...)
# (Running orchestrator_starter.py itself still says "Implement Orchestrator.run()" until the take-home.)
```

---

---

# 🏠 TAKE-HOME — 25 minutes

---

## Step 4 — Implement `arbitrate(findings)` ✏️

Open `orchestrator_starter.py`. Find the `arbitrate(findings)` function marked `# TODO`. Apply all 5 rules **in order**:

**Rule 1 — Timeout penalty:** For each timed-out agent in `findings`: `confidence -= 0.05`. Append `"timeout_penalty: {agent_name}"` to `rules_applied`.

**Rule 2 — Contradiction penalty:** If MetricsAgent and LogsAgent report *different* `symptom_class` values: subtract 0.15 from each agent's confidence. Append `"contradiction: metrics={metrics_symptom} vs logs={logs_symptom}"` to `rules_applied`.

**Rule 3 — Corroboration bonus:** If MetricsAgent and LogsAgent report the *same* `symptom_class`: add 0.10 to each (cap at 1.0). Append `"corroboration: {symptom_class}"` to `rules_applied`.

> Rules 2 and 3 are mutually exclusive — only one fires per run.

**Rule 4 — Cascade override:** If ANY agent finds `DEPLOYMENT_REGRESSION` with `confidence >= 0.80`: return immediately:
   ```python
   return ArbitrationResult(
       symptom_class="DEPLOYMENT_REGRESSION",
       action_id="config.rollback",
       confidence=finding.confidence,
       rules_applied=rules_applied + ["cascade_override: DEPLOYMENT_REGRESSION"],
       winning_agent=finding.agent_name,
   )
   ```

**Rule 5 — Weighted consensus:**
```
score(symptom) = Σ weight × confidence  for each non-timed-out agent
agreed         = argmax score(symptom)
final_confidence = max confidence among agents for agreed symptom
```
Weights: MetricsAgent = 1.5, LogsAgent = 1.2, all others = 1.0.

> **`AGENT_WEIGHTS` dict and `ACTION_MAP` are already defined in the starter.**

The rules encode trust relationships and safety posture. Contradiction doesn't block action — it reduces confidence, which may push the autonomy level below the execution threshold. Corroboration rewards convergent evidence from two independent sources. The cascade override for `DEPLOYMENT_REGRESSION` short-circuits consensus because rollback risk is lower than continued degradation and the correct action is known.

---

## Step 5 — Implement `Orchestrator.run()` ✏️

Open `orchestrator_starter.py`. Find `Orchestrator.run()` marked `# TODO`. Implement it using the fan-out pattern:

```python
async def safe(coro, name):
    try:
        return await asyncio.wait_for(coro, timeout=self.FAN_OUT_TIMEOUT_S)
    except asyncio.TimeoutError:
        return AgentFinding(agent_name=name, timed_out=True, confidence=0.0,
                            symptom_class="UNKNOWN", action_id="escalate_human",
                            evidence={}, duration_ms=50)

findings = list(await asyncio.gather(
    safe(self.metrics_agent.diagnose(deployment, namespace), "metrics"),
    safe(self.logs_agent.diagnose(deployment, namespace),    "logs"),
))
```

Then:
```python
result = arbitrate(findings)
await self.action_agent.act(
    action_id=result.action_id,
    deployment=deployment,
    namespace=namespace,
    symptom_class=result.symptom_class,
    confidence_score=result.confidence,
)
```

> `Orchestrator.FAN_OUT_TIMEOUT_S = 0.050` (50 ms in simulate mode; `main()` raises it to `REAL_FAN_OUT_TIMEOUT_S = 10.0` in real mode, because Prometheus queries and `kubectl logs` take seconds), the fully wired `ActionAgent` (it runs the command for the arbitrated action and escalates if there is no executor for it), and `main()` are all in the starter. Keep simulated agent latencies well under 50 ms: an agent that sleeps 50 ms against a 50 ms timeout times out on almost every run.

---

## Step 6 — Run the full pipeline

```bash
python3 orchestrator_starter.py --simulate
```

**Windows:**

```cmd
python orchestrator_starter.py --simulate
```

Expected:

```
✅  metrics    → MEMORY_PRESSURE           C=0.84 (22ms)
✅  logs       → MEMORY_PRESSURE           C=0.76 (31ms)
Arbitration rules: ['corroboration: MEMORY_PRESSURE', 'weighted_consensus: MEMORY_PRESSURE (score=2.4420)']
Consensus:  MEMORY_PRESSURE  →  pod.rolling_restart  C=0.9400
Action: EXECUTED (total wall time ~31ms)
```

---

## Step 7 — Test arbitration edge cases

**Contradiction:** Temporarily set `SIMULATED_LOGS_FINDING` to `symptom_class="DB_BOTTLENECK"`. Rerun — the contradiction penalty should fire. Notice how the confidence drop changes the autonomy decision.

**Cascade override:** Set `SIMULATED_METRICS_FINDING` to `symptom_class="DEPLOYMENT_REGRESSION"` and `confidence=0.96`. (The contradiction penalty runs first and takes 0.15 off, so the finding needs to start above 0.95 to still clear the 0.80 cascade threshold.) Verify `config.rollback` is returned regardless of the logs agent.

**Timeout:** Add `await asyncio.sleep(0.1)` inside `MetricsAgent.diagnose()`. Verify the agent returns `timed_out=True`, the timeout penalty fires, and the pipeline still completes on time using only the logs agent.

---

## Success Criteria

**In-class:**
- [ ] Both agents return valid `AgentFinding` objects in simulate mode
- [ ] The two smoke-test commands in Step 3 print an `AgentFinding` for each agent — not an import error

**Take-home:**
- [ ] Full pipeline runs in simulate mode in < 200 ms
- [ ] Corroboration bonus appears in `rules_applied` when agents agree
- [ ] Contradiction penalty appears when agents disagree
- [ ] Cascade override fires for `DEPLOYMENT_REGRESSION` + C ≥ 0.80
- [ ] A timed-out agent returns `timed_out=True` and doesn't block the pipeline

---

## Take-Home: Swap the Brain, Keep the Harness (optional, uses the Claude API)

The agents in this lab use rules: pattern lists and score bands. That is deliberate. It keeps every decision visible and the safety logic deterministic. In a production APO system, the diagnosis step is where a language model plugs in, and this exercise shows that nothing else has to change when it does.

`llm_logs_agent.py` replaces the rule-based `LogsAgent` with one that asks a Claude model to read the logs. It returns the same `AgentFinding`, with the same name and weight, to the same orchestrator, arbitration rules and gates. **The model is the replaceable part. The harness is what keeps you safe.**

Three rules the model-backed agent follows:

1. **The model names a symptom class and cites log lines. It does not set the confidence.** Code checks that each cited line really exists in the logs and sets confidence from the number of verified lines, using the same bands as the rule-based agent. Invented evidence counts for nothing.
2. **Any failure falls back to the rules.** No API key, a network error, a timeout or invalid output all degrade to today's behavior, so the pipeline never breaks.
3. **Simulate mode is the default.** No API call is made unless you pass `--llm`.

### Setup (only for `--llm`)

```bash
pip install anthropic
export ANTHROPIC_API_KEY=...            # your own key
export APO_LLM_MODEL=claude-haiku-5-5   # optional; a small, fast model suits diagnosis
```

A handful of runs with a small model costs a few cents.

### Steps

**1. Run it with rules (no API call).**

```bash
python3 lab3/llm_logs_agent.py
```

The simulated logs describe a payment-service whose threads are stuck waiting on a failing `order-service`. None of the lines contains a phrase in `LogsAgent.LOG_PATTERNS`, so the rule-based agent reports `UNKNOWN` at 0.30.

**2. Run the same logs through the model.**

```bash
python3 lab3/llm_logs_agent.py --llm
```

The logs agent should now report `DEPENDENCY_FAILURE`. Look at `LogsAgent evidence` in the output: `cited_lines` is what the model claimed, and `verified_lines` is what the code confirmed. The confidence comes from `verified_lines`, not from the model.

**3. Notice what the harness still decides.** MetricsAgent still says `MEMORY_PRESSURE`. The contradiction rule lowers both scores, and the weighted consensus still picks the metrics finding, so the pods are restarted. That is the right behavior for these rules, and the wrong fix for this incident. Add a rule to `arbitrate()` (in `solution/orchestrator.py`, or your own copy) that lets a root-cause finding beat a symptom finding: for example, a `DEPENDENCY_FAILURE` finding at 0.70 or more wins. Run step 2 again. The catalog has no circuit-breaker action, so ActionAgent now escalates to a human instead of restarting the wrong service.

**4. Break the model on purpose.** Run `--llm` with the key unset, or set `APO_LLM_MODEL` to a name that doesn't exist. The agent falls back to the rules, says why in its evidence, and the pipeline finishes.

**5. Measure the cost in time.** Compare the logs agent's `duration_ms` with rules and with the model. Set it against the 222 ms decision budget from Module 5. This is why the fan-out timeout is raised to 15 seconds when `--llm` is on, and why production systems use small, fast models for diagnosis.

### What to take away

You changed the part that reasons, and the confidence score, the gates and the arbitration rules kept working unchanged. A better model improves the diagnosis. It never gets to skip the checks.

---

## Advanced Exercises

**A. Circuit breaker:** Add a `CircuitBreaker` class that blocks an agent if it has timed out more than 3 times in the last 5 minutes.

**B. EventAgent:** Add an `EventAgent` (weight 1.0) that queries `kubectl get events` for `Warning` events and includes it in the fan-out.

**C. Training dataset:** After each `RECOVERED` outcome, write `{metrics, symptom_class, action_id, outcome, mttr_seconds}` to `training_data.jsonl` — the seed dataset for a future model that learns which actions work best for which symptom signatures.

---

## Cleanup

```bash
./teardown.sh               # stops port-forwards and load generator
../lab1/teardown.sh         # deletes the cluster (run only after all labs are done)
```

---

## Files in this lab

```
lab3/
├── setup.sh                   🟢 PROVIDED — run once at the start
├── teardown.sh                🟢 PROVIDED — run when done
├── orchestrator_starter.py    ✏️ IN-CLASS (Steps 2–3) + TAKE-HOME (Steps 4–5)
├── llm_logs_agent.py          🏠 OPTIONAL TAKE-HOME — LogsAgent backed by a Claude model
├── solution/
│   └── orchestrator.py        🟢 Reference — look after you attempt the lab
└── README.md
```
