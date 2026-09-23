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
    if not isinstance(node, promql.VectorSelector):
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
    if not isinstance(root, promql.BinaryExpr) or str(root.op) not in COMPARISONS:
        raise UnavailableExpression("requires_single_root_scalar_comparison")
    if root.modifier and root.modifier.return_bool:
        # An alert fires on vector presence, including the zero-valued bool results.
        raise UnavailableExpression("bool_comparison_is_not_alert_truth")
    left, right = _unwrap(root.lhs), _unwrap(root.rhs)
    operator = str(root.op)
    if isinstance(right, promql.NumberLiteral) and not isinstance(left, promql.NumberLiteral):
        value, threshold = left, right.val
    elif isinstance(left, promql.NumberLiteral) and not isinstance(right, promql.NumberLiteral):
        value, threshold, operator = right, left.val, REVERSED[operator]
    else:
        raise UnavailableExpression("requires_one_literal_scalar_threshold")
    if not math.isfinite(threshold):
        raise UnavailableExpression("non_finite_threshold")
    if not namespace or (labels.get("namespace") and labels["namespace"] != namespace):
        raise UnavailableExpression("incident_namespace_mismatch")

    static_labels = rule.get("labels", {})
    identities = {key: str(labels[key]) for key in IDENTITIES if labels.get(key) and key not in static_labels}
    # The resolved pod is a safe fallback only for a direct selector, not an aggregate.
    direct = isinstance(value, promql.VectorSelector)
    scope = {"namespace": namespace}
    selectors: list[Any] = []
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
    for selector in selectors:
        exact = {m.name: m.value for m in selector.matchers.matchers if m.op == promql.MatchOp.Equal}
        if "namespace" in exact and exact["namespace"] != namespace:
            raise UnavailableExpression("selector_outside_incident_namespace")
        if any(key in exact and exact[key] != expected for key, expected in identities.items()):
            raise UnavailableExpression("selector_outside_incident_identity")
        if not direct and (exact.get("namespace") != namespace or not any(exact.get(k) == v for k, v in identities.items())):
            raise UnavailableExpression("derived_expression_not_already_incident_scoped")
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
        scope.update(identities)
        if any(any(character in matcher.value for character in '\\"\n\r\t')
               for selector in selectors for matcher in selector.matchers.matchers):
            raise UnavailableExpression("unsupported_escaped_derived_matcher")
        expression = str(value)
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
    }
