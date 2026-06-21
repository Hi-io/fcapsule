# P1 Evaluation Record and Review Form

## Verified Reference Run

Case: `case_001`, generated from the local checkout-service failure simulation.

Observed capture:

- 60 successful checkout requests;
- 40 failed checkout requests returning HTTP 503;
- final request error rate: 40%;
- `CheckoutHighErrorRate` status: firing;
- 141 captured log events;
- 7 captured metric series.

Verified pipeline result:

- 4 log templates;
- 12 selected evidence items;
- 3 generated and verified hypotheses;
- 95.0% representative-line compression;
- 72.2% estimated token reduction;
- 100% operational signal preservation;
- 100% valid hypothesis citations;
- 100% retention-section survivability.

These values are measured from the committed case and may change when the case is regenerated because timestamps and latency values are live measurements.

## Reviewer Form

Score each item from 1 (strongly negative) to 5 (strongly positive).

| Question | Score |
|---|---|
| How clear is the capsule? | 1-5 |
| How useful is it for starting investigation? | 1-5 |
| How trustworthy are the hypotheses? | 1-5 |
| Does it preserve important evidence? | 1-5 |
| Are suggested next steps actionable? | 1-5 |
| Would it save time compared with raw logs? | 1-5 |

Free text:

- What was most useful?
- What was confusing?
- What evidence seemed missing?
- Would you use this during an investigation?
- What should improve in P2?

Record reviewer role, observability experience, reviewed baseline, date, and whether the case was known in advance.
