"""FCAPSule AI command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fcapsule.evaluation.report import recompute_evaluation
from fcapsule.io.case_loader import load_case
from fcapsule.models.schemas import CaseValidationError
from fcapsule.pipeline import investigate_case
from fcapsule.processing.entity_resolver import resolve_entities


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fcapsule", description="Generate compact cloud incident evidence capsules.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    investigate = subparsers.add_parser("investigate", help="Run the complete P1 investigation pipeline")
    investigate.add_argument("--case", required=True, help="Path to a prepared case directory")
    investigate.add_argument("--out", required=True, help="Output directory")
    evaluate = subparsers.add_parser("evaluate", help="Recompute objective evaluation metrics")
    evaluate.add_argument("--case", required=True)
    evaluate.add_argument("--output", required=True)
    inspect = subparsers.add_parser("inspect", help="Validate and summarize a case")
    inspect.add_argument("--case", required=True)
    return parser


def _print_result(result: dict) -> None:
    evaluation = result["evaluation"]
    print("FCAPSule AI - Investigation complete")
    print(f"Case: {result['case_id']}")
    print(f"Service: {result['service']}")
    print(f"Window: {result['window']['start']} -> {result['window']['end']}")
    print("\nLoaded:")
    print(f"- Alerts: {result['alerts']}")
    print(f"- Log lines: {result['logs']}")
    print(f"- Metric series: {result['metric_series']}")
    print("\nResults:")
    print(f"- Log templates generated: {result['templates']}")
    print(f"- Selected evidence items: {result['selected_evidence']}")
    print(f"- Hypotheses generated: {result['hypotheses']}")
    print(f"- Hypotheses verified: {result['verified_hypotheses']}")
    print("\nEvaluation:")
    print(f"- Log compression ratio: {evaluation['log_compression_ratio']:.1%}")
    print(f"- Token reduction: {evaluation['token_reduction_percentage']:.1%}")
    print(f"- Signal preservation: {evaluation['important_signal_preservation']:.1%}")
    print(f"- Grounded claims: {evaluation['hypothesis_grounding_score']:.1%}")
    print(f"- Retention survivability: {evaluation['retention_survivability_score']:.1%}")
    print(f"\nOutput archive: {result['archive']}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "investigate":
            _print_result(investigate_case(args.case, args.out))
        elif args.command == "evaluate":
            print(json.dumps(recompute_evaluation(args.case, args.output), indent=2))
        elif args.command == "inspect":
            bundle = load_case(args.case)
            summary = {
                "case_id": bundle.case_id,
                "metadata": bundle.metadata,
                "alerts": len(bundle.alerts),
                "logs": len(bundle.logs),
                "metric_series": len(bundle.metrics),
                "entity_resolution": resolve_entities(bundle),
            }
            print(json.dumps(summary, indent=2))
        return 0
    except CaseValidationError as exc:
        print(f"Case validation failed: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"I/O failure: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
