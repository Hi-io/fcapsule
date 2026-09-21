# Evidence-Seeking Episode Investigations

## Purpose

The operator needs a defensible explanation and a useful next action, not another
alert paraphrase. FCAPSule first preserves an incident capture without requiring an
LLM. It then lets the selected model choose bounded, read-only checks against the
configured observability sources. Each observation is saved before another model
call. A final assessment distinguishes supported, weakened and unresolved
explanations and links to the observations used.

This is an application of established tool-augmented reasoning, not a claim of a
new foundation model or a proven causal discovery algorithm. The distinctive
product emphasis is preserving diagnostic evidence across a correlated episode
while sources and pod state can change. Application replication, failure replay,
automatic experiments and remediation are explicitly outside this feature.

## Implemented Techniques

### 1. Joint Episode Reasoning

The store groups alerts using the existing application/time correlation rule.
One investigation reads up to the latest twelve member reports. It merges duplicate
evidence by content identity and retains each original incident/evidence reference.
Up to eighty initial evidence records enter the investigation context. Individual
reports outside that bound remain accessible; the bound is declared to the model.

The LLM receives alert identities, retained alert conditions when available, selected
logs, metric findings and configuration. A multi-alert assessment must describe at
least one relationship: `possibly_related`, `same_symptom` or
`no_link_established`. Group membership is not evidence of a shared cause.

The output has one episode-level summary, likely mechanism, next action, expected
finding, uncertainty, one to three competing hypotheses and cited relationships.
There are no model-generated percentages presented as calibrated confidence.

### 2. Hypothesis-Directed Tool Use

For live captures containing log evidence, a bounded source-log search is required
before finalizing, so a log investigation is not simply handed back to the operator.
An unavailable query remains a recorded limitation, not fabricated evidence.

After early preservation, the model returns either a structured check request or a
final assessment. Each check includes a short diagnostic question and the
explanations it is intended to distinguish. FCAPSule validates the tool and its
arguments, executes the read-only operation, saves the result and supplies that
observation to the next call. It does not request or retain private model reasoning.

This follows the action/observation pattern described in [ReAct](https://react-lm.github.io/).
The implementation uses structured JSON decisions with a fixed dispatch table,
not unrestricted function execution or a generic shell agent.

The evidence reference space distinguishes initial capture records (`E...`) from
executed checks (`Q001`, `Q002`, etc.). Failed checks cannot be cited as successful
observations. A successful empty query is still an observation of that bounded
query, never proof that an event did not occur.

A separate final model call checks the draft against the same observations before
publication. It specifically removes unsupported recovery/remediation claims,
distinguishes historical from current state and checks metric interpretation.
The draft and revised result are retained. This is model-assisted consistency
review, not an independent verifier or a guarantee of factual correctness.
If the provider places `hypotheses` or `connections` alongside `assessment` instead
of inside it, only that known layout mismatch is normalized and recorded in the
call audit. Conflicting values are rejected. The original response is retained,
and all content, bounds and reference validations still apply.

#### Service-Scoped Discovery Alerts

Some Prometheus alerts describe a missing target and therefore identify a
Kubernetes `Service`, not a particular pod. When a logical application name differs
from the Kubernetes Service, the alert can supply `target_service` (or
`kubernetes_service`) while retaining its operator-facing `service` label. A shared
monitoring Service can additionally declare `target_workload`; that explicit
workload is the capsule anchor, while the Service remains evidence for the
discovery chain. Otherwise live capture resolves the Service identity through its
selector and proceeds only when the selected pods belong to one workload. An empty
selector result or a Service spanning workloads remains an explicit limitation;
FCAPSule does not guess from a matching name or text similarity.

### 3. Bounded Historical Recurrence Comparison

When retained episode metadata has the same application, affected resource and
normalized alert identity, FCAPSule exposes at most three earlier episodes to the
investigator. The candidate list is deterministic and kept separate from normal
time correlation: episodes remain independently auditable and are never merged.

The model must inspect exactly one supplied candidate before it can publish an
assessment for a recurring episode. The `historical_episode` tool returns a
bounded prior report excerpt, selected configuration/log/alert observations, and
an optional `prior_hypothesis` explicitly marked as earlier model output. That prose
is not independent evidence and cannot be cited. The tool cannot fetch arbitrary
old incidents or query new sources. The final comparison must cite the returned
observation and classify the result as `similar_mechanism`,
`changed_or_different`, or `insufficient_evidence`.

This is support for responder memory, not similarity-based root-cause proof. A
matching alert or interval can be coincidental; missing old report material must
lead to an explicit insufficiency rather than an invented comparison.

### 4. Reference Comparisons

`compare_baseline` compares the affected pod's incident window with a currently
ready replica of the same workload. If none exists, it uses the preceding equal
time window on the affected pod. It retains min/max/median, sample counts, temporal
bounds and bounded trend samples for both sides.

This is observational comparison, not an experiment. Historical readiness, traffic,
deployment version and limits may differ. The tool labels comparability unverified,
and the prompt prohibits treating a difference as proof of causality. No healthy
reference is fabricated when Prometheus returns no samples.

### 5. Diagnostic-Preserving Reduction and Exclusion Review

The capture pipeline still uses deterministic masking and exact template grouping,
not an LLM per log line. In addition to variable masking, it retains a small
allowlist of diagnostic fields in the grouping identity: exit code, errno,
status code, SQL state, reason, delivery disposition, payload encoding,
`max_connections` and `memory_limit`. Thus `exit_code=1` and `exit_code=137`
remain different groups while changing job IDs can still collapse.

Each group retains its first example and one closest to the alert; when those are
the same event, the last example is used. The existing scoring/domain quotas select
the initial evidence. These are heuristics, not guaranteed sufficient statistics.

The model can inspect up to twelve initially unselected retained log templates via
`review_omitted`, optionally filtering by short literal terms. It can also search
the source for incident-window examples. This makes selection revisitable without
resending every raw log. It is not a comprehensive search of all discarded source
data, and old capsules are not silently reprocessed with the new grouping rule.

### 6. Early Preservation Without Invented Retention

Every investigation first attempts a current Kubernetes workload snapshot, before
the first model call. Limits, requests, current state, previous termination reason,
exit code, restart count, images and referenced ConfigMaps can be useful and may
change during investigation. Results are timestamped as current observations.

The initial report/capsule has already been retained at this point. Additional
model-selected observations are persisted as they arrive. Prometheus/OpenSearch
source expiry remains **unknown**. FCAPSule's own cleanup policy is not a proxy for
source retention. A current pod snapshot is not retroactively described as the
configuration at the historical incident time. Last termination is only the last
retained termination and may have been replaced by a later restart.

## Tool Catalog and Bounds

| Tool | Allowed access | Main limits |
|---|---|---|
| `workload_state` | Kubernetes pods and referenced ConfigMaps in the episode namespace/workload | Four pods, sixteen returned records; no Secrets |
| `resource_history` | Fixed Prometheus expressions for CPU, memory, limits, throttling, restarts, readiness and last OOM termination | Captured pod; at most thirty-minute incident window; bounded summaries |
| `search_logs` | OpenSearch log queries for a captured pod and incident window | Three literal terms of at most eighty characters; 300 lines; twelve returned patterns |
| `compare_baseline` | Same workload's ready peer, otherwise preceding affected-pod window | Fixed resource expressions; explicit comparability caveat |
| `database_pressure` | Three MySQL-exporter connection/limit expressions plus `mysql_up` reachability in the episode namespace | Four series per expression; labels retained; no inferred dependency from namespace proximity |
| `dependency_evidence` | One selector-backed Service explicitly declared by the affected workload's endpoint environment configuration | Same namespace; one current pod; 200 log lines, fixed resource metrics and eight configuration records; no arbitrary endpoints |
| `review_omitted` | Stored, unselected log templates | Twelve returned candidates; no network access |
| `historical_episode` | One of up to three deterministic retained recurrence candidates | No source query; bounded saved alert/configuration/log observations; any `prior_hypothesis` is labeled non-citable earlier model output |

Namespace-level MySQL metrics are context, not automatic attribution to a database
dependency. The tool does not connect directly to MySQL or issue SQL. Configuration
is read for the captured workload and, when explicitly requested, one verified
dependency pod. Arbitrary cross-workload ConfigMap discovery is not implemented.

Policy 1.5 adds dependency investigation after a live schema-mismatch case exposed
an upstream blind spot: the orders episode saw inventory failures but could only
ask the operator to inspect inventory logs. `workload_state` now supplies endpoint
candidates from explicit environment variables and referenced environment
ConfigMaps. Only URL/HOST/ENDPOINT-shaped, non-sensitive keys are considered;
Secret references are not read. Explicit environment overrides are respected.
The model chooses a declared Service name, not a URL. Kubernetes Service selectors
resolve it to a current pod in the same namespace; ExternalName and selectorless
Services are refused. Arbitrary hostnames, IPs and cross-namespace DNS are excluded.

A shared ConfigMap may declare endpoints that the application never uses. The
model must corroborate the dependency with application evidence; discovery alone
does not establish traffic or causality. A single current pod cannot represent all
replicas or establish historical Service membership. Results carry that limitation,
source failures, query window and source identity. Partial source failures retain
the available evidence. No recursive dependency crawl or database SQL is executed.
RBAC adds read-only Service access; the namespace and cluster checks still apply.

Reduction also preserves `mysql_error_code` as a categorical diagnostic field, so
SQL 1054 and 1205 do not collapse into one template. Relative changes from a quiet
baseline must not be interpreted as absolute resource-utilization percentages.
Consecutive lab runs also exposed mixed failure phases within a time-grouped episode.
The investigator is explicitly told that membership does not connect causes. Active
alerts no longer inherit an `ended_at` value from the capture-window boundary;
legacy active records are normalized in the model context. Resolution time and the
end of a telemetry query window are different observations.

Live literal searches reserve up to one quarter of their line budget for the
period before the latest member alert and the rest for matching logs after it.
Previously, ascending matches could fill the budget with older failures from the
same episode. Resource and database results now include a separate sampled summary
at or after that alert. A healthy earlier baseline is not evidence that a later
connection leak recovered. `mysql_up` identifies exporter collection failure;
it does not by itself establish that the MySQL process stopped.

The live CPU case identified PBKDF2 work correctly but suggested reducing its round
count. This is recorded as a recommendation-quality failure: reducing cryptographic
cost can weaken security. The investigation and final review instructions now prefer
reversible load controls and prohibit weakening cryptography, authentication, TLS,
validation or durability as a performance shortcut. Queue-data deletion likewise
requires preservation and an explicit operator decision. These are model instructions,
not a formal guarantee of safe advice; the product never executes remediation.

Policy 1.6 also separates alert-detection timestamps from recorded termination
times and requires consistent units when comparing a buffer, working set and
container limit. A late OOM alert must not imply that termination happened after
the associated backoff. A component allocation below the limit is not a measured
limit exceedance; runtime overhead and unobserved peaks remain distinct limitations.

If the last investigation decision exceeds the eight-reference display contract
using only known top-level references, the reserved final review call may select
a smaller valid citation set. The invalid draft is retained and never published
as a conclusion. No references are silently truncated, unavailable references stay
invalid, and the total provider-call budget does not increase. Final validation
still rejects an invalid reviewed response.

Live queries require an incident captured through the live integration, matching
configured cluster identity and allowed namespace. Imported cases use retained
evidence only. The model cannot provide URLs, PromQL, OpenSearch DSL, shell commands,
SQL, arbitrary paths or Kubernetes mutations. Configured sources remain a trusted
administrator boundary, not a multi-tenant authorization system.

The product default is one automatic preservation check, one model-selected check,
one final assessment and one evidence review. That normally means no more than three
provider calls. Operators can raise the number of model-selected checks to four, for a
maximum of six calls. The default per-investigation reserve is 12,000 tokens and the
default full-request input cap is 2,100 tokens. The latter includes system instructions,
the tool catalogue, citation IDs and selected evidence; it is not merely a cap on log
text. The completion limit in Settings applies per call. Calls request JSON output and
low reasoning effort. One schema repair can use a remaining call with reasoning
disabled; it does not increase the total call budget. Final evidence review also
disables private reasoning output. These controls follow the [DeepSeek API contract](https://api-docs.deepseek.com/api/create-chat-completion/).

Before each request, FCAPSule reserves its estimated full input plus the allowed
completion. A missing provider usage field therefore cannot create further unreserved
calls. If a provider later reports more tokens than the reservation, the excess is
charged to the attempt before another call can be made. The UI distinguishes provider
usage from this safety reserve. This is containment, not a guarantee that an upstream
provider will never bill hidden reasoning differently from its API output limit.
Repeated identical checks are rejected. A 420-second soft elapsed-time budget is
checked between calls, with a 90-second provider timeout; an in-flight call or source
operation can extend elapsed time beyond the soft budget. Two background workers
serve episodes. There is no currency budget or global provider rate limiter yet.

## Persistence and Lifecycle

`InvestigationService` coordinates work using the existing control-plane lock and
worker pool. A report fingerprint avoids rerunning an unchanged completed or
incomplete attempt automatically. A new episode member schedules a new joint
assessment after its report is ready. Members arriving during a running attempt
are coalesced into a follow-up attempt rather than parallel duplicate work.

State is atomically replaced under `state_dir/investigations/<episode-hash>.json`.
Statuses are `not_started`, `not_configured`, `waiting`, `queued`, `running`, `ready`
and `incomplete`. Startup resumes interrupted retained work for unarchived episodes.
A failed unchanged attempt requires explicit Reassess; it is not retried forever.

Completed/incomplete attempts copy `episode_investigation.json` into participating
capsules and rebuild the ZIP. Up to three earlier attempt records, including checks
and assessments, are retained in the state. Earlier usage totals accumulate beyond
that short history. Deleting an episode member invalidates the shared investigation
and removes its derived copies from surviving capsules so deleted evidence is not
resurrected there. External downloads are not changed by deletion.

The report API includes the current shared investigation. `GET` and `POST
/api/episodes/<episode-id>/investigation` inspect or explicitly start/reassess it.
The deterministic standalone CLI remains unchanged; automated source investigation
is a control-plane workflow. Legacy per-incident briefing files/API remain readable
for compatibility but are not the Operations assessment.

## Operator Presentation

- **Overview:** one episode conclusion, one next action, one expected finding,
  remaining uncertainty, citations and a compact progress column.
- **Explanations considered:** expandable competing explanations with their evidence.
- **How the alerts relate:** expandable multi-alert relationships.
- **Evidence:** executed observations, cited original capture records and the
  individually selectable alert's existing telemetry disclosures.
- **Timeline:** historical episode alerts and a separate agent-activity sequence.
  An investigation performed later is not placed into the historical failure timeline.
- **Export:** the investigation JSON and the derived capsule ZIP.
- **Usage:** small expandable input/output/total counts and model-call count.
  Provider-reported partial counts are marked incomplete; timeouts do not imply zero
  provider billing. Earlier-attempt totals are separate from the current attempt.

Queue timestamps are relative; exact local timestamps remain available on hover
and in report detail. Artifact cleanup dates still refer only to FCAPSule retention.

## Trust, Privacy and Validation

Resource observations carry explicit metric semantics alongside measurements. The
last-termination OOM reason is a state flag, not an event counter; sampled working
set is not a recorded memory peak. A flag/restart combination must not be dismissed
solely because sampled memory is low. These interpretations follow the
[kube-state-metrics contract](https://github.com/kubernetes/kube-state-metrics/blob/main/docs/metrics/workload/pod-metrics.md)
and [Kubernetes resource behavior](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/).
They constrain interpretation, not the observed outcome of any particular case.

Policy `episode-investigation-1.9` uses low reasoning effort for the already reserved
final review, with explicit byte conversion, component-versus-total memory and
termination-versus-alert time checks. It additionally requires a bounded, cited
historical comparison when a deterministic recurrence candidate exists. This
replaced a non-reasoning review after a real investigation repeated a false
below-limit memory comparison. The review has the same call and completion budget;
latency and reasoning-token consumption can increase. It is still model-assisted
consistency review, not a deterministic numerical validator. Original and revised
assessments must both remain in evaluation records, including unsuccessful corrections.

Telemetry is treated as untrusted input, and the prompt explicitly rejects
instructions embedded in it. Server-side tool dispatch restricts actions even if a
model ignores that instruction. No semantic validator can guarantee that every
accepted sentence follows from its cited observation. Citation validation proves
reference integrity, not entailment, causality or operational safety.

The provider receives selected evidence and additional scrubbed tool observations.
Heuristic masking now also covers quoted credential assignments and bearer values;
it remains incomplete DLP. ConfigMaps can contain secrets under innocuous keys.
Organizational approval, scoped permissions, protected state and a trusted network
remain required. See [data privacy](data_privacy.md).

Tests cover scope/argument rejection, missing sources, peer fallback, diagnostic
code preservation, recurrence-candidate bounds, usage accounting, invalid citations,
provider failure, bounded calls, full-request compaction, missing provider usage,
background progress, restart recovery and deletion races. These tests do
not establish general root-cause accuracy. Evaluation should compare the former
single-call briefing and the new investigator on the same incidents, recording
discriminating observations found, unsupported claims, operator usefulness,
latency and tokens, without selecting results to force a preferred hypothesis.

## Implementation Map

- `fcapsule/episode_investigation.py`: model protocol, budgets, validation and loop.
- `fcapsule/investigation_tools.py`: context construction and allowed query tools.
- `fcapsule/investigation_service.py`: scheduling, persistence and membership lifecycle.
- `fcapsule/adapters/`: source queries and runtime diagnostics.
- `fcapsule/processing/`: diagnostic template reduction and metric analysis.
- `fcapsule/ui/assets/app.js`: overview, references, observations and progress views.
