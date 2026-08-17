# Evaluation Record and Review Form

## Verified Complex Reference Run

Scenario: `inventory-lock-contention` for `checkout-platform`.

Observed workload:

- 180 baseline requests;
- 240 incident requests;
- concurrency 24;
- 420 total checkout requests;
- 213 successful and 207 failed requests in the recorded run;
- final retry amplification: 2.014x;
- final checkout error rate: 49.29%;
- peak inventory pool utilization: 100%.

Captured operational evidence:

- 3 firing FM events:
  - `CheckoutRetryAmplification`;
  - `InventoryPoolSaturation`;
  - `CheckoutErrorBudgetBurn`;
- 2,320 structured logs;
- 21 PM series;
- checkout, inventory, database, and telemetry-pipeline entities;
- runtime configuration-change context;
- verified 900-second trace-source window;
- 1,266 ephemeral spans observed by the source;
- 0 raw spans retained by FCAPSule.

Capsule result:

- 8 log templates;
- 20 selected evidence items;
- 3 generated and verified hypotheses;
- 99.5% representative-line reduction;
- 95.4% estimated token reduction;
- 100% representative diagnostic signal preservation;
- 100% valid hypothesis citations;
- 100% retention-section completeness.

Counts and latency values may vary slightly because the workload uses live HTTP services, deadlines, and concurrent scheduling. Alert names, diagnostic groups, data policy, and acceptance thresholds are deterministic.

## What the Run Demonstrates

- A multi-stage incident can be captured without reducing the problem to one HTTP status.
- FM, PM, logs, topology/configuration, and trace availability contribute different evidence.
- Domain quotas prevent PM metrics from hiding diagnostic log patterns.
- The capsule can preserve the full alert sequence and representative failure groups while retaining only a few anonymized log lines.
- A tentative retry-amplification and pool-pressure hypothesis can remain grounded without claiming final causality.
- Raw traces are available during the source window but are not copied into capsule storage.

## Reviewer Form

Score each item from 1 (strongly negative) to 5 (strongly positive).

| Question | Score |
|---|---|
| Is the incident sequence clear? | 1-5 |
| Are FM, PM, logs, and trace status easy to distinguish? | 1-5 |
| Is the strongest evidence useful for starting investigation? | 1-5 |
| Are the hypotheses appropriately cautious? | 1-5 |
| Are next checks actionable? | 1-5 |
| Does the capsule appear meaningfully smaller than raw telemetry? | 1-5 |
| Is the Operations view understandable without project context? | 1-5 |
| Is the Incident Lab progression easy to follow? | 1-5 |

Record:

- reviewer role and observability experience;
- reviewed capsule ID;
- whether the scenario was known in advance;
- most useful evidence;
- confusing or missing evidence;
- preferred model result, when model comparison is enabled;
- suggested product improvement.

