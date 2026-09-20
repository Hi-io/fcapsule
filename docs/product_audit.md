# Product Audit

Review: 20-21 September 2026. Scope: current deployed Operations, reports, Targets, Settings, artifact lifecycle and repository documentation. This is an implementation audit, not independent user research or a production security certification.

## Assessment

The existing information architecture is appropriate for an operational tool. Operations is incident-first, Targets combines connection health with namespace-grouped coverage, and Settings is narrow enough to remain one page. A fourth page would fragment the workflow without solving an observed need.

The interface is credible as a maintained open-source operational tool: restrained typography, functional navigation, meaningful status colors, compact controls and progressive evidence disclosure. Visual polish does not establish production readiness; the reference server still lacks authentication, authorization and TLS, and runs single-replica storage/jobs.

## Findings and Changes

| Area | Finding | Change / decision |
|---|---|---|
| Identity | Green split wordmark is disconnected from blue interaction styling | Unified graphite wordmark with a blue capture mark; local matching favicon |
| Navigation | Three views have clear ownership | Kept existing structure; added page-specific browser titles and a keyboard skip link |
| Color | Healthy green must not remain on a disconnected UI | Error indicator now changes with the Disconnected label; success, warning and failure remain labeled |
| Investigation | Episode expansion, report selector and three investigation tabs form a clear hierarchy | Preserved; no additional data panels or marketing copy |
| Export | Links do not explain artifact size, location or lifetime | Compact menu shows actual JSON/ZIP size, cleanup eligibility and expandable server-side directory; Escape/outside click closes it |
| Retained reports | Reading a saved report unnecessarily reloads the original case | Read current report JSON directly; legacy rebuild can use retained evidence when the source directory is gone |
| Targets | Connection testing rerenders and can discard unsaved fields | Test saved connections without replacing the form; visible progress/result and disabled duplicate action |
| Settings | Retention and automatic model configuration are sufficient | No new controls; clarified actual behavior in documentation |
| Repository | Several current guides still describe the old simulator, manual briefing, absent live adapters or raw-free storage | Updated current guides; added a documentation index; labeled concept/research and historical evaluations |

The capture mark uses the bundled Lucide scan-line icon, not a commissioned or exclusive trademark. It visually suggests bounded capture without adding an unrelated illustration. Green remains meaningful for healthy/resolved states instead of carrying the brand everywhere.

## Storage Audit

- SQLite stores metadata; live capture files and derived artifacts are separate directories on the state volume.
- Live source responses are bounded but retained on disk until incident cleanup. They are not just an in-memory transport and may contain sensitive data.
- ZIP exports use an explicit derived-file allowlist. Representative log lines and PM chart values are still present; "derived" does not mean "free of sensitive data."
- Retention defaults to 30 days from capture and includes archived incidents. Cleanup is snapshot-driven, not a precise deletion timer. Export shows eligibility rather than promising a deletion instant.
- External input directories outside managed state are not deleted. Downloaded copies and backups need their own retention controls.
- Current-format reports remain readable after source capture disappears. Rebuilding without the original metrics cannot recover raw series that were not retained.
- UI-saved keys exist in plaintext in the ignored state-directory `.env`; they are not in SQLite, settings responses or archives.

These boundaries are documented in [data privacy](data_privacy.md), the [data contract](../DATA_SCHEMA.md) and the [operating guide](operations.md). A shorter staging TTL and production access controls remain explicit roadmap items, not implied features.

## Evidence and Validation

Screenshots are retained locally under `local_reports/product-audit/`, outside Git. Initial screenshots covered Targets, the expanded report and Settings. Full-page capture produced a layout artifact in one queue image; it was not used as evidence of a UI defect. Viewport captures and DOM geometry were used for the actual comparison.

Verification completed:

- 57 Python tests passed, including retained report access after source deletion, fallback rebuild, incident lifecycle, archive contents and HTTP routes.
- 5 Node helper tests passed, including export sizes/path escaping and a connection-test regression that forbids replacing the editable form. JavaScript syntax checks passed; frontend tests now run in CI.
- 40 local Markdown links resolved with no missing targets.
- Read-only checks retrieved 41 existing report JSON files and their ZIPs from the deployed service. File lengths matched export metadata; ZIP report contents matched the API report; archive entries matched the derived-file allowlist.
- Prometheus, OpenSearch and Kubernetes source connection checks succeeded; `/healthz` returned `ok`.
- Browser review at 1280 x 720 and 390 x 844 covered the three pages, report expansion, individual alert selection, investigation tabs, export metadata/storage disclosure and Escape dismissal.
- A temporary unsaved cluster-name edit survived Test connections. It was restored without saving; no cluster configuration was changed for that check.
- Mobile review exposed left-clipped export placement. The menu is now anchored to the report navigation width; its final bounds were x=35 to x=342 inside the 390px viewport. No page-wide horizontal overflow was observed in the three narrow layouts; coverage tables retain their own horizontal scrolling.
- The final capture mark computed to the intended blue (`rgb(40, 92, 206)`); an inherited old green span rule was removed. Duplicate resolved wording in Timeline was also removed.
- No browser warning/error entries were reported in the final narrow-screen session. Both deployment rollouts completed successfully.

Final captures: `08-mobile-export-fixed.png`, `09-mobile-targets.png`, `10-mobile-settings.png`, `11-desktop-targets-final.png` and `12-desktop-operations-final.png`. These checks are not a full accessibility certification, load test or independent usability study. Destructive retention/deletion behavior was tested in isolated automated cases, not by deleting real cluster incidents.

No new fault was injected and no model-comparison experiment was run for this audit. Existing retained cluster reports provide realistic UI data; isolated tests exercise source expiration without deleting real incident evidence.

## Design References

- [Carbon status indicators](https://carbondesignsystem.com/patterns/status-indicator-pattern/): status needs recognizable labels as well as color.
- [GitHub repository practices](https://docs.github.com/en/repositories/creating-and-managing-repositories/best-practices-for-repositories): readable project purpose, operating instructions and explicit expectations matter alongside code.
- [Lucide scan-line](https://lucide.dev/icons/scan-line): capture mark within the existing local icon system and license.

No contribution program or new CONTRIBUTING guide was added. The repository owner's current scope is to finish and validate the software first.
