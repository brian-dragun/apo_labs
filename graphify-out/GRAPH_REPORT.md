# Graph Report - apo_labs  (2026-11-19)

## Corpus Check
- Corpus is ~25,695 words - fits in a single context window. You may not need a graph.

## Summary
- 606 nodes · 1001 edges · 36 communities (26 shown, 10 thin omitted)
- Extraction: 93% EXTRACTED · 7% INFERRED · 0% AMBIGUOUS · INFERRED: 70 edges (avg confidence: 0.9)
- Token cost: 349,797 input · 0 output

## Community Hubs (Navigation)
- Reference Solution Agents
- APO Workbook Concepts
- Grounded Decision Explainer
- Lab 1 Gates & Scoring Docs
- Payment Service Deployment
- Prometheus Deployment Manifest
- Payment Service & Load Generator
- Prometheus RBAC
- Lab 1 Gate Starter
- Cluster Setup Script
- Lab 1 Diagnostic Agent Starter
- Autonomy Levels & Agent Roles
- Prometheus Client & Specialist Agents
- Workshop Deck & Presenters
- Confidence Scoring & Audit Trail
- Remediation Action Catalog
- Lab 3 Orchestrator Starter
- Prometheus Scrape Config
- Cluster Teardown Script
- Mock Telemetry Snapshots
- Harness Pattern & Golden Path
- APO vs AIOps Mindset
- Probabilistic/Deterministic Layers
- Safety Gates & Env Tuning
- Lab Restore Script
- Observe-Decide-Act Pillars
- Lab 2 Setup Script
- Specialist Agent Findings
- Lab 3 Setup Script
- Lab 1 Teardown
- Lab 2 Teardown
- Lab 3 Teardown

## God Nodes (most connected - your core abstractions)
1. `APO Slides Day 1 (Agentic Platform Operations Workshop)` - 17 edges
2. `APO Reference Workbook (APO-003)` - 15 edges
3. `PrometheusClient` - 14 edges
4. `run()` - 14 edges
5. `GateOrchestrator` - 13 edges
6. `actions` - 13 edges
7. `run()` - 13 edges
8. `APO Workshop Labs README` - 13 edges
9. `Four hard limits / safety gates (all must pass, AND conditions)` - 13 edges
10. `GateOrchestrator` - 12 edges

## Surprising Connections (you probably didn't know these)
- `Confidence formula C = 0.35S + 0.25H + 0.25R + 0.15F` --implements--> `ConfidenceScorer`  [INFERRED]
  docs/APO-Slides-Day1.pdf → lab1/diagnostic_agent_starter.py
- `MEMORY_PRESSURE symptom class` --conceptually_related_to--> `detect_symptom_class()`  [INFERRED]
  docs/APO-Slides-Day1.pdf → lab1/diagnostic_agent_starter.py
- `Confidence-to-autonomy bands` --conceptually_related_to--> `autonomy_level()`  [INFERRED]
  docs/APO-Slides-Day1.pdf → lab1/diagnostic_agent_starter.py
- `ActionProposal` --references--> `ActionProposal`  [EXTRACTED]
  docs/APO-Slides-Day1.pdf → lab1/gate_starter.py
- `GateResult` --references--> `GateResult`  [EXTRACTED]
  docs/APO-Slides-Day1.pdf → lab1/gate_starter.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **APO remediation pipeline stages** — lab1_readme_safety_gates, lab1_readme_confidence_formula, lab2_readme_action_executor, lab2_readme_verification_loop, lab2_readme_jsonl_audit_log [EXTRACTED 1.00]
- **Model is replaceable, harness verifies its output** — docs_notes_day_1_harness, lab2_readme_explain_decision, lab3_readme_llm_logs_agent [INFERRED 0.85]
- **Multi-agent fan-out and arbitration flow** — lab3_readme_asyncio_fan_out, lab3_readme_metrics_agent, lab3_readme_logs_agent, lab3_readme_arbitration_rules, lab3_readme_action_agent [EXTRACTED 1.00]
- **Four deterministic gate checks** — docs_apo_reference_workbook_deterministic_gate, docs_apo_reference_workbook_cost_gate, docs_apo_reference_workbook_blast_radius_gate, docs_apo_reference_workbook_reversibility_gate, docs_apo_reference_workbook_time_gate, docs_apo_reference_workbook_gate_parameters_by_environment [EXTRACTED 1.00]
- **Confidence score composition (S, H, R, F)** — docs_apo_reference_workbook_confidence_score_formula, docs_apo_reference_workbook_signal_strength_s, docs_apo_reference_workbook_historical_match_h, docs_apo_reference_workbook_remediation_confidence_r, docs_apo_reference_workbook_data_freshness_f, docs_apo_reference_workbook_score_to_autonomy_band [EXTRACTED 1.00]
- **Gate design worksheet flow** — docs_gate_design_worksheet_step1_write_down_your_fix, docs_gate_design_worksheet_step2_four_checks, docs_gate_design_worksheet_step3_production_limits, docs_gate_design_worksheet_step4_stress_test, docs_gate_design_worksheet_autonomy_ladder [EXTRACTED 1.00]
- **Four deterministic safety gates (all must pass)** — docs_apo_slides_day1_cost_gate, docs_apo_slides_day1_blast_radius_gate, docs_apo_slides_day1_reversibility_gate, docs_apo_slides_day1_time_gate, docs_apo_slides_day1_gate_orchestrator [EXTRACTED 1.00]
- **Confidence score components (S, H, R, F)** — docs_apo_slides_day1_signal_clarity, docs_apo_slides_day1_historical_match, docs_apo_slides_day1_remediation_confidence, docs_apo_slides_day1_data_freshness, docs_apo_slides_day1_confidence_formula [EXTRACTED 1.00]
- **L4 multi-agent orchestration team** — docs_apo_slides_day1_orchestrator_agent, docs_apo_slides_day1_metrics_agent, docs_apo_slides_day1_logs_agent, docs_apo_slides_day1_action_agent, docs_apo_slides_day1_verification_agent [EXTRACTED 1.00]

## Communities (36 total, 10 thin omitted)

### Community 0 - "Reference Solution Agents"
Cohesion: 0.05
Nodes (25): autonomy_level(), ConfidenceScorer, detect_symptom_class(), MetricsReader, PrometheusClient, run(), symptom_to_action(), ActionProposal (+17 more)

### Community 1 - "APO Workbook Concepts"
Cohesion: 0.06
Nodes (44): APO Reference Workbook (APO-003), Action Catalog, ActionProposal, Agentic Platform Operations (APO), Ajay Chankramath, apo_labs labs (Lab 1 confidence gate, Lab 2 autonomous remediation, Lab 3 multi-agent orchestration), Audit Trail Record (append-only JSONL decision record), Autonomy Levels L1-L5 (+36 more)

### Community 2 - "Grounded Decision Explainer"
Cohesion: 0.08
Nodes (18): allowed_facts(), ask_model(), check_grounding(), load_last_record(), main(), template_message(), confidence_from_evidence(), LLMLogsAgent (+10 more)

### Community 3 - "Lab 1 Gates & Scoring Docs"
Cohesion: 0.08
Nodes (35): Day 1 Notes, Lab 1 - Your First Confidence Gate, Autonomy Levels L1-L5, Blast Radius Gate (10% of pods), APO Confidence Formula C=0.35S+0.25H+0.25R+0.15F, Cost Gate ($500/hr), Fault Injection (/toggle_fault), Reversibility Gate (600s reversal) (+27 more)

### Community 4 - "Payment Service Deployment"
Cohesion: 0.05
Nodes (40): prometheus.io/path, prometheus.io/port, prometheus.io/scrape, image, imagePullPolicy, livenessProbe, name, ports (+32 more)

### Community 5 - "Prometheus Deployment Manifest"
Cohesion: 0.05
Nodes (40): args, image, imagePullPolicy, name, ports, volumeMounts, apiVersion, kind (+32 more)

### Community 6 - "Payment Service & Load Generator"
Cohesion: 0.10
Nodes (14): _health(), _post(), run(), _toggle_fault(), checkout(), health(), metrics(), _record() (+6 more)

### Community 7 - "Prometheus RBAC"
Cohesion: 0.08
Nodes (25): apiVersion, kind, metadata, rules, apiVersion, kind, metadata, roleRef (+17 more)

### Community 8 - "Lab 1 Gate Starter"
Cohesion: 0.18
Nodes (5): ActionProposal, GateConfig, GateOrchestrator, GateResult, _run_tests()

### Community 9 - "Cluster Setup Script"
Cohesion: 0.35
Nodes (16): check_prereqs(), count_up_targets(), deploy_payment_service(), deploy_prometheus(), die(), ensure_cluster(), ensure_venv(), info() (+8 more)

### Community 10 - "Lab 1 Diagnostic Agent Starter"
Cohesion: 0.15
Nodes (6): autonomy_level(), ConfidenceScorer, detect_symptom_class(), MetricsReader, run(), symptom_to_action()

### Community 11 - "Autonomy Levels & Agent Roles"
Cohesion: 0.14
Nodes (12): ActionAgent (selects & executes remediation), Alertmanager, Deterministic arbitration rules, Confidence-to-autonomy bands, DiagnosticAgent, LogsAgent (Loki analysis), Loki, MetricsAgent (Prometheus analysis) (+4 more)

### Community 12 - "Prometheus Client & Specialist Agents"
Cohesion: 0.16
Nodes (5): PrometheusClient, LogsAgent, main(), MetricsAgent, Orchestrator

### Community 13 - "Workshop Deck & Presenters"
Cohesion: 0.18
Nodes (14): Ajay Chankramath (CEO/CTO, Platformetrics), apo_audit.jsonl (Lab 2 audit output), APO scaffold repo (github.com/achankra/apo_labs), APO Slides Day 1 (Agentic Platform Operations Workshop), Lab 1: Wire a confidence gate from scratch, Lab 2: Single-agent autonomous remediation with LLM explainer, Lab 3: Multi-agent diagnosis + auto-remediation with LLM diagnosis, Mallory Haigh (Head of Training & Enablement) (+6 more)

### Community 14 - "Confidence Scoring & Audit Trail"
Cohesion: 0.19
Nodes (7): Audit log schema (decision_id, incident_id, timestamp, agent_id, telemetry_hash), Data freshness (F), DB_BOTTLENECK symptom class, DEPLOYMENT_REGRESSION symptom class, MEMORY_PRESSURE symptom class, M04 Confidence Scoring & Audit Trails, Remediation confidence (R)

### Community 15 - "Remediation Action Catalog"
Cohesion: 0.14
Nodes (13): actions, action_id, allowed_environments, blast_radius_max, cooldown_seconds, description, display_name, max_executions_per_hour (+5 more)

### Community 16 - "Lab 3 Orchestrator Starter"
Cohesion: 0.16
Nodes (3): ActionAgent, arbitrate(), ArbitrationResult

### Community 17 - "Prometheus Scrape Config"
Cohesion: 0.17
Nodes (12): scrape_configs, namespaces, role, names, action, regex, replacement, source_labels (+4 more)

### Community 18 - "Cluster Teardown Script"
Cohesion: 0.42
Nodes (9): cluster_exists(), die(), info(), main(), ok(), cluster-teardown.sh script, stop_background(), usage() (+1 more)

### Community 19 - "Mock Telemetry Snapshots"
Cohesion: 0.24
Nodes (3): get_snapshot(), list_snapshots(), TelemetrySnapshot

### Community 20 - "Harness Pattern & Golden Path"
Cohesion: 0.29
Nodes (6): APO Golden Path 7-stage pipeline (Detect, Diagnose, Orchestrate, Gate, Execute, Verify, Audit), Harness execution flow (10 steps, end to end), M03 The Harness Pattern, payment-service (lab target deployment), pod.rolling_restart action, Worked example: payment-service C = 0.8950 -> L4, execute pod.rolling_restart

### Community 21 - "APO vs AIOps Mindset"
Cohesion: 0.24
Nodes (6): AIOps (alert correlation & noise reduction), M01 The Operational Mindset Shift, Production APO adopters (Netflix, Uber CURT, Shopify, LinkedIn, Google SRE, Cloudflare, Stripe, Datadog, Atlassian), Reactive ops death spiral, APO readiness decision tree, The trust gradient

### Community 22 - "Probabilistic/Deterministic Layers"
Cohesion: 0.25
Nodes (6): ActionProposal, Agent layer (probabilistic intelligence), Compliance mapping (SOC 2 CC6.1/CC7.2/CC8.1, ISO 27001 A.12.4/A.14.2), Gate layer (deterministic enforcement), GateOrchestrator, GateResult

### Community 24 - "Lab Restore Script"
Cohesion: 0.46
Nodes (7): die(), info(), main(), ok(), restore-lab.sh script, usage(), warn()

### Community 26 - "Lab 2 Setup Script"
Cohesion: 0.60
Nodes (5): die(), info(), setup.sh script, success(), warn()

### Community 28 - "Lab 3 Setup Script"
Cohesion: 0.60
Nodes (5): die(), info(), setup.sh script, success(), warn()

## Knowledge Gaps
- **39 isolated node(s):** `Simulate Mode (--simulate)`, `restore-lab.sh and workshop-original tag`, `Workshop Readiness Checkpoint`, `requests`, `Day 1 Notes` (+34 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 221 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **10 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `GateOrchestrator` connect `Lab 1 Gate Starter` to `Lab 3 Orchestrator Starter`, `Lab 1 Diagnostic Agent Starter`, `Workshop Deck & Presenters`, `Probabilistic/Deterministic Layers`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Are the 6 inferred relationships involving `PrometheusClient` (e.g. with `run()` and `VerificationLoop`) actually correct?**
  _`PrometheusClient` has 6 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Simulate Mode (--simulate)`, `restore-lab.sh and workshop-original tag`, `Workshop Readiness Checkpoint` to the rest of the system?**
  _39 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Reference Solution Agents` be split into smaller, more focused modules?**
  _Cohesion score 0.05070422535211268 - nodes in this community are weakly interconnected._
- **Why does `Lab 1: Wire a confidence gate from scratch` connect `Workshop Deck & Presenters` to `Lab 1 Gate Starter`, `Harness Pattern & Golden Path`, `Confidence Scoring & Audit Trail`, `Safety Gates & Env Tuning`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **Are the 6 inferred relationships involving `run()` (e.g. with `ConfidenceScorer` and `MetricsReader`) actually correct?**
  _`run()` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Should `APO Workbook Concepts` be split into smaller, more focused modules?**
  _Cohesion score 0.062409288824383166 - nodes in this community are weakly interconnected._