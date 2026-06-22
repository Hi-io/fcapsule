# Changelog

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
