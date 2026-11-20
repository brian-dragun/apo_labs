# APO Workshop Labs — Building Autonomous Operations

**Workshop:** Building Autonomous Operations: Agents for Production Safety at Speed  
**Repo:** `github.com/achankra/apo_labs`

---

## Overview

Three hands-on labs that build a full **detect → diagnose → gate → execute → verify → audit** platform, one layer at a time.

| Lab | Title | Key deliverable |
|---|---|---|
| Lab 1 | Your First Confidence Gate | Confidence scorer + 4 safety gates |
| Lab 2 | Single-Agent Autonomous Remediation | Execution loop + verification + JSONL audit |
| Lab 3 | Multi-Agent Diagnosis and Orchestration | asyncio fan-out + arbitration rules |

APO treats platform decisions the way a control system treats actuator commands: every proposed action earns a confidence score, passes a set of safety gates, executes, verifies that it worked, and writes a tamper-evident audit record. These three labs build that pipeline layer by layer — starting with the gates (Lab 1), adding the execution and verification loop (Lab 2), and distributing the diagnosis across specialist agents that run concurrently (Lab 3).

Each lab has a **simulate mode** (`--simulate`) that requires no cluster, no Prometheus, and no Claude API spend. The workshop runs every lab in simulate mode. Real-cluster mode is optional, for after the workshop; see [Why run a cluster?](#why-run-a-cluster-optional).

---

## Prerequisites

### Required (all modes)

| Tool | Min version | macOS | Linux | Windows |
|---|---|---|---|---|
| Python | 3.11+ | [python.org](https://python.org) | `sudo apt install python3` | [python.org](https://python.org) |
| Git | any | [git-scm.com](https://git-scm.com) | `sudo apt install git` | [git-scm.com](https://git-scm.com) |

### Why run a cluster? (optional)

All three labs run in simulate mode during the workshop. You don't need a cluster to complete them. Simulate mode teaches the logic: the gates, the confidence score, the agents and the arbitration rules all behave exactly as they do against a live system.

A real cluster shows that logic meeting a real system. On your own time afterwards, it gives you:

- **Live telemetry.** Signal strength (S) and freshness (F) come from real Prometheus readings, so you can inject load or memory pressure and watch the confidence score move.
- **Real actions.** `kubectl rollout restart` actually restarts pods, and an action the gate blocks really doesn't happen.
- **The infrastructure time.** Verification waits for real pods to come back, typically 90–120 seconds. In simulate mode this is instant. This wait is Kubernetes, not APO, and it's most of the two minutes an autonomous fix takes.
- **Real failure modes.** A Prometheus query that times out, a stale scrape, an agent that misses its deadline. These are the situations the gates and escalation paths exist for, and the reason real mode uses a 10-second fan-out timeout instead of 50 ms.

Expect 10–15 minutes of setup on a fresh machine, plus about 8 GB of RAM and 4 CPUs for Docker. Some company-managed laptops can't run Docker; if yours can't, stay with simulate mode.

### Required for real-cluster mode

> **Optional, for after the workshop.** Cluster setup takes 10–15 minutes on a fresh machine.

The real-cluster mode runs a full Kubernetes environment on your laptop using Docker as the container runtime and k3d to manage the cluster lifecycle. The setup below installs that stack from the bottom up: container runtime first, then the Kubernetes layer on top.

#### 1. Container runtime

The container runtime is what builds and runs Docker images. k3d uses it to spin up and tear down Kubernetes node containers.

**macOS** — Colima (lighter, no license requirement):

Colima runs Docker inside a lightweight Lima VM. It is functionally equivalent to Docker Desktop for this workshop but has no license restrictions, starts faster on cold boots, and uses less memory at idle. If you already have Docker Desktop installed and working, you can skip this.

```bash
brew install colima docker docker-compose
colima start --cpu 4 --memory 8 --disk 60
```

**macOS** — Or Docker Desktop: download from [docker.com](https://docker.com) and allocate 8 GB RAM / 4 CPUs in Preferences → Resources.

**Linux**:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose
sudo systemctl start docker
sudo usermod -aG docker $USER   # then log out and back in
```

**Windows**: Download [Docker Desktop for Windows](https://docker.com) and enable WSL 2 integration in Settings → Resources → WSL Integration.

Confirm the daemon is running on all platforms before continuing:

```bash
docker info
```

#### 2. k3d and kubectl

k3d (k3s-in-Docker) packages a minimal Kubernetes distribution inside Docker containers, giving you a fully functional multi-node cluster on a single laptop with no cloud account required. kubectl is the standard CLI for every Kubernetes operation — deploying workloads, inspecting pod state, running port-forwards that map cluster services to localhost, and tailing logs during fault injection.

**macOS**:

```bash
brew install k3d kubectl
```

**Linux**:

```bash
curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash
curl -LO "https://dl.k8s.io/release/$(curl -sL https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"
chmod +x kubectl && sudo mv kubectl /usr/local/bin/
```

**Windows** (run in PowerShell as Administrator):

```powershell
winget install Rancher.k3d
winget install Kubernetes.kubectl
```

Verify both are on your PATH before moving on:

```bash
k3d version
kubectl version --client
```

---

## Tools used

### CLI and infrastructure

| Tool | Purpose | Used in | Version |
|---|---|---|---|
| Python | Runtime for all lab scripts and the payment-service | All labs | 3.11+ |
| pip3 / pip | Python package installer | Setup | — |
| Git | Clone repo, commit lab solutions | Setup, all labs | any |
| GitHub | Hosts the upstream repo; fork target for each attendee | Setup | — |
| Docker | Builds container images; provides the runtime backend for k3d | Real-cluster mode | 24+ recommended |
| Colima | Lightweight Docker VM for macOS — no license, no Desktop app required | macOS real-cluster setup | latest |
| k3d | Runs a minimal k3s Kubernetes cluster inside Docker containers | Real-cluster mode | latest |
| kubectl | Kubernetes CLI — deploy workloads, inspect nodes, run port-forwards | Real-cluster mode | 1.28+ |
| Prometheus | Time-series metrics store; queried via PromQL for confidence scoring | Lab 1 & 2 real mode | 2.x |

### Python packages

| Package | Purpose | Used in | Min version |
|---|---|---|---|
| fastapi | Runs the payment-service target that generates latency and error rate metrics | Lab 1 — payment-service | 0.110 |
| uvicorn | ASGI server for the FastAPI payment-service | Lab 1 — payment-service | 0.29 |
| prometheus-client | Exposes a `/metrics` endpoint on the payment-service for Prometheus to scrape | Lab 1 — payment-service | 0.20 |
| requests | HTTP client for querying the Prometheus API and the payment-service health endpoint | Labs 1, 2, 3 | 2.31 |
| pyyaml | Parses `action_catalog.yaml` — the remediation action registry | Lab 2 | 6.0.1 |
| asyncio | Async event loop for concurrent multi-agent fan-out | Lab 3 | built-in (3.11+) |

---

## Fork & clone

1. Fork on GitHub: **github.com/achankra/apo_labs → Fork → Create fork**
2. Clone your fork and install dependencies:

```bash
git clone https://github.com/YOUR_USERNAME/apo_labs.git
cd apo_labs
pip3 install -r requirements.txt
```

> **Windows:** use `pip` instead of `pip3` if `pip3` is not on your PATH.

---

## ✅ Workshop Readiness Checkpoint

Run these before the workshop. Each script runs in simulate mode, without a cluster or any API calls. The starters are deliberately incomplete — you fill them in during the labs — so the expected result is a "not implemented" message, **not** a Python error. If you see the messages below and no `ImportError`, `ModuleNotFoundError` or `SyntaxError`, your environment is ready.

**macOS / Linux:**

```bash
python3 lab1/gate_starter.py --simulate              # expect: Results: 0/4 tests passed (the 4 gates are TODOs)
python3 lab1/diagnostic_agent_starter.py --simulate  # expect: detect_symptom_class() not yet implemented
python3 lab2/autonomous_agent_starter.py --simulate  # expect: ActionExecutor.execute() not yet implemented — skipping execution
python3 lab3/orchestrator_starter.py --simulate      # expect: Not implemented: Implement Orchestrator.run()
```

**Windows:**

```cmd
python lab1\gate_starter.py --simulate
python lab1\diagnostic_agent_starter.py --simulate
python lab2\autonomous_agent_starter.py --simulate
python lab3\orchestrator_starter.py --simulate
```

To see what the finished pipeline looks like, run the reference solutions:

```bash
python3 lab1/solution/gate.py --simulate               # expect: Results: 4/4 tests passed
python3 lab1/solution/diagnostic_agent.py --simulate   # expect: ✅  DECISION: Execute pod.rolling_restart
python3 lab2/solution/autonomous_agent.py --simulate   # expect: ✅  Recovered in 10s
python3 lab3/solution/orchestrator.py --simulate       # expect: Action: EXECUTED
```

> The Lab 1 time gate blocks actions between 08:00 and 10:00 UTC. If you run the solutions in that window, the time gate correctly blocks them — run again outside it.

All four starters showing their "not implemented" message with no Python errors = ready. Email ajay@platformetrics.com if any step fails.

---

## Cluster setup (real mode)

The lab cluster hosts three components that the APO agent talks to: the payment-service (a FastAPI app that generates realistic latency and error metrics), Prometheus (which scrapes those metrics on a 15-second interval), and your APO agent code (running locally and reaching both over port-forwards). Setting up in this order — cluster, then application, then metrics — mirrors how you'd onboard a new service to production monitoring.

### Quick setup (recommended) — one command

From the repo root (Linux, WSL2 or macOS):

```bash
bash cluster-setup.sh        # create/resume cluster, deploy everything, start port-forwards + load generator, run the 6 checks
bash cluster-teardown.sh --keep   # done for today: stop background processes, pause the cluster
bash cluster-teardown.sh          # done for good: stop everything and delete the cluster
```

`cluster-setup.sh` is safe to re-run (it reuses the cluster, image and `.venv`), runs port-forwards and the load generator in the background (logs in `lab1/.lab_logs/`), and ends with the same six checks as [Verify cluster is ready](#verify-cluster-is-ready). Re-run it after a reboot or `--keep` to bring everything back. The manual steps below do the same thing by hand.

### Manual setup

**macOS** — start Colima first if not already running:

```bash
colima start
```

All platforms:

```bash
# Create the lab cluster.
# --agents 2 gives you 1 control-plane node + 2 worker nodes.
# The agent count matters because the blast-radius gate in Lab 1
# calculates the fraction of running pods that an action would touch —
# that fraction changes with the number of scheduled nodes.
k3d cluster create apo-lab --agents 2
kubectl get nodes   # 1 server + 2 agents → all Ready

# Build the payment-service image locally.
# k3d image import loads it directly into the cluster's internal registry,
# skipping Docker Hub entirely. This is faster and works offline.
cd lab1/payment-service
docker build -t payment-service:lab .
k3d image import payment-service:lab -c apo-lab
kubectl apply -f deployment.yaml
cd ../..

# Deploy Prometheus using a ConfigMap to inject the scrape configuration.
# Storing the config as a Kubernetes resource means you can inspect and
# version it alongside the workloads it monitors, using the same kubectl
# commands you use everywhere else.
kubectl create namespace monitoring
kubectl create configmap prometheus-config \
  --from-file=prometheus.yml=lab1/prometheus-config.yaml \
  -n monitoring
# RBAC lets Prometheus discover the payment-service pods (kubernetes_sd_configs)
kubectl apply -f lab1/k8s/prometheus-rbac.yaml
# prometheus-config.yaml holds the scrape config AND the Deployment/Service.
# Apply only the Kubernetes part (from the first apiVersion: line) — applying the
# whole file fails with "apiVersion not set, kind not set".
sed -n '/^apiVersion:/,$p' lab1/prometheus-config.yaml | kubectl apply -f -
```

Port-forwards create a tunnel from your laptop to a service inside the cluster. kubectl does not multiplex port-forwards, so each one must run in its own terminal window to remain active while you work.

Open a **new terminal window** for each of the following:

```bash
kubectl port-forward svc/payment-service 8080:80
```

```bash
kubectl port-forward -n monitoring svc/prometheus 9090:9090
```

### Verify cluster is ready

Before starting any lab exercise, confirm that all three cluster components are reachable from your laptop. Each check targets a different layer of the stack: node readiness confirms the Kubernetes control plane is healthy, pod status confirms the workload scheduler placed and started the containers, and the HTTP checks confirm the application and metrics planes are reachable end-to-end.

Run these in a **third terminal** with both port-forwards active:

```bash
# 1. All nodes Ready — confirms the control plane is up and all workers joined
kubectl get nodes
# expect: 1 server + 2 agents, STATUS = Ready

# 2. payment-service pod Running — confirms the app container started cleanly
kubectl get pods
# expect: STATUS = Running

# 3. Prometheus pod Running — confirms the metrics store started
kubectl get pods -n monitoring
# expect: STATUS = Running

# 4. payment-service responds — confirms the port-forward is working
#    and the FastAPI app started without errors
curl http://localhost:8080/health
# expect: {"status":"ok"} or similar JSON

# 5. Prometheus is up and accepting queries
curl -s http://localhost:9090/-/ready
# expect: Prometheus Server is Ready.

# 6. Prometheus can see the payment-service target — confirms the
#    scrape config is correct and metrics are flowing end-to-end
curl -s "http://localhost:9090/api/v1/query?query=up" | grep payment
# expect: a result line with "value":[1,...] — 1 means target is up and being scraped
```

**Windows (PowerShell):**

```powershell
kubectl get nodes
kubectl get pods
kubectl get pods -n monitoring
Invoke-WebRequest -Uri http://localhost:8080/health -UseBasicParsing
Invoke-WebRequest -Uri http://localhost:9090/-/ready -UseBasicParsing
```

If any check fails, email ajay@platformetrics.com with the output.

---

## Lab sequence

Labs run in order — each builds on the previous. Attend the lecture module before starting the corresponding lab.

| Order | Lab | Follows module |
|---|---|---|
| 1 | Lab 1 | Confidence Scoring and Safety Gates |
| 2 | Lab 2 | Single-Agent Remediation Loop |
| 3 | Lab 3 | Multi-Agent Orchestration |

---

## Restoring a lab to the original code

The instructor's original code lives in git, not in a copy folder. The tag `workshop-original`
marks `lab1/`, `lab2/`, `lab3/` and `shared/` exactly as published on Day 1. It is pushed to
`origin` (this fork), so it survives even if the upstream repo is deleted. If you break a starter
file, reset it with `restore-lab.sh`:

```bash
bash restore-lab.sh lab1 --dry-run                     # 1. see which files you've changed (changes nothing)
git diff workshop-original -- lab1/gate_starter.py     # optional: see exactly what changed
bash restore-lab.sh lab1 gate_starter.py               # 2a. reset ONE file
bash restore-lab.sh lab1                               # 2b. or reset the whole lab
bash restore-lab.sh all                                # 2c. or reset every lab + shared/
bash restore-lab.sh lab1 --from upstream/main          # or reset to the instructor's LATEST version
```

How it keeps you safe:

- **Asks first** — lists every file it will overwrite and waits for `y` (add `--yes` to skip the prompt).
- **Undoable** — before overwriting, it copies your current version to `.restore_snapshots/<UTC timestamp>/`.
  To get your work back: `cp .restore_snapshots/<timestamp>/lab1/gate_starter.py lab1/`.
  Snapshots are local only (git-ignored).
- **Never deletes** — files you created yourself are left alone; only files that exist in the source ref are restored.
- **Working tree only** — your commits and staged changes are not touched.

**Plain git equivalents** (no prompt, no snapshot — commit or `git stash` first):

```bash
git restore lab1/gate_starter.py                                  # undo uncommitted edits → your last commit
git restore --source=workshop-original lab1/gate_starter.py       # reset to the Day 1 original
git restore --source=upstream/main lab1/gate_starter.py           # reset to the instructor's latest version
git show workshop-original:lab1/gate_starter.py                   # read the original without changing anything
```

> Commit your lab work regularly (`git add lab1 && git commit -m "lab1: gates done"`). A commit is
> the most reliable restore point — `restore-lab.sh` resets to the *original*, git resets to *your last good state*.

**On a fresh clone** of this fork, fetch the tag first: `git fetch origin --tags`.
**To pick up instructor fixes**, run `git fetch upstream` now and then; anything fetched stays in
your local repo even if the upstream repo later disappears.

---

## Repository layout

```
apo_labs/
├── README.md                        ← this file
├── requirements.txt
├── cluster-setup.sh                 one-command real-cluster setup + checks
├── cluster-teardown.sh              stop background processes; pause (--keep) or delete cluster
├── restore-lab.sh                   reset lab files to the originals (git tag workshop-original)
├── docs/                            slides, reference workbook, worksheets, notes, apo-use-cases.md
├── graphify-out/                    knowledge graph of code + docs (open graph.html; GRAPH_REPORT.md summary)
├── lab1/
│   ├── payment-service/             FastAPI target service
│   │   ├── app.py
│   │   ├── Dockerfile
│   │   └── deployment.yaml
│   ├── prometheus-config.yaml
│   ├── gate_starter.py              ← implement gates here
│   ├── diagnostic_agent_starter.py  ← implement agent here
│   ├── solution/
│   │   ├── gate.py
│   │   └── diagnostic_agent.py
│   └── README.md
├── lab2/
│   ├── action_catalog.yaml          remediation action registry
│   ├── autonomous_agent_starter.py  ← implement full loop here
│   ├── solution/
│   │   └── autonomous_agent.py
│   └── README.md
└── lab3/
    ├── orchestrator_starter.py      ← implement multi-agent system here
    ├── solution/
    │   └── orchestrator.py
    └── README.md
```

---

## Cleanup

```bash
bash cluster-teardown.sh          # stops port-forwards + load generator, deletes the cluster
# or, manually:
k3d cluster delete apo-lab
```

**macOS** (Colima):

```bash
colima stop
```

---

## Support

Questions? Email ajay@platformetrics.com.  
Solutions are in each lab's `solution/` directory — try the lab yourself first.
