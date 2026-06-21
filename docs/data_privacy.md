# Data Privacy and Safe Samples

## P1 Policy

- Do not commit production telemetry.
- Do not include secrets, access tokens, customer identifiers, or real account data.
- Use synthetic or explicitly approved anonymized cases.
- Review files before sending telemetry to any external model.
- Prefer local deterministic processing for sensitive experiments.

## Masking

P1 masks IP addresses, emails, UUID-shaped values, long hexadecimal IDs, token/secret/password assignments, and variable numeric tokens in templates. Representative lines are anonymized before entering derived evidence.

Masking is defense in depth, not a guarantee. Source case owners remain responsible for data approval.

## Reference Case

`cases/case_001` is generated entirely by `demo/unstable_service.py`. Its IP address, UUID-like request IDs, pod, cluster, and CNCC values are fictional. No company system or customer data is queried.

## Archives

The capsule ZIP excludes raw logs and metrics. It contains only derived capsule, evidence, baseline, and evaluation files. Derived evidence can still be sensitive in a real deployment and needs an explicit retention and access policy in P6.
