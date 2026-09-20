# Investigation Experience Review

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
