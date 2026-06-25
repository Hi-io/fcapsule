"""Compare LLM outputs on the same FCAPSule evidence input."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from fcapsule.io.output_writer import write_json
from fcapsule.reasoning.llm_client import ChatRequest, DeepSeekChatClient, LLMUnavailableError

DEFAULT_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")


def _compact_evidence(capsule: dict[str, Any]) -> dict[str, Any]:
    return {
        "case": capsule["case"],
        "domain_summary": capsule.get("domain_summary", {}),
        "timeline": capsule.get("timeline", []),
        "selected_evidence": [
            {
                "evidence_id": item["evidence_id"],
                "domain": item.get("domain"),
                "type": item.get("type"),
                "title": item.get("title"),
                "summary": item.get("summary"),
                "score": item.get("score"),
                "why_selected": item.get("why_selected"),
                "time_range": item.get("time_range"),
                "linked_entities": item.get("linked_entities", []),
            }
            for item in capsule.get("selected_evidence", [])
        ],
        "metric_anomalies": capsule.get("metric_anomalies", []),
        "deterministic_hypotheses": capsule.get("hypotheses", []),
        "missing_evidence": capsule.get("missing_evidence", []),
        "next_steps": capsule.get("next_steps", []),
        "warnings": capsule.get("warnings", []),
    }


def build_model_prompt(capsule: dict[str, Any]) -> list[dict[str, str]]:
    evidence = _compact_evidence(capsule)
    schema = {
        "incident_summary": "One concise paragraph grounded only in evidence IDs.",
        "primary_hypothesis": {
            "claim": "Most likely investigation path, not a final root cause.",
            "confidence": 0.0,
            "supporting_evidence_ids": ["ev_alert_001"],
            "domain_reasoning": {
                "fault_events": "How alerts/events contribute.",
                "log_text": "How log templates contribute.",
                "time_series_metrics": "How metrics contribute.",
                "topology_metadata": "How entity alignment contributes.",
            },
            "contradictions_or_limits": ["Missing telemetry or uncertainty."],
        },
        "alternative_hypotheses": [
            {
                "claim": "Alternative plausible path.",
                "supporting_evidence_ids": ["ev_metric_001"],
                "why_less_likely": "Short reason.",
            }
        ],
        "next_checks": ["Concrete check an engineer can run next."],
        "retention_value": "What the capsule preserves if raw telemetry expires.",
    }
    return [
        {
            "role": "system",
            "content": (
                "You are FCAPSule AI's incident investigation model. Analyze only the supplied evidence. "
                "Do not invent telemetry, do not assert final root cause, and cite evidence IDs exactly. "
                "Return valid JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                "Analyze this multidomain telemetry capsule. The domains are operational telemetry families, "
                "not media files: fault_events are alert/event streams, log_text is semi-structured text logs, "
                "time_series_metrics are numeric measurements over time, and topology_metadata aligns services, "
                "pods, namespaces, clusters, and CNCC UUIDs.\n\n"
                "Use the exact JSON schema below and keep every claim grounded in evidence IDs.\n\n"
                f"JSON schema:\n{json.dumps(schema, indent=2)}\n\n"
                f"Capsule evidence:\n{json.dumps(evidence, indent=2, ensure_ascii=False)}"
            ),
        },
    ]


def _extract_json(text: str) -> tuple[dict[str, Any] | None, str | None]:
    stripped = text.strip()
    if not stripped:
        return None, "empty response"
    fenced = re.search(r"```(?:json)?\s*(.*?)```", stripped, flags=re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else stripped
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start >= 0 and end > start:
        candidate = candidate[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, "JSON root is not an object"
    return parsed, None


def _flatten_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False).lower()


def _expected_signal_terms(capsule: dict[str, Any]) -> dict[str, list[str]]:
    case = capsule.get("case", {})
    alert_names = [str(alert.get("alertname", "")) for alert in capsule.get("alerts", [])]
    metric_names = [str(item.get("metric", "")) for item in capsule.get("metric_anomalies", [])]
    selected_titles = [str(item.get("title", "")) for item in capsule.get("selected_evidence", [])]
    return {
        "case_identity": [case.get("service", ""), case.get("namespace", ""), case.get("pod", "")],
        "fault_event": alert_names,
        "metric_signal": ["error rate", "request_error_rate", "checkout_errors_total", *metric_names[:4]],
        "log_signal": ["checkout failed", "dependency", "503", "failed", *selected_titles[:4]],
        "uncertainty": ["not a final root cause", "missing", "trace", "dependency health"],
    }


def _score_response(parsed: dict[str, Any] | None, raw_text: str, capsule: dict[str, Any]) -> dict[str, Any]:
    valid_ids = {item["evidence_id"]: item.get("domain") for item in capsule.get("selected_evidence", [])}
    text = _flatten_text(parsed if parsed is not None else raw_text)
    cited_ids = re.findall(r"ev_[a-z]+_[0-9]{3}|ev_[a-z]+_[0-9]{3}", text)
    cited_ids = list(dict.fromkeys(cited_ids))
    valid_citations = [item for item in cited_ids if item in valid_ids]
    citation_score = len(valid_citations) / len(cited_ids) if cited_ids else 0.0

    domain_ids = ("fault_events", "log_text", "time_series_metrics", "topology_metadata")
    mentioned_domains = [domain for domain in domain_ids if domain in text]
    domain_score = len(mentioned_domains) / len(domain_ids)

    expected = _expected_signal_terms(capsule)
    matched_groups: dict[str, list[str]] = {}
    for group, terms in expected.items():
        cleaned = [term.lower() for term in terms if term]
        matched = [term for term in cleaned if term and term in text]
        matched_groups[group] = matched
    signal_score = sum(1 for terms in matched_groups.values() if terms) / len(matched_groups)

    next_checks = parsed.get("next_checks", []) if isinstance(parsed, dict) else []
    actionability = min(1.0, len(next_checks) / 3) if isinstance(next_checks, list) else 0.0
    final_root_cause_penalty = 0.15 if "root cause is" in text or "definitive root cause" in text else 0.0
    json_score = 1.0 if parsed is not None else 0.0

    total = (
        json_score * 0.15
        + citation_score * 0.2
        + domain_score * 0.2
        + signal_score * 0.25
        + actionability * 0.15
        + (1.0 - final_root_cause_penalty) * 0.05
    )
    return {
        "total_score": round(max(0.0, min(1.0, total)), 4),
        "json_score": round(json_score, 4),
        "citation_score": round(citation_score, 4),
        "domain_score": round(domain_score, 4),
        "expected_signal_score": round(signal_score, 4),
        "actionability_score": round(actionability, 4),
        "final_root_cause_penalty": final_root_cause_penalty,
        "cited_evidence_ids": cited_ids,
        "valid_cited_evidence_ids": valid_citations,
        "mentioned_domains": mentioned_domains,
        "matched_expected_signal_terms": matched_groups,
        "definitions": {
            "total_score": "Weighted score over JSON validity, citation validity, domain coverage, expected signal coverage, actionability, and RCA caution.",
            "domain_score": "Fraction of P1 operational domains explicitly used by the model.",
            "expected_signal_score": "Fraction of expected incident signal groups mentioned by the model.",
        },
    }


def compare_models(
    capsule_path: str | Path,
    output_dir: str | Path,
    models: list[str] | tuple[str, ...] = DEFAULT_MODELS,
    api_key_env: str = "DEEPSEEK_API_KEY",
    max_tokens: int = 2400,
) -> dict[str, Any]:
    capsule = json.loads(Path(capsule_path).read_text(encoding="utf-8"))
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    prompt = build_model_prompt(capsule)
    client = DeepSeekChatClient(api_key_env=api_key_env)
    results: list[dict[str, Any]] = []
    for model in models:
        started = time.perf_counter()
        raw = client.chat(ChatRequest(model=model, messages=prompt, max_tokens=max_tokens))
        parsed, parse_error = _extract_json(raw["content"])
        if parsed is None and raw.get("finish_reason") == "length" and max_tokens < 6000:
            raw = client.chat(ChatRequest(model=model, messages=prompt, max_tokens=6000))
            parsed, parse_error = _extract_json(raw["content"])
        score = _score_response(parsed, raw["content"], capsule)
        results.append(
            {
                "model": model,
                "provider": raw["provider"],
                "status": "parsed" if parsed is not None else "unparsed",
                "parse_error": parse_error,
                "latency_seconds": raw["latency_seconds"],
                "wall_clock_seconds": round(time.perf_counter() - started, 4),
                "finish_reason": raw["finish_reason"],
                "usage": raw["usage"],
                "reasoning_content_present": raw["reasoning_content_present"],
                "reasoning_content_characters": raw["reasoning_content_characters"],
                "response": parsed,
                "raw_content": raw["content"],
                "score": score,
            }
        )
    ranked = sorted(results, key=lambda item: (-item["score"]["total_score"], item["model"]))
    comparison = {
        "schema_version": "p1-llm-comparison-1.0",
        "case_id": capsule["case"]["case_id"],
        "input_capsule": str(Path(capsule_path).resolve()),
        "models": list(models),
        "same_input_for_all_models": True,
        "prompt": prompt,
        "results": results,
        "winner": ranked[0]["model"] if ranked else None,
        "score_delta": round(ranked[0]["score"]["total_score"] - ranked[1]["score"]["total_score"], 4) if len(ranked) > 1 else 0,
        "interpretation": _interpret_comparison(ranked),
    }
    write_json(output / "llm_comparison.json", comparison)
    write_json(output / "llm_prompt.json", {"messages": prompt})
    from fcapsule.ui.dashboard import render_dashboard

    render_dashboard(output)
    return comparison


def _interpret_comparison(ranked: list[dict[str, Any]]) -> str:
    if len(ranked) < 2:
        return "Only one model was evaluated."
    best, second = ranked[0], ranked[1]
    delta = best["score"]["total_score"] - second["score"]["total_score"]
    if delta <= 0:
        return "The rubric did not show a measurable improvement between the compared model outputs."
    return (
        f"{best['model']} scored higher by {delta:.3f}, mainly reflecting stronger grounded use of the "
        "same multidomain evidence input under the P1 rubric."
    )
