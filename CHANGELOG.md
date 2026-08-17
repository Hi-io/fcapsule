# Changelog

## 1.0.0 - 2026-08-18

### Added

- Persistent SQLite control plane for applications, incidents, capsules, and model profiles.
- Operations console for application/source status, capsule history, storage reduction, model configuration, and evidence inspection.
- Incident Lab for configurable, real-time failure simulation and capsule generation.
- Multi-service checkout/inventory scenario with concurrent traffic, lock contention, retry amplification, pool saturation, and a three-alert FM sequence.
- On-demand trace availability probe with an explicit zero-raw-span retention policy.
- Final CLI commands: `serve`, `simulate`, `register`, and `status`.
- Domain-balanced evidence quotas and representative diagnostic signal groups.
- Detailed signal IDs in objective evaluation.
- Regression coverage for the simulator, control plane, HTTP views, store, and complete pipeline.

### Changed

- Product language now describes the complete FCAPSule 1.0 control plane.
- Operational domains are labeled as FM, PM, application logs, topology/configuration, on-demand traces, and AI reasoning.
- Evidence selection prevents correlated PM series from crowding diagnostic logs.
- Deterministic reasoning recognizes retry amplification and pool-pressure investigation paths.
- Local generated state moved to the ignored `.fcapsule/` directory.
- Documentation now describes the implemented control plane and pod deployment direction.

### Verified

- Default lab run produced thousands of logs, 21 PM series, and three firing alerts.
- Capsule generation achieved more than 99% representative-line reduction in the recorded reference run.
- Representative diagnostic signal preservation, citation grounding, and retention completeness reached 100% in the recorded reference run.
- Derived archives excluded normalized raw telemetry and raw traces.

## 0.2.0 - 2026-06-28

- Added explicit operational telemetry domains, same-input DeepSeek comparison, and a static review dashboard.
- Recorded model output quality, citation validity, domain coverage, token usage, and latency.

## 0.1.0 - 2026-06-22

- Added the initial file-based incident pipeline, synthetic case, grounded capsule outputs, baselines, and regression tests.
