# APO Beyond Kubernetes — Six Enterprise Use Cases

**Status:** Design proposal (not implemented) · **Author:** Brian Dragun · **Date:** 2026-11-20
**Builds on:** Labs 1–2 (`lab1/gate_starter.py`, `lab1/diagnostic_agent_starter.py`, `lab2/autonomous_agent_starter.py`)

The lab loop — **detect → diagnose → gate → execute → verify → audit** — does not depend on
Kubernetes. This document maps it onto six platforms and sets an autonomy ceiling for each,
based on blast radius, reversibility, and what a compromised agent identity could reach.

| # | Use case | Fit | Autonomy ceiling | First deliverable |
|---|---|---|---|---|
| 1 | Azure API Management (APIM) | Strong — closest to the lab | L3–L4 | Revision rollback + scale-out responder |
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
| Gates | cost, % pods, reversal time, peak hours | Same four, re-expressed per platform, plus a **change-correlation gate** (below) |
| `VerificationLoop` | P90 < 1.5× baseline | Per-action post-condition (sections below) |
| `AuditLogger` | local JSONL | Log Analytics custom table → Sentinel; written by an identity separate from the executor |

### New gate common to all use cases: change correlation

Block any action on a resource that has an **approved, in-window change** in ServiceNow / Azure
DevOps. Without it, the agent "remediates" legitimate admin work. The gate fails closed: if the
change system is unreachable, the action is blocked.

### Autonomy ceiling per catalog entry

The lab maps confidence to autonomy (C ≥ 0.80 → L4). Here each catalog entry also carries
`max_autonomy_level`; the effective level is `min(level_from_confidence, max_autonomy_level)`.
High confidence can never lift a dangerous action above its ceiling.

### Agent identity (all use cases)

- Managed identity / gMSA per agent **and per tier** — never a shared admin account.
- Detection identity: read-only. Execution identity: only the rights its catalog needs.
- Execution credentials never leave the tier they control (see §5).

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
    risk_class: low
    executor: azure_rest            # make the previous revision current
    reversal_time_seconds: 60
    required_confidence: 0.80
    max_autonomy_level: L4
    cooldown_seconds: 900
  - action_id: apim.scale_out
    risk_class: medium
    executor: azure_rest            # +1 unit
    estimated_cost_per_hour_usd: <from SKU price>
    reversal_time_seconds: 1800     # scale-in takes time
    required_confidence: 0.80
    max_autonomy_level: L3          # adds spend → human approves
  - action_id: apim.backend_failover
    risk_class: medium
    executor: azure_rest            # switch backend pool / trip circuit breaker
    reversal_time_seconds: 120
    required_confidence: 0.85
    max_autonomy_level: L4
```

### Gates
- **Cost:** scale-out unit price vs hourly budget.
- **Blast radius:** % of total gateway traffic served by the affected APIs.
- **Reversibility:** catalog `reversal_time_seconds` vs SLO.
- **Time:** block during business-critical windows; change-correlation gate always on.

### Verification
Re-run the KQL above every 30 s for up to 10 min; recovered when `error_rate < 0.01` and
`p90_ms < 1.5 × baseline`. Ingestion latency is minutes, not seconds — wait one ingestion
window before the first poll (same lesson as Lab 2's settle delay).

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
    reversal_time_seconds: 120
    required_confidence: 0.80
    max_autonomy_level: L4
  - action_id: aoai.rebalance_tpm
    executor: azure_rest            # move TPM between deployments in the same quota pool
    reversal_time_seconds: 300
    required_confidence: 0.85
    max_autonomy_level: L3
  - action_id: aoai.version_rollback
    executor: azure_rest
    reversal_time_seconds: 600
    required_confidence: 0.85
    max_autonomy_level: L3
  - action_id: aoai.add_ptu
    executor: none                  # recommendation only — commits significant spend
    max_autonomy_level: L2
```

### Gates
- **Cost gate is the main control.** PTU is a large commitment; anything that adds spend is capped at L2.
- **Blast radius:** % of consumers (APIM subscriptions / products) affected.
- **Data residency:** a failover target must be in an approved region — enforce as a hard gate,
  not a confidence input.

### Verification
429 and 5xx rates back under threshold on the serving deployment; latency within 1.5× baseline;
secondary deployment healthy under the shifted load.

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
- **Change correlation** with ServiceNow / ADO — mandatory for Tier 0.

### Verification
Re-query the object on **every DC** and confirm replication converged
(`Get-ADReplicationAttributeMetadata`). A fix on one DC that has not replicated is not a fix.

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
- **Change correlation:** template edits and CRL maintenance usually run in change windows — mandatory.
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
- **Change correlation:** mandatory for every credential and permission action.
- **Reversibility:** disable / grant revoke = fast; deletion = L1.

### Verification
- **Disable / grant revoke:** no new sign-ins for the SP (or user + app) after the action; the
  grant no longer appears in Graph.
- **Credential removal:** key ID absent from the app object and from SP sign-in logs since removal.
- **Redirect URI / assignment fixes:** live app object matches the merged PR.

---

## 8. Recommended rollout

| Phase | Scope | Exit criteria |
|---|---|---|
| 1 | All six in **shadow mode** (detect + propose + audit, no execution) | 30 days of proposals reviewed; false-positive rate measured per symptom |
| 2 | APIM revision rollback + backend failover at L3; ADCS CRL republish at L3; app secret-expiry notices at L2 | 10 approved executions, zero bad outcomes |
| 3 | Foundry failover at L3; AD privileged-group removal and ADCS certificate hold at L3 via JEA; illicit-consent grant revoke at L3 | Same bar per action |
| 4 | Promote individual actions to L4 where history supports it | Per-action H (historical match) ≥ 0.85 from audit data |
| — | Conditional Access stays at L1–L2 (PR-based) permanently | — |

The audit log from phase 1 is the training data for the H component of the confidence formula —
the same role `apo_audit.jsonl` plays in Lab 2.

## 9. Open questions
- Which change-management system is authoritative for the change-correlation gate?
- Is a Tier 0 host available to run the JEA endpoint, or does AD action stay at L1–L2 until one is?
- Is Foundry traffic already fronted by APIM? (Use cases 1 and 2 share failover actions if so.)
- Retention and immutability requirements for the audit table.
- Is CA auditing (`AuditFilter 127`) already enabled and forwarded to Sentinel on every issuing CA?
- Are DCs in full enforcement for strong certificate mapping (KB5014754)?
- Is Workload ID Premium licensed (risky workload identities, CA for workload identities)?
- Which apps go on the critical-app allowlist, and who owns that list?
- Are `AADServicePrincipalSignInLogs` and `MicrosoftGraphActivityLogs` already sent to the Sentinel workspace?
