#!/usr/bin/env python3
"""Run the FCAPSule multi-service incident simulation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from demo.incident_lab import SimulationConfig, run_simulation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="cases/lab_latest", type=Path)
    parser.add_argument("--app-id", default="checkout-platform")
    parser.add_argument("--app-name", default="Checkout Platform")
    parser.add_argument("--baseline-requests", type=int, default=180)
    parser.add_argument("--incident-requests", type=int, default=240)
    parser.add_argument("--concurrency", type=int, default=24)
    args = parser.parse_args()
    config = SimulationConfig(
        app_id=args.app_id,
        app_name=args.app_name,
        baseline_requests=args.baseline_requests,
        incident_requests=args.incident_requests,
        concurrency=args.concurrency,
    )
    print(json.dumps(run_simulation(args.output, config), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
