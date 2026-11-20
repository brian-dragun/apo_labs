# APO Beyond Kubernetes — Six Enterprise Use Cases

**Status:** Design proposal (not implemented) · **Author:** Brian Dragun · **Date:** 2026-11-20
**Builds on:** Labs 1–3 (`lab1/gate_starter.py`, `lab1/diagnostic_agent_starter.py`, `lab2/autonomous_agent_starter.py`, `lab3/orchestrator_starter.py`)
**Aligned with:** workshop deck `docs/Workshop-Slides.pdf` (Modules 1–8). Where the deck gives
two different numbers, this document uses the stricter one and says so.

The lab loop — **detect → diagnose → gate → execute → verify → audit** — does not depend on
Kubernetes. This document maps it onto six platforms and sets an autonomy ceiling for each,
based on blast radius, reversibility, and what a compromised agent identity could reach.

| # | Use case | Fit | Autonomy ceiling | First deliverable |
|---|---|---|---|---|
| 1 | Azure API Management (APIM) | Strong — closest to the lab | L3–L4 | Revision rollback + backend failover responder |
| 2 | Azure AI Foundry / Azure OpenAI | Strong — cost gate matters | L2–L4 by action | Throttling failover via APIM backend pool |
| 3 | Entra ID Conditional Access | Detection only | **L1–L2** | Policy-change impact + drift detector |
| 4 | On-prem AD tiering model | Strong detection, narrow action | L1–L3 by tier | Privileged-group drift responder |
| 5 | Internal AD CS / PKI | Ops: strong · Security: detection, narrow action | L4 (CRL) · L3 (hold) · L1 (key ops) | CRL health responder + ESC1 issuance detector |
| 6 | Entra ID Enterprise Apps & App Registrations | Strong — top outage and persistence risk | L3 (grant revoke, SP disable) · L2 (config) · L1 (delete) | Secret-expiry + rotation check; illicit-consent responder |

> **Principle carried over from the course:** the model proposes, code decides. Every action is
> a reviewed catalog entry; gates are deterministic code; the agent never invents a command.

---

## 1. Shared adaptation model

What changes from the lab, for every use case:

| Lab component | Lab (k8s) | Azure / AD version |
|---|---|---|
| `MetricsReader` | PromQL via Prometheus | KQL via Log Analytics (`azure-monitor-query`), managed identity, **read-only** |
| `detect_symptom_class` | memory / latency / errors | Per-platform rule table (sections below) |
| `ConfidenceScorer` | S hardcoded to memory | S = clarity of the signal that *defines* the symptom; H/R/F unchanged |
| `action_catalog.yaml` | kubectl actions | Per-platform catalog; same fields (`risk_class`, `blast_radius_max`, `reversal_time_seconds`, `required_confidence`, `max_executions_per_hour`, `cooldown_seconds`) plus `max_autonomy_level` |
| `ActionExecutor` | `kubectl` subprocess | Azure SDK / REST, Bicep redeploy, or PowerShell behind a JEA endpoint |
| Gates | cost, % pods, reversal time, peak hours | Same four, re-expressed per platform; the time gate also covers change correlation and open incidents (§1.3) |
| `VerificationLoop` | P90 < 1.5× baseline | Per-class "healthy" condition from the catalog, 3 healthy polls in a row (§1.7) |
| `AuditLogger` | local JSONL | Log Analytics custom table → Sentinel; written by an identity separate from the executor (§1.8) |
| `Orchestrator` (Lab 3) | MetricsAgent + LogsAgent, asyncio | Same rules; agents read KQL tables instead of PromQL and pod logs (§1.6) |

### 1.1 Autonomy levels and the level rule

| Level | Course name | What APO does | Human role |
|---|---|---|---|
| L1 | Manual | Nothing — humans are paged as before | Does everything |
| L2 | Assisted | Diagnoses and recommends (or opens a PR) | Decides and executes |
| L3 | Supervised | Executes **after** approval | Approves each action |
| L4 | Active | Executes, then notifies; 60-second review window | Reviews, can undo |
| L5 | Mature | Full autonomy for the class; quarterly review | Audits |

**Level rule:** the effective level is the **lowest** of three values:

```
effective_level = min(level_from_confidence,      # lab: C ≥ 0.80 → L4
                      max_autonomy_level,          # catalog: the action's risk target
                      org_ceiling)                 # current rollout phase (§8.4)
```

High confidence can never lift a dangerous action above its ceiling, and no action can run above
the phase the organization has reached. L5 is out of scope for every use case in this document.

### 1.2 Trigger: what starts an incident

APO consumes **correlated** input; it does not do raw-event correlation itself (that is the AIOps
layer). It starts from one of two places:

| Source | Use cases | Notes |
|---|---|---|
| **SLO burn-rate alert** (Azure Monitor scheduled-query alert) | APIM, Foundry | Fast burn: 14.4× budget over 5 min · slow burn: 2× over 1 h. Fire *before* the SLO is breached |
| **Sentinel incident** (analytics rule → automation rule) | CA, AD, AD CS, Entra apps | Sentinel has already grouped related alerts; APO never acts on a single raw event |

Every alert APO receives carries the same contract, or APO escalates without diagnosing:

```json
{ "incident_id": "INC-…", "service": "apim-prod-eus", "environment": "prod",
  "apo_class": "latency", "severity": "P1",
  "current": "P90 2.4 s", "baseline": "P90 0.6 s", "duration": "6 min" }
```

- **Severity:** P1 act now · P2 act within 15 min · P3 inform only (never executes).
- **Scope:** one incident, one service. Downstream symptoms are suppressed (alert processing rules
  in Azure Monitor, incident grouping in Sentinel), never "everything is broken".
- **Telemetry is untrusted input.** An attacker who can write logs can try to *trigger* an action
  (for example, forging events so APO disables a legitimate admin). Validate the alert against its
  source table before diagnosing, and keep any learning component away from the gates.

### 1.3 Gates

The four course gates, with production defaults and per-environment thresholds:

| Gate | Prod default | Dev / test | Notes |
|---|---|---|---|
| Confidence floor (`required_confidence`) | **0.85** | 0.60 | Per catalog entry; may be higher, never lower than the environment floor |
| Cost | $1,000 / hour | higher | Anything that commits spend (PTU, APIM units) is capped at L2 regardless |
| Blast radius | **≤ 5 %** of the service's users / traffic / objects | higher | Definition per use case below |
| Reversibility | undo ≤ **300 s** | higher | Uses the action's reversibility class (below) |
| Time | freeze windows, month-end close, **approved change in progress**, **correlated incident already open** | — | Change correlation and open-incident checks live here |

**Time gate detail.** Block when the target resource has an approved, in-window change in
ServiceNow / Azure DevOps (otherwise the agent "remediates" legitimate admin work), or when another
open incident already touches the same resource (two responders fighting). The gate **fails
closed**: if the change system or Sentinel is unreachable, the action is blocked.

**Reversibility classes** — every catalog entry declares one:

| Class | Meaning | Maximum level |
|---|---|---|
| `IMMEDIATE` | Undo ≤ 300 s with a known `reversal_command` | L4 |
| `DELAYED` | Undo possible but > 300 s (scale-in, re-deploy, re-replication) | Blocked by the prod reversibility gate → L2 recommend only |
| `IRREVERSIBLE` | No undo (password reset, permanent revocation, deletion, key operations) | **L1** — never executed by APO |

**Hard gates** are code checks no confidence can override (break-glass exclusions, critical-app
allowlist, revocation reason code 6 only, approved failover regions). They run before the four gates.

### 1.4 Catalog fields

Each entry carries the lab fields (`risk_class`, `blast_radius_max`, `reversal_time_seconds`,
`required_confidence`, `max_executions_per_hour`, `cooldown_seconds`) plus:

| Field | Purpose |
|---|---|
| `display_name` | What humans see in approvals and change records |
| `reversibility_class` | `IMMEDIATE` / `DELAYED` / `IRREVERSIBLE` (§1.3) |
| `reversal_command` | The tested undo; required for `IMMEDIATE` |
| `allowed_environments` | e.g. `[dev, test]` until the action earns prod |
| `max_autonomy_level` | The action's risk target (§1.1) |
| `healthy_condition` | What "recovered" means for this class (§1.7) |
| `enabled` | Per-class kill switch (§8.2) |

**Stale-catalog check (monthly):** re-run every `reversal_command` in non-prod and compare the
measured undo time with `reversal_time_seconds`. An entry that drifts past 300 s drops to `DELAYED`.

Action selection follows the course: symptom class → catalog candidates → drop those below the
confidence floor → drop those not allowed in this environment → the remaining action goes to the gates.

### 1.5 Confidence in production

`C = 0.35·S + 0.25·H + 0.25·R + 0.15·F` as in the labs, with the production definitions:

| Component | Lab | Production |
|---|---|---|
| S — signal strength | Hard-coded from memory | z-score of the defining signal vs baseline: `min(1, z / 3)` |
| H — history | Constant | `verified_recoveries / executions` for this class over 90 days, exponential decay, **minimum 20 executions** (below that, H = 0) |
| R — reversibility | From catalog | Unchanged |
| F — freshness | Last scrape age | Log Analytics ingestion lag for the source table |

**Calibration:** expected calibration error (ECE) < 0.05, recalculated monthly from the audit
table. If C says 0.90, about 90 % of those actions must actually verify.

**Limitation for the security use cases:** H needs 20+ executions and promotion needs 100+
occurrences (§8.4). Tier 0 group additions, ESC1 issuance and illicit consent are — hopefully —
rare, so these classes may never accumulate the history for L4. Plan for them to stay at L3
(approval) permanently; that is the design working, not a gap.

### 1.6 Diagnosis agents

- **Timeouts:** 50 ms per agent in simulate mode, 10 s against real systems (Lab 3); model
  inference, if an LLM agent is added, 5 s. On timeout or error the finding is marked timed out,
  never guessed.
- **Quorum:** if fewer than 2 of 3 agents return a finding, escalate.
- **Arbitration:** the five Lab 3 rules, in order (timeout → contradiction / corroboration →
  cascade → weighted consensus). If the arbitrated confidence is **below 0.85, do not act:
  escalate with every agent's finding attached**.
- **Two `UNKNOWN` findings must not corroborate each other.** The Lab 3 rule as written adds +0.10
  when both agents return `UNKNOWN`; production code only applies the bonus to a known class.
- **Circuit breaker:** an agent that fails 3 times in 5 minutes is disabled and alerts. Core agents
  run with 2+ replicas.
- **Messages** (`DiagnosisRequest`, `DiagnosisResponse`, `ActionProposal`, `GateResult`,
  `ExecutionResult`) all carry `incident_id`, so one incident can be traced end to end.

### 1.7 Verification standard

Applies to every "Verification" section below unless it says otherwise:

- Poll the class's `healthy_condition`. **Recovered = 3 healthy polls in a row**; one healthy poll
  followed by a bad one resets the count.
- Course default: poll every 5 s, 300 s timeout. Log Analytics sources have minutes of ingestion
  lag, so here: wait one ingestion window, poll every 30 s, 10-minute timeout.
- No streak by the timeout → outcome `ESCALATED`, page a human with the audit record.
- Outcomes are one of `RECOVERED`, `GATE_BLOCKED`, `ESCALATED`, `EXECUTION_FAILED` (as in Lab 2).

### 1.8 Audit record and retention

One record per decision, written **before** the switch check (§8.2) so nothing is lost in any mode:

| Field | Content |
|---|---|
| `decision_id`, `incident_id` | Unique per decision; incident links the whole chain |
| `timestamp` | Millisecond precision, UTC |
| `agent_id` | Which agent(s) and versions produced the diagnosis |
| `telemetry_snapshot` + `snapshot_sha256` | Exactly what the agent saw, with a hash to prove it was not altered |
| `scores` | S, H, R, F, C and every arbitration rule applied |
| `gate_result` | Approved, checks, `blocked_by`, reasons |
| `approver` | Human (L3) or gate-config owner (L4) |
| `execution`, `verification`, `outcome` | Command, exit code, stdout/stderr, poll history |

- **Where:** Log Analytics custom table → Sentinel, mapped to OCSF, written by an identity the
  executor cannot use.
- **Retention:** 7 years, immutable (Log Analytics archive tier or an immutable storage export).
- **Control mapping:** SOC 2 CC6.1 (logical access), CC7.2 (monitoring), CC8.1 (change
  management); ISO 27001 A.12.4 (logging), A.14.2 (change control) — 2013 numbering, as on the slide; the 2022
revision moves these to Annex A 8.15 and 8.32. *Confirm the mapping with
  compliance before relying on it.*
- **Sensitive data:** no secrets, tokens or personal data beyond the identifiers needed for the
  decision (UPN, object ID).

### 1.9 Agent identity (all use cases)

- Managed identity / gMSA per agent **and per tier** — never a shared admin account.
- Detection identity: read-only. Execution identity: only the rights its catalog needs.
- Execution credentials never leave the tier they control (see §5).

### 1.10 What to expect

APO removes the human response time, not the platform's recovery time. The course's own
measurements are about 35–115 s end to end (decision in well under a second, the rest is the
platform restarting, scaling or rolling back). For the Azure and AD platforms here, with Log
Analytics ingestion lag and replication, expect **minutes rather than hours** — not "zero MTTR".
Measure the human MTTR per class before enabling APO, or there is nothing to compare against.

---

## 2. Use case: Azure API Management

**Goal:** detect gateway or backend degradation and roll back / scale / fail over without waiting
for an on-call engineer.

### Signals (Log Analytics, resource-specific tables)

```kql
// 5xx rate and P90 latency per API, last 5 minutes
ApiManagementGatewayLogs
| where TimeGenerated > ago(5m)
| summarize total = count(),
            errors = countif(ResponseCode >= 500),
            throttled = countif(ResponseCode == 429),
            p90_ms = percentile(TotalTime, 90),
            backend_p90_ms = percentile(BackendTime, 90)
          by ApiId
| extend error_rate = todouble(errors) / total
```

Plus the `Capacity` platform metric (Azure Monitor) for gateway saturation.

### Symptom classes

| Priority | Condition | Symptom | Proposed action |
|---|---|---|---|
| 1 | error_rate > 0.10 within 15 min of a revision change | `REVISION_REGRESSION` | `apim.revision_rollback` |
| 2 | Capacity > 80% sustained 10 min | `GATEWAY_SATURATION` | `apim.scale_out` |
| 3 | backend_p90 > 3× baseline AND error_rate > 0.05 | `BACKEND_DEGRADED` | `apim.backend_failover` |
| 4 | else | `UNKNOWN` | escalate |

### Action catalog (sketch)

```yaml
actions:
  - action_id: apim.revision_rollback
    display_name: Roll back APIM API revision
    risk_class: low
    executor: azure_rest            # make the previous revision current
    reversibility_class: IMMEDIATE
    reversal_command: make the rolled-back revision current again
    reversal_time_seconds: 60
    required_confidence: 0.85
    max_autonomy_level: L4
    allowed_environments: [dev, test, prod]
    healthy_condition: error_rate < 0.01
    cooldown_seconds: 900
  - action_id: apim.scale_out
    display_name: Add one APIM unit
    risk_class: medium
    executor: none                  # recommend only in prod (see note)
    estimated_cost_per_hour_usd: <from SKU price>
    reversibility_class: DELAYED    # scale-in takes ~30 min
    reversal_time_seconds: 1800
    required_confidence: 0.85
    max_autonomy_level: L2          # fails the 300 s prod reversibility gate; also adds spend
    allowed_environments: [dev, test, prod]
    healthy_condition: Capacity < 70%
  - action_id: apim.backend_failover
    display_name: Fail over APIM backend pool
    risk_class: medium
    executor: azure_rest            # switch backend pool / trip circuit breaker
    reversibility_class: IMMEDIATE
    reversal_command: restore original backend pool weights
    reversal_time_seconds: 120
    required_confidence: 0.85
    max_autonomy_level: L4
    allowed_environments: [dev, test, prod]
    healthy_condition: error_rate < 0.01 and p90_ms < 1.5 × baseline
```

> **Scale-out cannot be autonomous in prod.** Its undo (scale-in) takes about 30 minutes, which
> fails the 300 s reversibility gate at any confidence. APO recommends it with the evidence; a
> human approves and runs it. Autoscale rules on the APIM instance are the better fix.

### Gates
- **Cost:** scale-out unit price vs hourly budget.
- **Blast radius:** % of total gateway traffic served by the affected APIs (≤ 5 % in prod).
- **Reversibility:** catalog reversibility class (§1.3).
- **Time:** freeze and business-critical windows, approved APIM changes in progress, open
  incidents on the same instance.

### Verification
Standard (§1.7) on the KQL above: healthy when `error_rate < 0.01` and `p90_ms < 1.5 × baseline`.
Wait one ingestion window before the first poll (same lesson as Lab 2's settle delay).

### Identity
Managed identity with **API Management Service Contributor scoped to the one APIM instance** —
not subscription-wide.

---

## 3. Use case: Azure AI Foundry / Azure OpenAI

**Goal:** keep model endpoints available under throttling, quota exhaustion and regional
degradation, without uncontrolled spend.

### Signals
- Azure Monitor metrics on the AI resource: request count by status code (429 / 5xx), processed
  tokens vs deployment TPM quota, latency, and provisioned-throughput utilization for PTU
  deployments. *Verify exact metric names in your tenant before implementing.*
- If traffic flows through APIM (recommended), the APIM gateway logs in §2 give per-consumer views.

### Symptom classes

| Priority | Condition | Symptom | Proposed action |
|---|---|---|---|
| 1 | 429 rate > 0.20 for 5 min, quota at ceiling | `QUOTA_EXHAUSTED` | `aoai.failover_secondary` |
| 2 | 5xx rate > 0.05 in one region, other region healthy | `REGIONAL_DEGRADATION` | `aoai.failover_secondary` |
| 3 | Error/quality regression after a model version change | `MODEL_REGRESSION` | `aoai.version_rollback` |
| 4 | PTU utilization > 90% sustained | `CAPACITY_PRESSURE` | `aoai.add_ptu` (recommend only) |

### Action catalog (sketch)

```yaml
actions:
  - action_id: aoai.failover_secondary
    executor: azure_rest            # shift APIM backend pool weight to secondary deployment
    reversibility_class: IMMEDIATE
    reversal_time_seconds: 120
    required_confidence: 0.85
    max_autonomy_level: L4
  - action_id: aoai.rebalance_tpm
    executor: azure_rest            # move TPM between deployments in the same quota pool
    reversibility_class: IMMEDIATE  # exactly at the 300 s limit — re-measure monthly (§1.4)
    reversal_time_seconds: 300
    required_confidence: 0.85
    max_autonomy_level: L3
  - action_id: aoai.version_rollback
    executor: none                  # recommend only in prod
    reversibility_class: DELAYED    # re-deploying the newer version takes ~10 min
    reversal_time_seconds: 600
    required_confidence: 0.85
    max_autonomy_level: L2          # fails the 300 s prod reversibility gate
    allowed_environments: [dev, test, prod]
  - action_id: aoai.add_ptu
    executor: none                  # recommendation only — commits significant spend
    max_autonomy_level: L2
```

> A model version rollback can still be the right fix. In prod it is a recommendation with
> evidence; in dev/test (looser reversibility gate) it can run at L3. Prefer failing over to a
> secondary deployment pinned to the previous version — that path is `IMMEDIATE`.

### Gates
- **Cost gate is the main control.** PTU is a large commitment; anything that adds spend is capped at L2.
- **Blast radius:** % of consumers (APIM subscriptions / products) affected.
- **Data residency:** a failover target must be in an approved region — enforce as a hard gate,
  not a confidence input.

### Verification
Standard (§1.7). Healthy when 429 and 5xx rates are back under threshold on the serving
deployment, latency is within 1.5× baseline, and the secondary deployment is healthy under the
shifted load.

---

## 4. Use case: Entra ID Conditional Access

**Recommendation: detection and proposals only (L1–L2). No autonomous writes.**

Why:
1. **Blast radius is the whole tenant.** One wrong change can lock out every user — including
   the agent — or silently weaken MFA.
2. **Credential risk.** `Policy.ReadWrite.ConditionalAccess` on an agent identity means a stolen
   token can rewrite tenant security posture. That is the worst lateral-movement outcome to create.
3. **"Healthy" is not measurable.** A policy that blocks too little produces no error signal.

### Signals

```kql
// CA failures per policy, last hour, vs 7-day hourly baseline
SigninLogs
| where TimeGenerated > ago(1h)
| mv-expand policy = ConditionalAccessPolicies
| where tostring(policy.result) == "failure"
| summarize failures = count(), users = dcount(UserPrincipalName)
          by policy_name = tostring(policy.displayName)
```

```kql
// Recent CA policy changes
AuditLogs
| where TimeGenerated > ago(24h)
| where OperationName in ("Add conditional access policy",
                          "Update conditional access policy",
                          "Delete conditional access policy")
| project TimeGenerated, OperationName,
          actor = tostring(InitiatedBy.user.userPrincipalName),
          policy = tostring(TargetResources[0].displayName)
```

Plus: policy definitions from Graph compared against a baseline in git (CA-as-code).

### Symptom classes

| Symptom | Condition | Output |
|---|---|---|
| `POLICY_CHANGE_IMPACT` | Failures for a policy > 10× baseline within 1 h of a change to it | Proposal: revert to last known-good version |
| `POLICY_DRIFT` | Live policy ≠ git baseline, no matching change ticket | Proposal: PR restoring baseline |
| `BREAKGLASS_EXPOSURE` | A break-glass account is no longer excluded from a blocking policy | High-priority alert |
| `REPORT_ONLY_RISK` | Report-only policy would block > N users if enabled | Advisory before rollout |

### Action model
- **Act = open a pull request** against the CA-as-code repo with the proposed Graph JSON, plus
  the evidence (KQL results, change history). A human approves and the pipeline deploys.
- The agent identity holds `Policy.Read.All` only. Write rights belong to the deployment
  pipeline, behind required reviewers.
- **Hard gates in code, no confidence can override:** reject any proposal that removes a
  break-glass exclusion, disables a policy that enforces MFA, or widens an exclusion to a group.

---

## 5. Use case: On-prem Active Directory tiering model

**Strongest detection fit; action limited to narrow, reversible containment.**

### The constraint that drives the design

Under the tiering model, anything that can write Tier 0 objects *is* Tier 0. An agent with
Tier 0 write rights must run on Tier 0 infrastructure with a Tier 0 identity, or it becomes the
attack path. So the agent is **split by tier**:

| Layer | Runs in | Rights |
|---|---|---|
| Detect / diagnose / score / gate / audit | Sentinel / Log Analytics (Tier 1 or cloud) | Read-only: Security events, Defender for Identity, AD read |
| Executor for Tier 1/2 objects | Tier 1 server | Delegated rights on specific OUs only |
| Executor for Tier 0 objects | **JEA endpoint** on a Tier 0 host, gMSA | Only whitelisted cmdlets with constrained parameters |

JEA is the action catalog enforced by Windows: the role capability (`.psrc`) exposes, for
example, `Remove-ADGroupMember` restricted to four named groups — nothing else is callable.

### Signals

```kql
// Membership added to Tier 0 groups (global / local / universal)
SecurityEvent
| where EventID in (4728, 4732, 4756)
| where TargetUserName in ("Domain Admins", "Enterprise Admins",
                           "Schema Admins", "Administrators")
| project TimeGenerated, Computer, group = TargetUserName,
          member = MemberName, actor = SubjectUserName
```

```kql
// Tier violation: Tier 0 account interactive/RDP logon to a non-Tier-0 host
SecurityEvent
| where EventID == 4624 and LogonType in (2, 10)
| where TargetUserName in (Tier0Accounts)          // watchlist
| where Computer !in (Tier0Hosts)                  // watchlist
```

Also: 5136 on AdminSDHolder, Tier 0 ACLs and logon-restriction GPOs; 4662 replication rights
(DCSync); Protected Users / Authentication Policy Silo drift; LAPS coverage gaps; new attack
paths from BloodHound / PingCastle runs.

### Action catalog by risk

| Action | Ceiling | Rationale |
|---|---|---|
| Remove **unapproved** member just added to a Tier 0 group | **L3** → L4 after track record | Speed matters most here; re-adding is trivial. Gated on: not in baseline, no approved change, not added via PAM |
| Disable stale (> 90 days) Tier 1/2 admin account | L3 | Reversible, low impact |
| Isolate host where a Tier 0 credential was exposed (MDE) | L3 | Reversible, disruptive |
| Disable Tier 0 account with compromise indicators | L2–L3 | Reversible, but may lock out a legitimate admin |
| Fix logon-restriction GPOs, ACLs, Protected Users membership | **L2 — PR only** | Protected Users breaks NTLM/delegation; GPO errors cascade |
| Password reset on a compromised service account | L1 | Irreversible — breaks dependent services |
| krbtgt reset, trusts, schema, AdminSDHolder, DC configuration | **Never autonomous** | Domain-wide and partly irreversible |

### Gates
- **Blast radius = tier + object count.** Tier 0 objects always need higher `required_confidence`.
- **Reversibility** from catalog: disable / group removal fast; password reset infinite.
- **Time gate** (§1.3) — the change-correlation check is mandatory for Tier 0; a PAM-approved
  elevation counts as an approved change.
- **Trigger** is a Sentinel incident (Defender for Identity + analytics rules), never a single
  raw 4728 event — forged or replayed events are an attack on the agent itself (§1.2).

### Verification
Re-query the object on **every DC** and confirm replication converged
(`Get-ADReplicationAttributeMetadata`). A fix on one DC that has not replicated is not a fix.
"Healthy" for the 3-in-a-row rule (§1.7) means every DC agrees on that poll.

### Audit
Sentinel custom table, written by an identity different from the executor, so the agent cannot
alter its own record.

---

## 6. Use case: Internal AD CS / PKI

**Two tracks: PKI operational health (strong automation fit) and certificate-abuse security
(strong detection, narrow action).**

### The constraint that drives the design

An enterprise issuing CA is **Tier 0**: whoever controls the CA or a vulnerable template can
issue a certificate that authenticates as any account, including Domain Admins. Everything in
§5 applies — tier split, gMSA, JEA, separate audit identity. The **offline root CA is never in
scope**; it stays air-gapped by design.

Each issuing CA gets **two JEA roles**, so the frequent, harmless action cannot be used to reach
the sensitive one:

| JEA role | Allowed commands | Used by |
|---|---|---|
| `PKI-Ops` | `certutil -CRL`, `Restart-Service CertSvc` | Track A actions |
| `PKI-Contain` | `certutil -revoke <serial> 6` (reason **6 = certificateHold only**), `certutil -CRL delta` | Track B containment |

### Track A — PKI operational health

Most PKI outages are maintenance failures: an expired CRL fails revocation checks, which takes
down VPN, 802.1X Wi-Fi, smartcard logon and TLS clients that check revocation.

| Symptom | Signal | Action | Ceiling |
|---|---|---|---|
| `CRL_EXPIRING` | CRL `NextUpdate` within 20% of its validity period | Republish CRL (`certutil -CRL`) | **L4** — idempotent, low risk |
| `CDP_UNREACHABLE` | HTTP fetch of CDP / AIA URLs fails or serves a stale CRL | Re-copy CRL to CDP web servers / fail over CDP | L3–L4 |
| `CA_SERVICE_DOWN` | `CertSvc` stopped or requests failing | Restart `CertSvc` | L3 |
| `OCSP_UNHEALTHY` | Responder errors or stale responses | Restart responder / refresh OCSP signing cert | L3 |
| `LEAF_EXPIRING` | Issued certs for critical services expire < 30 days | Trigger autoenrollment (`certutil -pulse`) or open ticket | L2–L3 |
| `CA_CERT_EXPIRING` | CA certificate near expiry | **Recommend only** — key ceremony / HSM | L1 |

Signals come from a scheduled probe (fetch CDP/AIA URLs, parse `NextUpdate`), the CA's
Application event log, and service state — shipped to Log Analytics via the Azure Monitor Agent.

**Verification:** `certutil -URL` / `certutil -verify` succeed, the published CRL's `NextUpdate`
is in the future, and every CDP endpoint serves the new CRL.

### Track B — certificate abuse (ESC1–ESC8+)

**Prerequisite:** CA auditing enabled (`certutil -setreg CA\AuditFilter 127` plus the
*Audit Certification Services* subcategory), Security log forwarded to Sentinel.

| Event ID | Meaning | Detects |
|---|---|---|
| 4886 / 4887 / 4888 | Request received / issued / denied | **ESC1 abuse:** low-privilege requester receives a cert whose SAN names a privileged account |
| 4899 / 4900 | Template updated / template security updated | ESC4 (template ACL abuse) |
| 4882 / 4890 | CA permissions / manager settings changed | ESC7 (CA officer/manager abuse) |
| 4870 | Certificate revoked | Audit trail for containment |

```kql
// ESC1 abuse: issued certificate whose request attributes carry a SAN,
// requested by an account outside the PKI admin watchlist.
// Sketch — EventData field names must be validated against your CA's events.
SecurityEvent
| where EventID == 4887
| extend ed = parse_xml(EventData)
| extend requester  = tostring(ed.EventData.Data[1]["#text"]),
         attributes = tostring(ed.EventData.Data[3]["#text"])
| where attributes has "SAN:" or attributes has "upn="
| where requester !in (PkiAdmins)          // watchlist
| project TimeGenerated, Computer, requester, attributes
```

**Posture drift** (scheduled scan with Certipy / Locksmith / PingCastle, diffed against a git
baseline): templates with *enrollee supplies subject* + client-auth EKU + broad enroll rights
(ESC1), writable template ACLs (ESC4), `EDITF_ATTRIBUTESUBJECTALTNAME2` set on the CA (ESC6),
HTTP web enrollment accepting NTLM (ESC8).

### Track B action catalog

| Action | Ceiling | Rationale |
|---|---|---|
| **Hold** suspicious cert (`certutil -revoke <serial> 6`) **+ publish delta CRL immediately** | **L3** → L4 after track record | Reason 6 is the only reversible revocation (`certutil -revoke <serial> unrevoke`). Without the immediate delta CRL, DCs keep honoring the cert until their cached CRL expires |
| Disable the account that requested the rogue cert | L3 | Same containment as §5 |
| Stop issuing an exploited template (remove from CA's issued templates) | L2–L3 | Reversible, but breaks legitimate enrollment for that template |
| Fix template flags / ACLs, clear ESC6 flag (needs `CertSvc` restart) | **L2 — PR only** | Template changes affect every enrollee |
| Permanent revocation (any reason other than hold) | L1 | Irreversible |
| Offline root CA, CA key renewal, CA security descriptor changes | **Never autonomous** | Air-gapped by design; key operations need a multi-person ceremony |

**Persistence caveat:** revoking a certificate does not remove every persistence path. Pair
containment with a check that DCs enforce strong certificate mapping (KB5014754, full
enforcement) — otherwise a forged or stolen certificate can still be mapped weakly to an account.

### Gates
- **Blast radius:** number of enrollees / services depending on the template or CA affected.
- **Reversibility:** hold = reversible; any other revocation = infinite → L1.
- **Time gate (§1.3):** template edits and CRL maintenance usually run in change windows — the
  change-correlation check is mandatory.
- **Hard gate in code:** the containment role may only use reason code 6; any other reason is rejected
  before the executor is called.

### Verification
- **Hold:** the serial appears in the newly published delta CRL, every CDP serves it, and no
  further 4887 events appear for the same template + requester.
- **Template disable:** template absent from the CA's issued list; no new issuance from it.

---

## 7. Use case: Entra ID Enterprise Applications & App Registrations

**Two tracks: credential and provisioning health (outage prevention) and app abuse (consent
phishing, persistence through service principal credentials, privilege escalation).**

Terms: an **app registration** is the application object (credentials, redirect URIs, requested
permissions); an **enterprise application** is its service principal (SP) in a tenant (consent
grants, app role assignments, SAML signing cert, provisioning, user assignment).

### The constraint that drives the design

The Graph permissions that let an agent *fix* apps are the permissions attackers use to take
over a tenant. `Application.ReadWrite.All` can add a secret to any privileged app;
`AppRoleAssignment.ReadWrite.All` can grant any app — including the agent — any permission.
Both are effectively Global Admin. So:

| Identity | Permissions | Purpose |
|---|---|---|
| Detection | `Application.Read.All`, `AuditLog.Read.All`, Log Analytics reader | Detect, diagnose, score, audit |
| Executor | Only the narrow permission each catalog action needs (table below) | Act |
| Approval-gated pipeline | `Application.ReadWrite.All` / `AppRoleAssignment.ReadWrite.All`, behind required reviewers | Actions that need tenant-wide write — **never** the agent's standing identity |

**The agent never holds rights to add credentials or app role assignments.**

### Track A — credential and provisioning health

| Symptom | Signal | Action | Ceiling |
|---|---|---|---|
| `SECRET_EXPIRING` | App registration secret / cert expires < 30 days | Notify owners + ticket; if the app reads from Key Vault, create the new secret and KV version | L2–L3 |
| `SAML_SIGNING_CERT_EXPIRING` | Enterprise app token-signing cert near expiry | Create a **new inactive** cert, send metadata to the SP owner. Activating it breaks the SP until they update | L2 |
| `SECRET_EXPIRED_IN_USE` | SP sign-in failures from an expired credential | Page owner with evidence | L1 |
| `PROVISIONING_QUARANTINED` | SCIM provisioning job quarantined / failing | Restart the job via Graph once the cause is cleared | L3 |
| `ORPHANED_APP` | No owners, or all owners disabled | Assign a fallback owner group | L3 |

```kql
// Rotation check: which credential key ID each SP is actually using.
// Safe to remove an old secret only after N days with zero sign-ins on its key ID.
AADServicePrincipalSignInLogs
| where TimeGenerated > ago(14d)
| summarize last_used = max(TimeGenerated), sign_ins = count()
          by AppId, ServicePrincipalName, ServicePrincipalCredentialKeyId
| order by AppId asc, last_used desc
```

**Verification:** sign-ins have moved to the new key ID; provisioning job state is healthy and
the next cycle completes; SAML SP owner confirms the new cert before activation.

### Track B — app abuse

```kql
// High-signal app changes: credentials, consent, permissions, owners, redirect URIs
AuditLogs
| where TimeGenerated > ago(1h)
| where OperationName has_any ("Certificates and secrets management",
                               "Add service principal credentials",
                               "Consent to application",
                               "Add delegated permission grant",
                               "Add app role assignment to service principal",
                               "Add owner to application",
                               "Add owner to service principal",
                               "Update application")
| project TimeGenerated, OperationName, Result,
          actor = coalesce(tostring(InitiatedBy.user.userPrincipalName),
                           tostring(InitiatedBy.app.displayName)),
          target = tostring(TargetResources[0].displayName),
          target_id = tostring(TargetResources[0].id),
          changes = TargetResources[0].modifiedProperties
```

| Symptom | Condition | Typical attack |
|---|---|---|
| `PRIVILEGED_APP_NEW_CREDENTIAL` | Credential added to an app holding high-privilege permissions or a directory role, by a non-owner or outside a change | Persistence / escalation (Midnight Blizzard pattern) |
| `ILLICIT_CONSENT` | User consent to an unverified multi-tenant app requesting mail / files scopes + `offline_access` | Consent phishing |
| `HIGH_PRIV_GRANT` | `RoleManagement.ReadWrite.Directory`, `AppRoleAssignment.ReadWrite.All`, `Application.ReadWrite.All`, `Mail.ReadWrite` granted, or a directory role assigned to an SP | Privilege escalation |
| `REDIRECT_URI_RISK` | Wildcard, `http://`, or unowned / dangling domain added | Token theft / subdomain takeover |
| `PRIVILEGED_APP_OWNER_ADDED` | Owner added to a privileged app | Indirect control of the app's credentials |
| `SAML_OPEN_ASSIGNMENT` | SAML enterprise app with *assignment required* off | Any user can obtain a token |
| `SP_BEHAVIOR_ANOMALY` | SP signs in from new IP / country, or calls new Graph endpoints (`MicrosoftGraphActivityLogs`); risky workload identity (Workload ID Premium) | Stolen SP credential in use |

### Action catalog

| Action | Ceiling | Executor permission | Rationale |
|---|---|---|---|
| Revoke illicit delegated grant (delete `oauth2PermissionGrant`) + revoke the user's sessions | **L3** → L4 after track record | `DelegatedPermissionGrant.ReadWrite.All`, `User.RevokeSessions.All` | Reversible; affects one user |
| Disable SP (`accountEnabled=false`) — suspicious, low-usage third-party app | L3 (L2 if business-critical) | Custom Entra role limited to disabling SPs (*verify available custom-role permissions*) | Reversible; stops all token issuance for every user of the app |
| Remove credential just added to a privileged app, no matching change | L3 — **via approval-gated pipeline** | `Application.ReadWrite.All` (pipeline only) | Fast containment, but must not undo a legitimate rotation |
| Remove high-privilege app role assignment | L2 — via pipeline | `AppRoleAssignment.ReadWrite.All` (pipeline only) | May break a real integration |
| Fix redirect URIs, turn on *assignment required* | **L2 — PR only** | Pipeline | Configuration belongs in code |
| Change tenant consent settings / admin consent workflow | L2 — PR only | Pipeline | Tenant-wide |
| Rotate a secret into Key Vault for an app the agent owns | L3 | `Application.ReadWrite.OwnedBy` + Key Vault Secrets Officer on one vault | `OwnedBy` limits writes to apps that list the agent as owner |
| Restart provisioning job | L3 | `Synchronization.ReadWrite.All` | Reversible |
| Delete an app or SP | **L1** | — | App registrations soft-delete for 30 days; a deleted SP loses assignments and configuration |

`DelegatedPermissionGrant.ReadWrite.All` can also *create* grants — restrict the executor's code
path to delete calls and alert on any grant it creates.

### Gates
- **Blast radius:** distinct users and sign-ins for the SP over 30 days.
- **Hard gate — critical-app allowlist:** first-party Microsoft apps and named business-critical
  apps can never be disabled by the agent, at any confidence.
- **Time gate (§1.3):** the change-correlation check is mandatory for every credential and
  permission action.
- **Reversibility:** disable / grant revoke = fast; deletion = L1.

### Verification
- **Disable / grant revoke:** no new sign-ins for the SP (or user + app) after the action; the
  grant no longer appears in Graph.
- **Credential removal:** key ID absent from the app object and from SP sign-in logs since removal.
- **Redirect URI / assignment fixes:** live app object matches the merged PR.

---

## 8. Rollout: prerequisites, kill switches and phases

Adapted from the workshop's Module 8 rollout requirements (Kubernetes-centric in the course)
to the Azure and on-prem platforms in this document.

### 8.1 What must exist before Phase 1 (shadow mode)

Build from layer 0 up — each layer depends on the one beneath it. Phase 1 starts only when
every layer is in place for the use cases in scope. If layer 0 fails (no tested runbook, fix not
callable by API), the use case is not ready for APO, however good the detection is.

| # | Course layer | Equivalent here | Per use case |
|---|---|---|---|
| 0 | **Readiness** (course readiness tree) | Organizational, not technical | SLOs with burn-rate alerts for APIM / Foundry · top-10 runbooks per use case **tested within the last 90 days** · every catalog fix callable by API (Azure REST, Graph, PowerShell behind JEA — no portal-only steps) · an executive sponsor · on-call engineers who agreed which classes to automate |
| 1 | **Prometheus** — RED metrics for every P0/P1 service | **Azure Monitor metrics + Log Analytics** | APIM gateway logs + `Capacity` · Foundry 429 / 5xx / token metrics · `SigninLogs`, `AuditLogs`, `AADServicePrincipalSignInLogs` · `SecurityEvent` from DCs and issuing CAs via Azure Monitor Agent · Defender for Identity |
| 2 | **Log aggregation** — structured, central | **One Log Analytics workspace behind Sentinel** | All KQL in §2–§7 runs here; the APO audit table lives here |
| 3 | **Alertmanager** — webhook entry point for APO | **Azure Monitor action groups + Sentinel automation rules** | Alert / analytics rule → webhook → APO runner (Function, Container App or Logic App) |
| 4 | **Kubernetes RBAC** — least privilege, never cluster-admin | **Scoped identities — never Owner, Global Admin or Domain Admin** | Read-only detection identity everywhere · managed identity scoped to one APIM / AI resource · per-action Graph permissions (§7) · gMSA + JEA endpoints for AD and AD CS (§5, §6) |
| 5 | **CI/CD** — every action command tested in staging first | **GitHub Actions / Azure DevOps pipeline exercising each catalog action in non-prod** | Dev APIM · test Foundry deployment · **separate test tenant** (Conditional Access, app registrations) · **isolated lab forest + lab CA** (AD tiering, AD CS) |

For identity and directory use cases, "staging" means a separate tenant or forest — a
non-production OU inside the real forest is not isolation.

### 8.2 Rollback: three switches, narrow to broad

Reach for the narrowest switch that stops the problem.

| Switch | Diagnose | Log | Act | Implementation | Example |
|---|---|---|---|---|---|
| **Disable one action class** | ✅ | ✅ | that class only stops | `enabled` flag per catalog entry, held in **Azure App Configuration feature flags** (flip without a deploy) | Turn off `adcs.cert_hold`; CRL republish keeps running |
| **Stop every action** (`ALL_GATES_BLOCK=true`) | ✅ | ✅ | ❌ | A **global gate evaluated first** in the gate orchestrator; blocks every proposal | This *is* shadow mode — Phase 1 runs with it on; Conditional Access stays on it permanently |
| **Turn APO off** (`APO_ENABLED=false`) | ❌ | ❌ | ❌ | **Disable the Sentinel automation rule / action-group webhook**; alerts route straight to on-call | Back to L1: humans paged as before |

### 8.3 Hardening beyond the course slide

1. **The agent cannot flip its own switches.** Feature flags and automation rules sit outside
   every APO identity's write scope. A compromised agent that can re-enable itself has no kill
   switch. Critical for the Tier 0 use cases.
2. **Tier 0 hard stop independent of APO code.** For AD (§5) and AD CS (§6), unregistering the
   JEA session configuration (`Unregister-PSSessionConfiguration`) halts every Tier 0 action even
   if the agent or its configuration store is misbehaving.
3. **Nothing is lost.** The audit record is written *before* switch evaluation, so every mode —
   including APO off — records the alert and where it was routed.
4. **Humans back fast (< 60 s).** Through Phases 1–2 the human paging route runs **in parallel**
   with APO rather than being replaced by it, so turning APO off removes the agent but never has
   to restore paging.
5. **Switch changes are audited and alerting.** Any flip of the three switches raises a Sentinel
   incident with the actor — a disabled kill switch is itself a security event.

### 8.4 Phases

Evidence gates every transition: never skip Phase 1, and do not advance on a date. The deck gives
two agreement thresholds for leaving shadow mode (> 75 % in Module 8, > 90 % over 30 days in the
Day 1 promotion criteria); this document uses **90 %**, because identity and PKI actions carry
more risk than pod restarts.

| Phase | Switch state (org ceiling) | Scope | Exit criteria |
|---|---|---|---|
| 0 | APO off (L1) | Build §8.1 layers 0–5; test every catalog action in non-prod | All layers in place; every action passes in staging |
| 1 — Shadow, 4–12 weeks | `ALL_GATES_BLOCK=true` (L2) | All six use cases: detect + propose + audit, no execution. Engineers label every proposal daily | **≥ 50 incidents** per use case; agent–human agreement **> 90 % over 30 days**; **zero blast-radius violations** in proposals |
| 2 — Supervised, 4–8 weeks | Global block off; per-class flags on only for listed actions (L3) | APIM revision rollback + backend failover; AD CS CRL republish; app secret-expiry notices (L2) | Per class: false positives **< 2 %**; **≥ 60 %** of in-scope incidents handled; 30-day success **> 95 %**; engineer approval |
| 3 — Expand | Add classes one at a time (L3) | Foundry failover; AD privileged-group removal and AD CS certificate hold via JEA; illicit-consent grant revoke. Formal catalog review per class; monthly calibration review | Same bar per class |
| 4 — Active | Promote individual classes (L4) | Only classes meeting the promotion criteria below | Quarterly calibration review and a rollback drill |
| — | Global block permanent for CA | Conditional Access stays at L1–L2 (PR-based) | — |

**Promotion criteria for a class (course trust signals)** — all must hold:

| Signal | Threshold |
|---|---|
| Occurrences of the class | ≥ 100 |
| Agent–human agreement in shadow / supervised | > 90 % over 30 days |
| Gate violations | Zero in the last 100 runs |
| Undo time | < 5 min, tested (`IMMEDIATE` class) |
| Blast radius | ≤ 5 % |
| Sign-off | Tech lead for the platform, recorded as a change |

**Step back down** a level as soon as any signal slips (for example, approval rate below 85 % or
a gate violation). **Gates tighten as scope expands; they never relax.**

The audit log from Phase 1 is the training data for the H component of the confidence formula —
the same role `apo_audit.jsonl` plays in Lab 2.

### 8.5 Governance and change control

- **Every autonomous action is an ITSM change record** (ServiceNow / Azure DevOps): what changed,
  approver (human at L3, gate-config owner at L4), outcome, link to the audit record.
- **The CAB approves the gate configuration, not each action.** Catalog entries and gate
  thresholds go through normal change control; actions inside them run as pre-approved changes
  because the gates enforce the policy.
- **Gate and catalog changes need two engineers and a 24-hour delay** before they take effect —
  implemented as a protected branch with required reviewers and a scheduled deploy, so nobody
  (and no agent) can loosen a gate during an incident.
- **Explainable and reversible:** every decision can be explained from its audit record alone.
- **Regulatory scope** (from the deck; applicability is a question for compliance — see §9):
  EU DORA for financial entities (production changes logged with approver), HIPAA (no protected
  health data in telemetry or audit), FedRAMP (isolated environment for agents), EU AI Act
  Article 9 (risk assessment for autonomous actions).

### 8.6 Observing APO itself

APO needs its own dashboard (Azure Workbook or Grafana over the audit table):

| Area | Metrics | Watch for |
|---|---|---|
| Decisions | decision rate, approval rate, gate block rate **per gate** | A gate that never blocks, or always does |
| Quality | MTTR p50/p99 per class vs the human baseline; false-positive rate (actions that did not fix the incident); average C | Average C trending down = drift → recalibrate |
| Agents | timeout rate, error rate, replica count, p99 latency | Rising timeouts before the circuit breaker opens |
| Cost | action cost per hour; cost avoided (MTTR saved × engineer hourly cost × incidents) | Spend from recommended-only actions being approved by habit |
| Security | switch flips, gate-config changes, unusual gate-bypass patterns, class changes | Forward to Sentinel; each is a potential incident |

### 8.7 Success metrics and the human side

| KPI | Target | Note |
|---|---|---|
| MTTR | 80 % reduction for in-scope classes within 6 months | Median, same class, before vs after |
| Correct actions | > 90 % verify within 10 min | Per class, never aggregated; pause expansion below 85 % approval |
| On-call toil | Measurably down within 12 months | Tracked in post-incident reviews |
| Engineer trust | Monthly survey ("How confident are you in APO's decisions?") | Low trust → more shadow data, clearer decision logs, narrower scope |

- Involve on-call engineers in gate design from day one and **never automate a class they have not
  agreed to automate**.
- Start with the classes the team finds most tedious (secret-expiry notices, CRL republish), not
  the most impressive ones.
- Share shadow-mode data weekly ("APO would have handled this one").
- On-call engineers gain new work: labeling outcomes, tuning thresholds, approving scope
  expansion. Plan time for it.

> **Gaps in the lab code** (fine for a workshop, must change before production):
> - None of the three switches exist. `GateOrchestrator` has no global block and
>   `action_catalog.yaml` has no `enabled` flag — a first gate reading `ALL_GATES_BLOCK` and a
>   catalog lookup before a proposal is built would cover both.
> - Lab 2 declares recovery on the first healthy poll; production needs 3 in a row (§1.7).
> - Lab 3 escalates only when *every* agent times out; production escalates below a 2-of-3
>   quorum, below C = 0.85 after arbitration, and never lets two `UNKNOWN` findings corroborate (§1.6).

## 9. Open questions
- Who holds authority to flip each of the three switches, and how fast can they be reached?
- Which on-call system (PagerDuty, ServiceNow, Teams) is the parallel human route in Phases 1–2?
- Are a test tenant and an isolated lab forest / lab CA available for §8.1 layer 5?
- Which change-management system is authoritative for the change-correlation gate?
- Is a Tier 0 host available to run the JEA endpoint, or does AD action stay at L1–L2 until one is?
- Is Foundry traffic already fronted by APIM? (Use cases 1 and 2 share failover actions if so.)
- Does the 7-year immutable retention (§1.8) match our records policy, and is the SOC 2 / ISO mapping accepted by compliance?
- Which regulatory regimes in §8.5 actually apply (EU DORA via any EU entities, state insurance regulations such as NYDFS Part 500, HIPAA for any health data)?
- Can ServiceNow accept APO actions as pre-approved changes with the gate-config owner as approver (§8.5)?
- What is the measured human MTTR per class today? Without it, §8.7 has no baseline.
- Who are the tech leads that sign off promotion for each platform (§8.4)?
- Is CA auditing (`AuditFilter 127`) already enabled and forwarded to Sentinel on every issuing CA?
- Are DCs in full enforcement for strong certificate mapping (KB5014754)?
- Is Workload ID Premium licensed (risky workload identities, CA for workload identities)?
- Which apps go on the critical-app allowlist, and who owns that list?
- Are `AADServicePrincipalSignInLogs` and `MicrosoftGraphActivityLogs` already sent to the Sentinel workspace?
