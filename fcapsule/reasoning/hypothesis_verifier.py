"""Mechanical grounding checks for generated hypotheses."""

from __future__ import annotations

from typing import Any


def verify_hypotheses(hypotheses: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid_ids = {item["evidence_id"] for item in evidence}
    verified = []
    for hypothesis in hypotheses:
        cited = hypothesis.get("supporting_evidence", [])
        missing_ids = sorted(set(cited) - valid_ids)
        notes = []
        confidence = float(hypothesis.get("confidence", 0))
        if not cited:
            verdict = "unsupported"
            confidence = 0.0
            notes.append("No supporting evidence IDs were supplied.")
        elif missing_ids:
            verdict = "unsupported"
            confidence *= 0.25
            notes.append(f"Unknown evidence IDs: {', '.join(missing_ids)}")
        else:
            verdict = "plausible"
            notes.append("All cited evidence IDs exist in the selected evidence set.")
        if len(cited) == 1:
            confidence *= 0.8
            notes.append("Confidence reduced because only one evidence item supports the hypothesis.")
        if hypothesis.get("missing_evidence"):
            confidence *= 0.95
            notes.append("Missing telemetry prevents a stronger causal conclusion.")
        verified.append(
            {
                **hypothesis,
                "verdict": verdict,
                "adjusted_confidence": round(max(0.0, min(1.0, confidence)), 3),
                "verification_notes": notes,
            }
        )
    return verified
