# Lab 1 — Your First Confidence Gate

**Duration:** 45 minutes total · 20 min in-class · 25 min take-home  
**Goal:** Implement four safety gates and a confidence scorer — the core decision layer of an Autonomous Platform Operator.

---

## Prerequisites

| Tool | Min version | macOS | Linux | Windows |
|---|---|---|---|---|
| Docker | 24+ | Colima or Docker Desktop | `sudo apt install docker.io` | Docker Desktop |
| k3d | 5.x | `brew install k3d` | see root README | `winget install Rancher.k3d` |
| kubectl | 1.27+ | `brew install kubectl` | see root README | `winget install Kubernetes.kubectl` |
| Python | 3.11+ | [python.org](https://python.org) | `sudo apt install python3` | [python.org](https://python.org) |

Verify:

```bash
docker info && k3d version && kubectl version --client && python3 --version
```

> **macOS + Colima:** `setup.sh` raises inotify limits automatically. Nothing to do manually.

---

## Step 1 — Start the lab environment (one command)

```bash
chmod +x setup.sh
./setup.sh
```

This takes about 2–3 minutes the first time (Docker image build). Subsequent runs are faster.

**What it sets up for you** (you do not need to know the details):
- k3d cluster with 1 control-plane node + 2 worker nodes
- payment-service deployed with 2 replicas and Prometheus scraping
- Port-forwards: `localhost:8080` → payment-service, `localhost:9090` → Prometheus
- Load generator running in the background so metrics are non-zero from the start

When `setup.sh` finishes you will see:

```
════════════════════════════════════════════════════
  Lab 1 environment ready
════════════════════════════════════════════════════

  payment-service  →  http://localhost:8080
  Prometheus       →  http://localhost:9090
```

> If `setup.sh` fails, check the error message — it always says which step failed and what to look at. The most common cause is Docker not running.

---

---

# 🏫 IN-CLASS — 20 minutes

---

## Step 2 — Implement the four safety gates ✏️

Open `gate_starter.py`. Search for the four methods marked `# TODO` inside `GateOrchestrator` — they are the only stubs in the file. Each must return `(bool, reason_str)`: `True` means the gate passes, `False` means it blocks.

```
_cost_gate(proposal)          → (True/False, "reason string")
_blast_radius_gate(proposal)  → (True/False, "reason string")
_reversibility_gate(proposal) → (True/False, "reason string")
_time_gate(proposal)          → (True/False, "reason string")
```

Everything else is already written: `ActionProposal` (the data you receive), `GateResult` (what `evaluate()` returns), `GateConfig` (the threshold constants you check against), the `evaluate()` method that calls your four gates in sequence, and four test cases at the bottom of the file. **Your job is the four `# TODO` methods only.**

Safety gates are binary pass/fail checks that run before any action is committed. All four must pass; a single rejection blocks execution regardless of confidence score.

| Gate | What it prevents | Lab threshold |
|---|---|---|
| Cost | Automation that blows the hourly budget | $500/hr |
| Blast radius | Restarting more than N% of pods at once | 10% of running pods |
| Reversibility | Actions that take longer to undo than your incident SLO | 600 s reversal time |
| Time | Changes during peak traffic hours | Block 08:00–10:00 UTC |

> **🤖 Claude in production:** The thresholds in `GateConfig` are lab constants. In a live system they come from your incident history and approved budgets. A model such as Claude can help with the analysis, for example by proposing a blast-radius limit from past remediations, but a human reviews and commits every threshold. Gates define what is allowed, so neither the gate code nor its numbers are set by a model.

Test your gates:

```bash
python3 gate_starter.py --simulate
# Expected: 4/4 tests passed
```

---

---

# 🏠 TAKE-HOME — 25 minutes

---

## Step 3 — Implement the confidence scorer ✏️

Open `diagnostic_agent_starter.py`. Implement three things:

### A. `detect_symptom_class(metrics)`

Maps raw metric values to a named symptom class. Apply these rules in order:

| Priority | Condition | Symptom class |
|---|---|---|
| 1 | `memory_usage_ratio > 0.85` | `MEMORY_PRESSURE` |
| 2 | `p99_latency_ms > 500` AND `error_rate_ratio > 0.05` | `DB_BOTTLENECK` |
| 3 | `error_rate_ratio > 0.10` | `DEPLOYMENT_REGRESSION` |
| 4 | `restart_count_1h > 5` | `CAPACITY_EXHAUSTION` |
| 5 | else | `UNKNOWN` |

### B. `ConfidenceScorer.compute(metrics, symptom_class, action_id)`

Implement the APO confidence formula: `C = 0.35×S + 0.25×H + 0.25×R + 0.15×F`

| Component | What it measures | Rule |
|---|---|---|
| S — Signal Clarity | How clearly the metric signal indicates a problem | use `metrics["memory_usage_ratio"]` as `mem`: mem > 0.90 → 0.95 · mem > 0.85 → 0.80 · mem > 0.75 → 0.60 · else 0.40 |
| H — Historical Match | How often this action resolved this symptom in the past | look up `(symptom_class, action_id)` in `HISTORICAL_MATCH` |
| R — Remediation Confidence | How reliable this action is in general | look up `action_id` in `REMEDIATION_CONFIDENCE` |
| F — Data Freshness | How recent the Prometheus scrape is | use `metrics["last_scrape_age_seconds"]` as `age`: age < 30 s → 0.95 · < 60 → 0.80 · < 120 → 0.65 · else 0.40 |

The composite score drives the autonomy level:

| C score | Autonomy level | What the agent does |
|---|---|---|
| ≥ 0.90 | L5 | Acts silently |
| ≥ 0.80 | L4 | Acts + notifies |
| ≥ 0.70 | L3 | Acts + requires approval |
| ≥ 0.60 | L2 | Suggests action |
| < 0.60 | L1 | Escalates to human |

> **🤖 Claude in production:** `detect_symptom_class()` is the part a model can usefully help with. Given the raw `metrics` dict, a model such as Claude can propose a symptom class by reasoning over the full picture, catching compound failures (high latency AND elevated restarts AND stale scrape) that a linear rule table misses. `ConfidenceScorer` stays in code. Confidence comes from evidence you can verify, never from the model's own estimate, and the action comes from the catalog, never from the model. Lab 3's take-home shows this pattern.

### C. `PrometheusClient.query()` and `MetricsReader.read()` *(OPTIONAL — real cluster only)*

Skip this if you are working in simulate mode. These two methods wire the agent to live Prometheus data. Leave the stubs as-is — the starter automatically falls back to `SIMULATED_METRICS` when they raise `NotImplementedError`.

> **PROVIDED** — `SIMULATED_METRICS`, `autonomy_level()`, `HISTORICAL_MATCH`, `REMEDIATION_CONFIDENCE`, and `run()` are written. `--simulate` bypasses Prometheus so you can complete the lab without touching the cluster.

Test in simulate mode:

```bash
python3 diagnostic_agent_starter.py --simulate
```

Expected:

```
Symptom class:  MEMORY_PRESSURE
C (Composite):  0.8950
Autonomy level: L4 — act + notify
✅  DECISION: Execute pod.rolling_restart on payment-service
   C=0.8950 → L4 — act + notify
```

---

## Step 4 — Inject a fault and observe *(bonus — real cluster)*

The load generator is already running. Inject a fault, wait one Prometheus scrape interval (30 s), then run the agent against live data.

```bash
# Inject fault
curl -X POST http://localhost:8080/toggle_fault

# Wait ~30 s for Prometheus to scrape the degraded metrics, then:
python3 diagnostic_agent_starter.py --prometheus-url http://localhost:9090

# Turn fault back off
curl -X POST http://localhost:8080/toggle_fault
```

**Windows (PowerShell):**

```powershell
Invoke-WebRequest -Method POST http://localhost:8080/toggle_fault
# Wait ~30 s, then:
python diagnostic_agent_starter.py --prometheus-url http://localhost:9090
Invoke-WebRequest -Method POST http://localhost:8080/toggle_fault
```

> **Note:** `MetricsReader.read()` and `PrometheusClient.query()` are TODO stubs (Step 3C). Until you implement them, the agent falls back to `SIMULATED_METRICS` automatically and prints a notice.

---

## Success Criteria

**In-class:**
- [ ] `gate_starter.py --simulate` reports **4/4 tests passed**
- [ ] Blast radius > 10% blocks the action

**Take-home:**
- [ ] `diagnostic_agent_starter.py --simulate` outputs correct symptom class
- [ ] C score lands between 0.80 and 0.90 for the MEMORY_PRESSURE scenario
- [ ] *(bonus)* After fault injection in real mode, the agent recommends `pod.rolling_restart`

---

## Cleanup

```bash
./teardown.sh
```

---

## Files in this lab

```
lab1/
├── payment-service/              🟢 PROVIDED — do not modify
│   ├── app.py
│   ├── Dockerfile
│   └── deployment.yaml
├── k8s/
│   └── prometheus-rbac.yaml      🟢 PROVIDED — applied by setup.sh
├── prometheus-config.yaml        🟢 PROVIDED — applied by setup.sh
├── setup.sh                      🟢 PROVIDED — run once at the start
├── teardown.sh                   🟢 PROVIDED — run at the end
├── load_generator.py             🟢 PROVIDED — started by setup.sh
├── gate_starter.py               ✏️ IN-CLASS — implement 4 gate methods
├── diagnostic_agent_starter.py   ✏️ TAKE-HOME — implement scorer + symptom detection
├── solution/
│   ├── gate.py                   🟢 Reference — look after you attempt the lab
│   └── diagnostic_agent.py       🟢 Reference — look after you attempt the lab
└── README.md
```
