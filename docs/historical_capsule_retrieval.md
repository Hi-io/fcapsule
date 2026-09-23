# Historical Capsule Retrieval

## Retrieval Contract

Recurrence is deterministic candidate selection, not a causal conclusion. The
store selects at most three earlier episodes sharing the application, resource
identity and alert identity. It does not search arbitrary similar descriptions,
cross application boundaries or infer a match across replacement pod identities.

`historical_episode` accepts only these supplied candidate IDs. It executes before
live adapter construction and reads retained report/capsule artifacts. Deleting
raw case files or losing Prometheus, OpenSearch and Kubernetes availability does
not remove the stored observations. FCAPSule's own retention/deletion policy can
still remove those artifacts; missing or unreadable captures remain unavailable.

The bounded historical result contains selected observations from the last four
available member reports, including alert-rule measurements, log examples and
configuration facts. It also includes up to four completed source checks from the
prior saved investigation, without recursively following historical checks.
The combined observation list is capped at 80. Episode, incident, evidence and
prior-check provenance stays attached. Source-check collection time is not assumed
to be incident time. Prior model hypotheses are explicitly non-citable context.

## Retained-Only Review

A retained-only review independently resolves these candidates, even when the
current investigation never ran a historical tool. It has no live adapter or tool
executor. It still needs one configured model-provider request; retained-only does
not mean offline model inference.

Queueing freezes the input context and checks. The deduplication fingerprint
includes historical evidence, so changed or unavailable retained history cannot
silently reuse an earlier answer. Previous assessments, including hypotheses nested
inside legacy checks, are excluded. Empty evidence can produce an uncited
`unresolved` answer, but not an uncited sufficiency claim.

Each new review saves and exposes:

- `retained_context`: complete supplied context, including E/A source provenance.
- `retained_checks`: complete supplied Q records, including historical snapshots.
- `model_context`: the actual bounded context supplied to the model.
- `available_evidence_ids`: only the citations visible in that bounded context.

The incident payload and `investigation_history.json` /
`investigation_revisions.json` exports carry this ledger. Review citations must
resolve within that review's saved context/checks, never a later investigation's
reused Q IDs. Legacy reviews without a saved ledger cannot reconstruct it safely.
Portable archives retain observations and review provenance, not raw telemetry.

## Verification And Limits

`tests/test_historical_capsules.py` builds separate normalized captures through
the real capsule pipeline using a mocked metrics transport, deletes their raw
directories, and makes live-source access fail. It checks retained measured values,
configuration, provenance through compaction, candidate scope/bounds, unavailable
artifacts, saved source checks, review input snapshots, API ledgers and ZIP exports.
All model responses in these tests are fake; no provider or cluster is contacted.

Prompt budgets still omit observations. The retained-review projection removes
duplicate historical metadata before compaction; this is not exhaustive retrieval.
Policy `episode-investigation-1.21` compaction retains structured check facts instead
of a truncated JSON prefix of source metadata. Discovery facts pair the recorded
monitor selectors with captured target labels and states; ServiceMonitor and
PodMonitor labels remain distinct. It ranks captured targets by scope and available
diagnostic fields, not by whether labels agree or by an expected diagnosis. This
projection never queries sources or expands the adapter's label allowlist.

When required checks cannot all fit, prior-episode comparisons yield before current
observations. Omitted checks lose their visible citation IDs, and an extremely small
budget can still omit any check. Each investigation call saves `model_context`
beside `visible_evidence_ids` so the actual bounded evidence can be audited. A
comparison must cite a visible historical check for the selected candidate;
otherwise it is downgraded to insufficient evidence without discarding the current
assessment. An absent comparison or an unknown candidate is instead omitted with a
`historical_comparison_review` grounding-guard trace; no substitute episode is
invented. All other assessment citations must still validate. These guards do not
request a model repair call. `tests/test_evidence_budget_guard.py` covers the full
2,100-token request and review paths, matching and differing labels, absent monitor
definitions, scrape errors, privacy, and invalid current citations.

Recurrence and citation validation still do not mechanically verify
natural-language causal claims. Tests passing do not establish model accuracy.

## Live Probe

After the tested HTTPS revision is ready, repeat a known alert for the same
application/resource/alert identity. Confirm its historical check identifies the
earlier capsule and a distinctive earlier measurement and time window, rather than
the current value or prior conclusion. Use a disposable capture or disconnect
source access for the retention test; do not delete shared production telemetry.

Ask a retained-only comparison question. Inspect its `model_context` and citation
ledger, confirm no telemetry queries occurred, and verify that similarities remain
tentative and missing observations remain unresolved. Download the archive and
verify the review ledger and retained report remain available. A separate check
with a missing capsule should report unavailable evidence, not health or an
automatically repeated cause.
