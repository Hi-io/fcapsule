# Investigation Experience Review

> Historical UI review. Use the [current Operations guide](../operations.md) for today's workflow.

Review snapshot: 20 September 2026. Later storage/export fixes and documentation reconciliation are recorded in [product_audit.md](product_audit.md). Historical runs below are not repeated automatically by subsequent visual changes.

## Goal

Help an on-call engineer understand the affected workload, distinguish observations from possible causes, and choose a useful next check without reading every retained field. The evidence remains available for verification and export.

## Baseline Audit (20 September 2026)

Current deployed pages were inspected in the browser before editing. Screenshots are stored locally under `local_reports/ux-review/` and are intentionally excluded from Git.

1. **Operations and expanded investigation** (`01-investigation-before.png`): the queue, correlated-episode heading, signal list, report heading and summary repeat the same title. Multiple bordered surfaces obscure the relationship between a row and its expanded content. Every domain is displayed at once. The selected report looks like an action button rather than a selected state. Charts lack a visible time range. The manual AI button interrupts the investigation flow.
2. **Targets** (`02-targets-before.png`): connection health and coverage are useful. The ungrouped application table is difficult to scan across namespaces. Polling rebuilds editable forms, which risks overwriting unsaved input and keyboard focus.
3. **Settings** (`03-settings-before.png`): scope and labels are understandable. Keep the page focused on configuration; explain whether automatic analysis is enabled by the saved credential.

The screen also exposed a quality issue: the deterministic configuration hypothesis can be selected even without a matching configuration-error log. An automatic briefing must assess the evidence independently, explain its proposed mechanism, identify missing proof, and propose a concrete check with an expected result. A citation check verifies references, not the truth of a diagnosis.

## Implementation Decisions

- Make the episode title a keyboard-accessible disclosure with a directional indicator. Expand immediately with a short motion and one continuous accent border. Honour reduced-motion preferences.
- Keep a single compact signal selector. Distinguish the selected signal and preserve the selection during updates.
- Open on Overview: automatic AI assessment, observed impact and the first investigative action. Separate Evidence and Timeline into predictable tabs. Exports and diagnostics remain available without dominating the first view.
- Give the AI explicit sections for interpretation, likely mechanism, first check, expected finding, possible mitigation and uncertainty. Require retained evidence references and never execute suggested changes.
- Persist queued, running, ready and failed briefing states. Generate after report retention in a separate bounded worker so source capture remains responsive. Show missing credentials and retry states honestly.
- Show chart start/end times and the captured duration. Keep stable metrics accessible as context rather than implying that every value is a failure.
- Group coverage by cluster and namespace with expandable rows. Keep source settings intact while polling.
- Preserve focus, open disclosures and report tabs across background refreshes. Use explicit labels in addition to colour.

## Verification

Validate asynchronous generation, deduplication, failure handling and citation contracts with focused tests. Exercise disclosure, report selection, evidence details, archive/restore, exports, Targets and Settings in the deployed browser. Check narrow and desktop layouts and reduced motion. Screenshots establish visual findings; keyboard checks and runtime tests are required separately and do not imply a full accessibility certification.

### Results

- All 53 Python tests pass (`python3 -m unittest discover -s tests -q`). Two dependency-free browser-helper regression tests pass (`node --test tests/ui_helpers.test.cjs`). JavaScript syntax and Git whitespace checks pass.
- Deployed to the existing Kubernetes instance and exercised against the retained cluster incidents, not fabricated UI responses. Four briefings completed with the configured DeepSeek provider; queued/running states appeared before completion without requiring a page reload.
- Switched between the two reports in the worker episode, opened log examples and metric charts, followed an AI citation to its alert evidence, and exercised the timeline. Report selection and expanded evidence survive background updates.
- Archived the resolved worker episode and restored it. The queue returned to its original state; no retained incident was deleted. JSON export parsed successfully, and the capsule ZIP passed an integrity check and contained its AI briefing.
- Tested source connections successfully. All three sources reported healthy. An unsaved cluster-name edit survived background polling and was reverted without saving. Coverage displays 14 applications grouped into three namespaces, including Grafana.
- Keyboard testing exposed duplicate report controls inside collapsed episodes. Those hidden copies were removed; ArrowRight and Home now select the appropriate report tab. Enter collapses the selected episode, which remains closed after polling. Opening and closing animations target the open episode rather than the first row.
- Saved and reviewed desktop screenshots for Targets, the investigation overview, and expanded log/metric evidence in `local_reports/ux-review/`. Settings retains its existing controls with automatic-analysis wording. No JavaScript errors were observed during the final interaction checks.

### Analysis Quality Findings

The first real-model run treated low sampled memory as evidence against a short-lived OOM and suggested CPU saturation from log wording. The prompt now distinguishes missing samples from contrary evidence, requires CPU measurements for saturation claims, and prioritises termination state when investigating an OOM alert. Another response guessed a namespace because the prompt omitted the available workload identity. Cluster, namespace, event time and configuration inventory are now supplied explicitly as authoritative context. These findings were recorded rather than hiding unsuccessful iterations.

Briefings are investigative guidance, not verified root causes. Citation validation proves that referenced evidence exists; it does not prove every assertion or suggested action. Retained historical windows may omit the peak, terminal state, queue acknowledgement, or other decisive evidence. Conditional mitigation and a concrete confirming check are preferable to a confident but unsupported fix. No model-generated remediation is executed.

The final OOM rerun completed successfully without the invented namespace. It recommended checking the affected worker's recorded termination reason, exit code, finish time and configured memory limit; it explicitly noted that sampled metrics may miss a brief peak. This is a useful next step, not proof that the reported OOM mechanism is correct.

### Remaining Validation Limits

The browser's viewport override did not change the observed 1280px viewport, so narrow-screen visual validation is not claimed. The temporary override was reset. Responsive CSS and reduced-motion handling are present, but this pass is not a full device or accessibility audit. No live incident was permanently deleted, no credentials were replaced, and no new workload fault was induced in this UX pass; lifecycle deletion and asynchronous capture behaviour are covered by isolated automated tests.
