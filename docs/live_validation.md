# Live Integration Validation

## Scope

This record describes one controlled validation against the local Kubernetes
integration. It is not a production reliability claim, a general model benchmark or
an accuracy guarantee. The scenario was selected because it exercises a realistic
observability failure in which the application continues running while Prometheus
cannot discover its metrics target.

The validation used the configured `deepseek-v4-pro` investigator with the then-default
per-investigation limits: one model-selected check, 12,000 total tokens and 2,100
tokens for each complete model input. The optional image and audio specialists were
validated separately with minimal canaries; they were not used for this run.
New installations now default to 3,200 input tokens per call, with the same 12,000
total reserve. This recorded run remains a 2,100-token evaluation, not a result under
the newer default.

## Controlled Scenario

1. The Lab Service `fcapsule-lab/lab-app-metrics` normally exposes the label
   `fcapsule.io/app-metrics: "true"` for the application `ServiceMonitor`.
2. The label was temporarily changed to `"ture"`. Workloads, endpoints, images and
   pod readiness were left untouched.
3. Prometheus stopped discovering the application metrics endpoints. The
   `LabApplicationMetricsDiscoveryMissing` rule fired after its
   `absent_over_time(...[1m])` window and 30-second `for` period.
4. FCAPSule synchronized its configured live sources and captured the alert as a
   workload-scoped incident for `orders-api`.
5. The original Service label was restored after the investigation, and the rule
   resolved. A current `up{namespace="fcapsule-lab",service="orders-api"}` query
   returned `1`.

The Lab manifests already declare `release: prometheus` on both ServiceMonitors.
The live resources were reconciled to that manifest label during validation because
the running Prometheus instance selects ServiceMonitors by that label. This is an
integration prerequisite, not an FCAPSule inference.

## Captured Material

The captured incident retained:

- 1,999 source log lines before deterministic reduction;
- four selected metric series;
- alert rule labels and rule logic;
- the current workload and ConfigMap snapshot; and
- the generated report and capsule archive.

The investigator automatically preserved two read-only observations:

- `Q001 workload_state`: current workload state, resource limits and referenced
  configuration;
- `Q002 scrape_discovery`: Prometheus target view, ServiceMonitor selectors, matched
  Services and current pod labels.

The second observation showed that the application ServiceMonitor expects
`fcapsule.io/app-metrics: "true"` on Kubernetes Services, while no Service matched
the selector during the fault. It did not claim that the typo was proven from the
later current state.

## Investigation Outcome

The final assessment was accepted under policy `episode-investigation-1.11`. Its
main supported hypothesis was a Prometheus target-discovery selector mismatch rather
than an application-health failure. It cited the retained alert and `Q002`, weakened
the application-outage alternative, retained a transient-infrastructure alternative
as unresolved, and gave a safe next action: compare monitoring selectors with the
current labels.

This is intentionally a cautious conclusion. The system distinguishes evidence of a
missing target from proof of a specific human configuration change, and it notes that
current Kubernetes state can differ from the state at alert time.

The episode contained several historical occurrences of the same alert identity.
They were treated as recurrence evidence rather than artificial causal edges. This
avoids forcing a relationship between repeated instances of one alert merely because
they share an episode.

## Token and Protocol Result

The completed run used three provider calls and reported 5,658 total tokens:

| Phase | Prompt tokens | Completion tokens | Total tokens |
|---|---:|---:|---:|
| Initial structured decision | 1,452 | 77 | 1,529 |
| Corrected final decision | 1,481 | 541 | 2,022 |
| Evidence review | 1,587 | 520 | 2,107 |
| **Total** | **4,520** | **1,138** | **5,658** |

The first response selected an automatic `scrape_discovery` check that had already
completed. FCAPSule recorded that protocol correction, did not rerun the source query,
and used the next already-budgeted decision turn to produce a final assessment. The
run reserved 8,197 tokens and remained below the 12,000-token ceiling; the provider
reported no unbudgeted token excess.

This is the expected token-control behavior: selected evidence and bounded tool
results are sent rather than raw logs, each full request fits the prompt cap, and a
repeated check cannot multiply source work or model-call allowance.

## Reproduction Notes

Use the Lab control plane or the documented scenario to change only the
`fcapsule.io/app-metrics` label, wait for the Prometheus rule, then run a source sync
from FCAPSule. Always restore the Service label after the run. Validate recovery with
the alert state and an `up` query rather than assuming that one particular Prometheus
target-list representation is complete.

See [AI investigation techniques](ai_investigation_techniques.md) for the bounded
agent design and [multimodal evidence](multimodal_evidence.md) for the optional
operator-supplied evidence workflow.
