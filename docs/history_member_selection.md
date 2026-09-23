## Historical Member Selection

Historical retrieval keeps the existing maximum of three supplied recurrence
candidates and four member captures per candidate. Before loading artifacts, it
prioritizes members whose stored recurrence identity exactly equals the current
incident's identity, then recent other members. The stored identity combines
application, resource and normalized alert identity; report prose, diagnostics
and previous model conclusions never determine ranking. Revision-specific primary
incident identity takes precedence over the episode's display primary.

Selection covers candidate membership metadata, including members older than the
current-context twelve-report window, without loading extra captures. Matching
observations lead the result even when the historical episode's display primary
belongs to another failure phase. Existing source/evidence provenance is retained.

`member_selection` records the policy, current incident, matching and omitted
counts, selected incident IDs, identity matches and capture availability. Every
captured observation identifies whether its provenance includes a matching member.
Other members remain context, not automatically evidence of the same diagnosis.
Saved source checks are explicitly episode-level, not necessarily matching-member
observations.

If no selected matching capture is readable, the candidate is unavailable:
unrelated member evidence, source checks and previous hypotheses do not substitute
for it. The selected missing IDs remain in the audit. Missing identity is likewise
unknown, not inferred from prose. Retrieval cannot establish causal equivalence,
and downstream prompt budgets may still omit observations.

`tests/test_history_member_selection.py` exercises retained artifact loading,
older/multiple matches, resource/application isolation, missing or corrupt matching
captures, revision identity and provenance. Tests use temporary artifacts and
mocked stores; no cluster or provider calls are made.
