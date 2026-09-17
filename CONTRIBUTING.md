# Contributing to FCAPSule

FCAPSule is an incident-evidence product, not a telemetry warehouse. Contributions
must preserve that boundary: source telemetry is queried for a bounded incident window;
capsules retain derived, attributable evidence rather than raw logs or trace payloads.

## Development setup

Use Python 3.11 or later. Install the local package and test dependency set, then run:

```bash
python3 -m unittest discover -s tests -v
```

Synthetic inputs that make the test suite deterministic belong in `tests/fixtures/`.
Do not commit captures, archives, `.fcapsule/` state, provider keys, or telemetry exported
from a real environment.

## Change expectations

- Keep operator-facing reports focused on impact, evidence, uncertainty, and next checks.
- Preserve evidence identifiers and source references when changing the investigation pipeline.
- Treat model calls as optional enrichment. Deterministic capture and reporting must remain
  available without credentials or network access.
- Add or update focused tests for behavioral changes.
- Update documentation whenever a normalized-case, storage, or privacy contract changes.

## Pull requests

Describe the operational problem being solved, the affected telemetry domain, validation
performed, and any retention or privacy implication. Avoid attaching raw production
telemetry to issues or pull requests.
