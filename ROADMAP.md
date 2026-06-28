# FCAPSule AI Roadmap

## P1: File-Based Evidence Capsule

- validated case folders;
- entity alignment and anonymization;
- log template reduction;
- metric anomaly detection;
- alert timeline;
- explainable evidence ranking;
- grounded hypothesis generation and verification;
- optional DeepSeek same-input comparison;
- Markdown/JSON capsule, evaluation, baselines, static dashboard, optional demo UI, and archive;
- synthetic incident capture and regression tests.

## P2: Live Read Adapters

- Prometheus range-query adapter;
- OpenSearch query adapter;
- credential and timeout configuration;
- snapshot-to-case export for reproducibility.

## P3: Alertmanager Trigger

- webhook receiver;
- alert label to entity resolution;
- automatic incident window selection;
- asynchronous capsule job status.

## P4: Evidence Attention Improvements

- learned or calibrated scoring weights;
- embedding-based semantic relevance;
- cross-case similarity;
- more explicit contradiction handling.

## P5: Rich Review UI

- local case browser;
- evidence inclusion/exclusion controls;
- side-by-side baseline comparison;
- model comparison history;
- reviewer feedback capture.

## P6: Retention-Aware Capsule Store

- immutable capsule versions;
- retention policy metadata;
- searchable evidence index;
- integrity and provenance checks.

## P7: Final Evaluation

- multiple synthetic and anonymized cases;
- model/method ablations;
- domain-expert review;
- latency and cost analysis;
- documented threats to validity.

## Optional Later Work

- Grafana panel links or snapshots;
- downstream RCA/AIOps agent integration;
- postmortem drafting;
- remediation recommendations with human approval.
