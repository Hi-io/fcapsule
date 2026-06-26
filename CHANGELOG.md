# Changelog

## 0.2.0 - 2026-06-28

### Added

- Explicit operational telemetry domain model across P1 evidence and capsules.
- Optional DeepSeek same-input comparison for `deepseek-v4-flash` and `deepseek-v4-pro`.
- Static local dashboard for multidomain evidence, evaluation metrics, and LLM comparison.

### Changed

- Documentation now distinguishes telemetry domains from media modalities such as text, audio, and images.
- P1 evaluation now records model-specific output quality, citation validity, domain coverage, usage, and latency when LLM comparison is enabled.

## 0.1.0 - 2026-06-22

### Added

- P1 file-based incident investigation pipeline.
- Synthetic failing-service case generator.
- Grounded capsule and evaluation outputs.
- Regression and unit test suite.

### Changed

- Replaced the original broad project guide with the reviewed P1 development guide.
- Correctly analyzes Prometheus-style counters using per-sample deltas.

### Verified

- Captured a synthetic checkout dependency failure that fired `CheckoutHighErrorRate`.
- Reduced 141 captured log events into four templates and seven representative lines.
- Passed all 15 unit and regression tests.
- Verified 100% operational signal preservation and evidence-ID grounding on `case_001`.
