"""Conservative AST-based plans for graphing a captured alert's condition."""

from __future__ import annotations

import json
import math
from typing import Any

import promql_parser as promql

COMPARISONS = {"==", "!=", ">", ">=", "<", "<="}
REVERSED = {"==": "==", "!=": "!=", ">": "<", ">=": "<=", "<": ">", "<=": ">="}
IDENTITIES = ("service", "pod", "deployment", "statefulset", "daemonset", "workload", "job", "instance")
FUNCTIONS = {
    "rate", "irate", "increase", "delta", "idelta", "avg_over_time", "min_over_time",
    "max_over_time", "sum_over_time", "count_over_time", "quantile_over_time",
    "histogram_quantile", "clamp_min", "clamp_max", "abs", "ceil", "floor",
}


class UnavailableExpression(ValueError):
    """A rule cannot be graphed without changing or over-broadening its meaning."""


def _unwrap(node: Any) -> Any:
    while isinstance(node, promql.ParenExpr):
        node = node.expr
    return node


def _unit(node: Any) -> str | None:
    node = _unwrap(node)
    if not isinstance(node, promql.VectorSelector):
        if isinstance(node, promql.BinaryExpr) and str(node.op) == "/":
            return "ratio"
        return None
    name = node.name or ""
    if name == "up":
        return "state"
    for suffix, unit in (("_seconds", "seconds"), ("_bytes", "bytes"), ("_ratio", "ratio"), ("_ms", "milliseconds")):
        if name.endswith(suffix):
            return unit
    return None


def _selector_expression(name: str, matchers: list[Any]) -> str:
    # The parser's AST formatter does not escape matcher values. Serialize
    # literals as JSON strings (valid PromQL strings), never raw source fragments.
    operators = ((promql.MatchOp.Equal, "="), (promql.MatchOp.NotEqual, "!="), (promql.MatchOp.Re, "=~"), (promql.MatchOp.NotRe, "!~"))
    return name + "{" + ",".join(
        matcher.name + next(symbol for op, symbol in operators if op == matcher.op) + json.dumps(matcher.value)
        for matcher in matchers
    ) + "}"


def _comparison_parts(node: Any) -> tuple[Any, float, str] | None:
    node = _unwrap(node)
    if not isinstance(node, promql.BinaryExpr) or str(node.op) not in COMPARISONS:
        return None
    if node.modifier and node.modifier.return_bool:
        return None
    left, right = _unwrap(node.lhs), _unwrap(node.rhs)
    operator = str(node.op)
    if isinstance(right, promql.NumberLiteral) and not isinstance(left, promql.NumberLiteral):
        value, threshold = left, right.val
    elif isinstance(left, promql.NumberLiteral) and not isinstance(right, promql.NumberLiteral):
        value, threshold, operator = right, left.val, REVERSED[operator]
    else:
        return None
    if not math.isfinite(threshold):
        return None
    return value, threshold, operator


def _conjuncts(node: Any) -> list[Any]:
    node = _unwrap(node)
    if isinstance(node, promql.BinaryExpr) and str(node.op) == "and":
        if node.modifier and node.modifier.return_bool:
            raise UnavailableExpression("bool_comparison_is_not_alert_truth")
        return [*_conjuncts(node.lhs), *_conjuncts(node.rhs)]
    return [node]


def _primary_metric_score(value: Any) -> tuple[int, int]:
    """Prefer a measurable business signal over freshness/count guard clauses."""
    names: list[str] = []

    def visit(node: Any) -> None:
        if isinstance(node, promql.VectorSelector):
            names.append(node.name or "")

    promql.walk(value, pre_visit=visit)
    names = [name.casefold() for name in names]
    names = [name for name in names if name and name != "up" and
             not any(part in name for part in ("sample_count", "samples_total", "timestamp_seconds"))]
    if not names:
        return (-1, -1)
    ranked_units = max((4 if name.endswith(("_seconds", "_bytes", "_ratio")) else
                        3 if any(term in name for term in ("latency", "duration", "memory", "cpu", "connection", "lock", "buffer", "throttl", "error", "failure", "retry", "backlog")) or
                        name.startswith("kube_pod_status_ready") else
                        -1) for name in names)
    return ranked_units, len(set(names))


def plan_alert_expression(rule: dict[str, Any], labels: dict[str, Any], namespace: str, pod: str) -> dict[str, Any]:
    """Only accept rule definitions supplied by the configured Prometheus adapter.

    A direct selector can be intersected with the alert labels. For derived values,
    scope must already be present in every selector: adding it beneath an aggregate
    could change the threshold's meaning. Unknown constructs fail closed.
    """
    query = rule.get("query")
    if rule.get("ambiguous"):
        raise UnavailableExpression("ambiguous_rule_name")
    if not isinstance(query, str) or not query.strip():
        raise UnavailableExpression("rule_definition_unavailable")
    if len(query) > 4096:
        raise UnavailableExpression("expression_limit_exceeded")
    try:
        root = _unwrap(promql.parse(query))
    except ValueError as exc:
        raise UnavailableExpression("invalid_or_unsupported_promql") from exc
    capture_mode = "alert_condition"
    qualifier_count = 0
    parts = _comparison_parts(root)
    if parts is None and isinstance(root, promql.BinaryExpr) and str(root.op) == "and":
        candidates = [(node, _comparison_parts(node)) for node in _conjuncts(root)]
        ranked = [(node, result, _primary_metric_score(result[0]))
                  for node, result in candidates if result is not None and _primary_metric_score(result[0])[0] >= 1]
        if not ranked:
            raise UnavailableExpression("primary_threshold_series_not_identifiable")
        best = max(item[2] for item in ranked)
        selected = [item for item in ranked if item[2] == best]
        if len(selected) != 1:
            raise UnavailableExpression("ambiguous_primary_threshold_series")
        _, parts, _ = selected[0]
        capture_mode = "primary_threshold_series"
        qualifier_count = len(candidates) - 1
    if parts is None:
        raise UnavailableExpression("requires_single_root_scalar_comparison")
    value, threshold, operator = parts
    if not namespace or (labels.get("namespace") and labels["namespace"] != namespace):
        raise UnavailableExpression("incident_namespace_mismatch")

    static_labels = rule.get("labels", {})
    identities = {key: str(labels[key]) for key in IDENTITIES if labels.get(key) and key not in static_labels}
    target_identity = next((key for key in ("pod", "service", "deployment", "statefulset", "daemonset", "workload", "job", "instance")
                            if key in identities), None)
    # The resolved pod is a safe fallback only for a direct selector, not an aggregate.
    direct = isinstance(value, promql.VectorSelector)
    scope = {"namespace": namespace}
    selectors: list[Any] = []
    aggregations: list[Any] = []
    node_count = 0

    def inspect(node: Any) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > 64:
            raise UnavailableExpression("expression_limit_exceeded")
        if isinstance(node, promql.BinaryExpr):
            if str(node.op) not in {"+", "-", "*", "/", "%", "^"}:
                raise UnavailableExpression("compound_or_nested_condition")
        elif isinstance(node, promql.Call):
            if node.func.name not in FUNCTIONS:
                raise UnavailableExpression("unsupported_function")
        elif isinstance(node, promql.AggregateExpr):
            if str(node.op) not in {"sum", "avg", "min", "max", "count"}:
                raise UnavailableExpression("unsupported_aggregation")
            aggregations.append(node)
        elif isinstance(node, promql.MatrixSelector):
            if node.range.total_seconds() > 600:
                raise UnavailableExpression("lookback_limit_exceeded")
        elif isinstance(node, promql.VectorSelector):
            if node.offset is not None or node.at is not None or node.matchers.or_matchers:
                raise UnavailableExpression("unsupported_selector_modifier")
            if not node.name:
                raise UnavailableExpression("explicit_metric_name_required")
            selectors.append(node)
        elif not isinstance(node, (promql.ParenExpr, promql.UnaryExpr, promql.NumberLiteral)):
            raise UnavailableExpression("unsupported_expression_node")

    promql.walk(value, pre_visit=inspect)
    if not selectors or len(selectors) > 8:
        raise UnavailableExpression("selector_limit_exceeded")
    needs_identity_matcher = False
    for selector in selectors:
        exact = {m.name: m.value for m in selector.matchers.matchers if m.op == promql.MatchOp.Equal}
        if "namespace" in exact and exact["namespace"] != namespace:
            raise UnavailableExpression("selector_outside_incident_namespace")
        if any(key in exact and exact[key] != expected for key, expected in identities.items()):
            raise UnavailableExpression("selector_outside_incident_identity")
        if not direct:
            if exact.get("namespace") != namespace:
                raise UnavailableExpression("derived_expression_not_already_incident_scoped")
            if target_identity and exact.get(target_identity) != identities[target_identity]:
                matchers = [matcher for matcher in selector.matchers.matchers if matcher.name == target_identity]
                if matchers:
                    # Do not attempt to prove regex, negated, or OR matcher intersections.
                    if any(matcher.op != promql.MatchOp.Equal for matcher in matchers):
                        raise UnavailableExpression("derived_identity_matcher_not_exact")
                needs_identity_matcher = True
    if not direct and needs_identity_matcher and aggregations:
        if not target_identity:
            raise UnavailableExpression("derived_incident_identity_unavailable")
        if any(
            aggregation.modifier is None
            or str(aggregation.modifier.type).rsplit(".", 1)[-1] != "By"
            or target_identity not in aggregation.modifier.labels
            for aggregation in aggregations
        ):
            raise UnavailableExpression("derived_aggregation_drops_incident_identity")
    underlying_expression = _selector_expression(value.name, value.matchers.matchers) if direct else str(value)
    if direct:
        scope.update(identities or ({"pod": pod} if pod else {}))
        if len(scope) == 1:
            raise UnavailableExpression("incident_identity_unavailable")
        matchers = list(value.matchers.matchers)
        for key, expected in scope.items():
            if not any(m.name == key and m.op == promql.MatchOp.Equal and m.value == expected for m in matchers):
                matchers.append(promql.Matcher(promql.MatchOp.Equal, key, expected))
        expression = _selector_expression(value.name, matchers)
    else:
        if target_identity:
            scope[target_identity] = identities[target_identity]
        if any(any(character in matcher.value for character in '\\"\n\r\t')
               for selector in selectors for matcher in selector.matchers.matchers):
            raise UnavailableExpression("unsupported_escaped_derived_matcher")
        expression = str(value)
        if needs_identity_matcher:
            for selector in selectors:
                exact_identity = any(
                    matcher.name == target_identity and matcher.op == promql.MatchOp.Equal
                    and matcher.value == identities[target_identity]
                    for matcher in selector.matchers.matchers
                )
                if exact_identity:
                    continue
                old_selector = _selector_expression(selector.name, selector.matchers.matchers)
                new_matchers = list(selector.matchers.matchers)
                new_matchers.append(promql.Matcher(promql.MatchOp.Equal, target_identity, identities[target_identity]))
                new_selector = _selector_expression(selector.name, new_matchers)
                if old_selector not in expression:
                    raise UnavailableExpression("derived_selector_serialization_mismatch")
                expression = expression.replace(old_selector, new_selector)
    if len(expression) > 4096:
        raise UnavailableExpression("scoped_expression_limit_exceeded")
    try:
        promql.parse(expression)
    except ValueError as exc:
        raise UnavailableExpression("unsupported_expression_serialization") from exc
    return {
        "expression": expression,
        "underlying_expression": underlying_expression,
        "metric": value.name if direct else str(value),
        "threshold": threshold,
        "operator": operator,
        "unit": _unit(value),
        "scope": scope,
        "capture_mode": capture_mode,
        "rule_qualifier_count": qualifier_count,
    }
