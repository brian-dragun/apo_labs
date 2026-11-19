# Lab 2 — Single-Agent Autonomous Remediation

**Duration:** 45 minutes total · 20 min in-class · 25 min take-home  
**Builds on:** Lab 1 (gate.py + diagnostic_agent.py)  
**Goal:** Extend the diagnostic agent into a fully autonomous remediation loop: detect → diagnose → gate → **execute → verify → audit**

---

## Prerequisites

Lab 1 must be complete (gate + diagnostic agent both pass in simulate mode).  
The Lab 1 cluster must be running, or `setup.sh` will start it for you.

---

## Step 1 — Start the lab environment

```bash
chmod +x setup.sh
./setup.sh
```

This reuses the Lab 1 cluster. If Lab 1's cluster is not running, `setup.sh` starts it. If port-forwards have died, it restores them. Takes under 30 seconds if the cluster is already up.

---

## What you'll add in this lab

| Component | Status | Timing | What it does |
|---|---|---|---|
| `load_action_catalog()` | 🟢 **PROVIDED** | — | Reads and parses the YAML action registry |
| `ActionExecutor.execute()` | ✏️ **YOUR TASK** | In-class | Runs `kubectl rollout restart` via subprocess |
| `VerificationLoop.wait_for_recovery()` | ✏️ **YOUR TASK** | Take-home | Polls P90 latency until it drops to 1.5× baseline |
| `AuditLogger.append()` | ✏️ **YOUR TASK** | Take-home | Appends a full JSONL record per run |
| `action_catalog.yaml` | 🟢 **PROVIDED** | — | Registry of approved actions with risk metadata |
| `run()` orchestration | 🟢 **PROVIDED** | — | Chains all components in order |

---

---

# 🏫 IN-CLASS — 20 minutes

---

## Step 2 — Inspect the action catalog *(read only)*

Open `action_catalog.yaml`. Each action specifies `risk_class`, `blast_radius_max`, `reversal_time_seconds`, `required_confidence`, `max_executions_per_hour`, `cooldown_seconds`, and `symptom_classes`. This is what APO agents consult before proposing an action — it is the boundary of what the agent is authorized to do.

> **🤖 Claude in production:** Claude doesn't read a static YAML file — it queries a live action catalog service via API. When no approved action fits a novel symptom, Claude can draft a new candidate action and submit it for human review before it enters the catalog.

---

## Step 3 — Implement `ActionExecutor.execute()` ✏️

Open `autonomous_agent_starter.py`. Find the `ActionExecutor` class and its `execute()` method marked `# TODO`. Implement it:

1. Inside `execute()`, as the first line: `start = time.monotonic()`
2. If `self.simulate` is `True` → return this dict immediately, no kubectl:
   ```python
   {"action_id": action_id, "deployment": deployment, "namespace": namespace,
    "executed_at": datetime.now(timezone.utc).isoformat(),
    "dry_run": False, "simulate": True, "success": True,
    "output": f"[SIMULATE] {action_id} on deployment/{deployment} -n {namespace}",
    "error": "", "duration_ms": 0}
   ```
   If `self.dry_run` is `True` → build `cmd` first (step 3), then return a similar dict with `"dry_run": True, "simulate": False` and `"output": "[DRY RUN] " + " ".join(cmd)` — no kubectl.
3. Look up the right command:

```python
COMMANDS = {
    "pod.rolling_restart": ["kubectl", "rollout", "restart", f"deployment/{deployment}", "-n", namespace],
    "db.connection_reset": ["kubectl", "rollout", "restart", f"deployment/{deployment}", "-n", namespace],
    "config.rollback":     ["kubectl", "rollout", "undo",    f"deployment/{deployment}", "-n", namespace],
}
```
   Then look up: `cmd = COMMANDS[action_id]`

4. `proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)`
5. `duration_ms = int((time.monotonic() - start) * 1000)`  and  `success = proc.returncode == 0`
6. Return a dict with these keys: `action_id`, `deployment`, `namespace`, `executed_at` (ISO-8601), `dry_run`, `simulate`, `success` (bool), `output` (stdout), `error` (stderr), `duration_ms` (int) — the docstring in the starter lists the same keys.

The `dry_run` flag lets you test the full decision path without restarting any pods — useful for validating gate logic during an incident.

> **🤖 Claude in production:** Claude selects the execution method based on the target — a Kubernetes workload gets kubectl, a VM-based service gets an Ansible playbook, a managed cloud service gets an API call. The `ActionExecutor` abstraction maps to this.

Test:

```bash
python3 autonomous_agent_starter.py --simulate --dry-run
# Expected: ✅  [DRY RUN] Executed pod.rolling_restart
```

---

---

# 🏠 TAKE-HOME — 25 minutes

---

## Step 4 — Implement `VerificationLoop.wait_for_recovery()` ✏️

Open `autonomous_agent_starter.py`. Find `VerificationLoop.wait_for_recovery()` marked `# TODO`. Implement it in two parts:

**Part A — Simulate mode (do this first):**
```python
if self.simulate:
    time.sleep(self.poll_interval)
    p90 = SIMULATED_POST_RESTART["p99_latency_ms"]
    threshold_ms = baseline_p90_ms * 1.5
    return {
        "recovered": p90 < threshold_ms,
        "final_p90_ms": p90,
        "threshold_ms": threshold_ms,
        "elapsed_seconds": float(self.poll_interval),
        "polls": 1,
    }
```

Implementing Part A alone lets you run `--simulate` end to end immediately.

**Part B — Real mode polling loop:**

Success criterion: P90 latency drops below 1.5× the pre-action baseline within 120 seconds.

PromQL:
```
histogram_quantile(0.90,
  rate(http_request_duration_seconds_bucket{deployment="payment-service"}[1m])
) * 1000
```

```python
threshold_ms = baseline_p90_ms * 1.5
start = time.monotonic()
polls, p90 = 0, threshold_ms + 1
while time.monotonic() - start < self.timeout:
    p90 = self.client.query("histogram_quantile(...)")  # float ms
    polls += 1
    if p90 < threshold_ms:
        return {"recovered": True, "final_p90_ms": p90, "threshold_ms": threshold_ms,
                "elapsed_seconds": time.monotonic() - start, "polls": polls}
    time.sleep(self.poll_interval)
return {"recovered": False, "final_p90_ms": p90, "threshold_ms": threshold_ms,
        "elapsed_seconds": time.monotonic() - start, "polls": polls}
```

> **🤖 Claude in production:** Claude would derive the 1.5× threshold dynamically from the deployment's historical p90 distribution — tight enough to catch a failed recovery, not so tight it false-positives on normal traffic jitter.

---

## Step 5 — Implement `AuditLogger.append()` ✏️

Open `autonomous_agent_starter.py`. Find `AuditLogger.append()` marked `# TODO`. It receives a `record` dict already assembled by `_write_audit()` — you don't build the dict. Your implementation is two lines:

```python
with open(self.path, "a") as f:
    f.write(json.dumps(record) + "\n")
```

`"a"` mode appends without overwriting previous runs. `"\n"` makes each run a separate line (JSONL format).

Every autonomous decision — including no-ops, blocked proposals, and failed verifications — produces an audit record. JSONL is crash-safe (a kill mid-write corrupts at most the current line), directly parseable by both humans and downstream tools, and usable as a training dataset for future models.

---

## Step 6 — Run the full loop

```bash
# Simulate mode — no cluster needed
python3 autonomous_agent_starter.py --simulate

# Against live cluster
python3 autonomous_agent_starter.py

# Inject a fault first, then run
curl -X POST http://localhost:8080/toggle_fault
python3 autonomous_agent_starter.py
```

**Windows (PowerShell):**

```powershell
python autonomous_agent_starter.py --simulate
Invoke-WebRequest -Method POST http://localhost:8080/toggle_fault
python autonomous_agent_starter.py
```

---

## Step 7 — Inspect the audit log

```bash
cat apo_audit.jsonl | python3 -m json.tool

python3 -c "
import json
for line in open('apo_audit.jsonl'):
    r = json.loads(line)
    v = r.get('verification') or {}
    print(r['run_id'], r['outcome'], 'MTTR=', v.get('elapsed_seconds','n/a'))
"
```

---

## Blast-radius gate test

Set `blast_radius_fraction=0.15` on the `ActionProposal` in `run()` and re-run with `--dry-run`. Confirm the gate blocks before the executor runs.

---

## Success Criteria

**In-class:**
- [ ] `--simulate --dry-run` completes without errors and shows `[DRY RUN] Executed pod.rolling_restart`
- [ ] Gate blocks a 15% blast-radius proposal before the executor runs

**Take-home:**
- [ ] `--simulate` completes with `✅  Recovered in <N>s`
- [ ] `apo_audit.jsonl` contains a valid JSONL record with all fields
- [ ] MTTR < 120 seconds in simulate mode
- [ ] `--dry-run` skips kubectl but still writes an audit record

---

## Optional Take-Home: Explain the Decision (any model)

Your loop now makes a decision, acts, verifies and writes an audit record. The last step in a real system is telling a person what happened: the "act and notify" message at L4, or the recommendation an engineer approves at L2. Writing that message is one of the jobs a language model is genuinely good at.

`explain_decision.py` reads the last record in `apo_audit.jsonl` and produces that message. Two rules make it safe:

1. **The model explains a decision that has already been made.** The record is written before the model sees it, so nothing it writes can change what happened.
2. **Code checks the explanation before anyone reads it.** Every number and every action or symptom name in the text must appear in the record. If the model adds anything that isn't there, its text is rejected and a plain template built from the record is used instead.

This is the same discipline as the rest of the course: the model is useful, the harness decides what you trust.

### Setup (only for `--llm`)

The script works with Claude, with any OpenAI-compatible API, or with a local model. It uses `requests`, which you already installed.

```bash
# Claude (default)
export ANTHROPIC_API_KEY=...
export APO_LLM_MODEL=claude-haiku-5-5        # optional

# Any OpenAI-compatible API
export APO_LLM_PROVIDER=openai
export APO_LLM_BASE_URL=https://api.example.com/v1
export APO_LLM_API_KEY=...
export APO_LLM_MODEL=<model name>

# A local model, no key and no cost (Ollama)
export APO_LLM_PROVIDER=openai
export APO_LLM_BASE_URL=http://localhost:11434/v1
export APO_LLM_MODEL=llama3.2
```

### Steps

**1. Create a record.** Run the loop once so there's something to explain:

```bash
python3 lab2/solution/autonomous_agent.py --simulate   # or your own version
```

**2. Get the template message (no model call).**

```bash
python3 lab2/explain_decision.py
```

This is the fallback: correct, complete and a little stiff.

**3. Let a model write it.**

```bash
python3 lab2/explain_decision.py --llm --show-rejected
```

If the grounding check passes, you'll see the model's version. If it fails, you'll see which claims weren't in the record, the rejected draft and the template in its place.

**4. Watch the check catch something.** Models often add plausible details: a percentage of traffic, a pod count, a cause. The record from Step 1 has no blast-radius field, so a draft that says "affected 5% of traffic" is rejected, even though 5% is the value the agent used. Ask yourself: should that fact be in the audit record? (It should. Adding the gate inputs to `_build_record()` is a good follow-up.)

**5. Try a blocked decision.** Edit `GateConfig` in `lab1/solution/gate.py` so the gate blocks the restart (for example, set `blast_radius_max` to 0.01), run Step 1 again and explain the new record. Then restore the limit.

**6. Compare models.** Run Step 3 with two different models, including a small local one. Which ones pass the check most often? The check, not the model, decides what reaches the engineer.

### What to take away

A model can turn a dense record into a clear message in seconds. It can also state things that didn't happen, fluently. Checking its output against the record, and falling back to a template when the check fails, is what makes the message safe to send.

---

## Cleanup

```bash
./teardown.sh               # stops port-forwards and load generator
../lab1/teardown.sh         # deletes the cluster (run only after all labs are done)
```

---

## Files in this lab

```
lab2/
├── action_catalog.yaml            🟢 PROVIDED — do not modify
├── setup.sh                       🟢 PROVIDED — run once at the start
├── teardown.sh                    🟢 PROVIDED — run when done
├── autonomous_agent_starter.py    ✏️ IN-CLASS (Step 3) + TAKE-HOME (Steps 4–5)
├── explain_decision.py            🏠 OPTIONAL TAKE-HOME — model-written message, checked against the record
├── solution/
│   └── autonomous_agent.py        🟢 Reference — look after you attempt the lab
└── README.md
```
