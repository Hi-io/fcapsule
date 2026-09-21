"""Conservative, evidence-led correlation across independent incident episodes.

An episode already combines alerts for one application within a short period.  This
module answers a deliberately narrower question: are two *different* applications
likely reacting to one shared operating condition?  It never groups by similar
free-form incident text alone.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any


CORRELATION_WINDOW_SECONDS = 15 * 60


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalise(value: Any) -> str:
    return " ".join(_text(value).lower().split())


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _value_from_link(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "service", "destination", "to", "value"):
            if value.get(key):
                return _text(value[key])
        return ""
    return _text(value)


def _configuration_nodes(entry: dict[str, Any]) -> set[str]:
    nodes: set[str] = set()
    report = entry.get("report") if isinstance(entry.get("report"), dict) else {}
    capsule = entry.get("capsule") if isinstance(entry.get("capsule"), dict) else {}
    configurations = list(report.get("configuration_evidence") or [])
    for evidence in capsule.get("selected_evidence", []):
        if isinstance(evidence, dict) and evidence.get("type") == "configuration":
            configurations.append(evidence.get("configuration") or {})
    for configuration in configurations:
        if not isinstance(configuration, dict):
            continue
        spec = configuration.get("spec") if isinstance(configuration.get("spec"), dict) else {}
        node = _text(
            configuration.get("node")
            or configuration.get("node_name")
            or configuration.get("nodeName")
            or spec.get("nodeName")
        )
        if node:
            nodes.add(_normalise(node))
    return nodes


def _alert_families(entry: dict[str, Any]) -> set[str]:
    report = entry.get("report") if isinstance(entry.get("report"), dict) else {}
    families = set()
    for alert in report.get("fault_alerts", []):
        if not isinstance(alert, dict):
            continue
        name = _normalise(alert.get("name") or alert.get("alertname") or alert.get("title"))
        if name:
            families.add(name)
    return families


def _dependencies(entry: dict[str, Any]) -> set[str]:
    report = entry.get("report") if isinstance(entry.get("report"), dict) else {}
    dependencies: set[str] = set()
    for link in report.get("topology", []):
        if not isinstance(link, dict):
            continue
        destination = _value_from_link(
            link.get("to") or link.get("destination") or link.get("service") or link.get("depends_on")
        )
        if destination:
            dependencies.add(_normalise(destination))
    return dependencies


def episode_profile(
    episode: dict[str, Any], application: dict[str, Any] | None, entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Project retained reports into the small, auditable grouping vocabulary."""

    cluster = _text((application or {}).get("cluster"))
    if not cluster or not entries:
        return None
    alerts: set[str] = set()
    nodes: set[str] = set()
    dependencies: set[str] = set()
    for entry in entries:
        alerts.update(_alert_families(entry))
        nodes.update(_configuration_nodes(entry))
        dependencies.update(_dependencies(entry))
    # Alert family and an independent topology/node link are both required.
    if not alerts or not (nodes or dependencies):
        return None
    try:
        started = _timestamp(_text(episode.get("started_at")))
        last_activity = _timestamp(_text(episode.get("last_activity_at") or episode.get("started_at")))
    except (TypeError, ValueError):
        return None
    return {
        "episode_id": _text(episode.get("episode_id")),
        "reference": _text(episode.get("reference")),
        "app_id": _text(episode.get("app_id")),
        "cluster": cluster,
        "status": _text(episode.get("status") or "resolved"),
        "severity": _text(episode.get("severity") or "info"),
        "started_at": started,
        "last_activity_at": last_activity,
        "alerts": alerts,
        "nodes": nodes,
        "dependencies": dependencies,
    }


def _time_clusters(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Use a bounded span rather than transitive time chaining."""

    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    anchor: datetime | None = None
    for item in sorted(items, key=lambda candidate: candidate["started_at"]):
        if anchor is None or (item["started_at"] - anchor).total_seconds() <= CORRELATION_WINDOW_SECONDS:
            current.append(item)
            anchor = anchor or item["started_at"]
        else:
            result.append(current)
            current, anchor = [item], item["started_at"]
    if current:
        result.append(current)
    return result


def _one_per_application(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Same-application alert folding is handled by the episode layer already."""

    selected: dict[str, dict[str, Any]] = {}
    for item in sorted(items, key=lambda candidate: candidate["started_at"]):
        selected.setdefault(item["app_id"], item)
    return list(selected.values())


def _severity(items: list[dict[str, Any]]) -> str:
    rank = {"critical": 3, "warning": 2, "info": 1}
    return max(items, key=lambda item: rank.get(_normalise(item["severity"]), 0))["severity"]


def _candidate(
    cluster: str,
    alert: str,
    link_kind: str,
    link_value: str,
    items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    items = _one_per_application(items)
    if len(items) < 2:
        return None
    started = min(item["started_at"] for item in items)
    # Round rather than floor so a short incident window around a half-hour boundary
    # remains one candidate, while incidents hours apart remain distinct.
    window_bucket = round(started.timestamp() / (2 * CORRELATION_WINDOW_SECONDS))
    identity = {
        "cluster": cluster,
        "alert": alert,
        "link_kind": link_kind,
        "link_value": link_value,
        "window_bucket": window_bucket,
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()[:18]
    relationship = "common condition suspected; verify shared evidence before treating it as a root cause"
    basis = [
        {"kind": "same_alert_family", "value": alert},
        {"kind": "same_node" if link_kind == "node" else "shared_dependency", "value": link_value},
        {"kind": "time_window", "value": "within 15 minutes"},
    ]
    return {
        "group_id": f"related-{digest}",
        "correlation_key": digest,
        "cluster": cluster,
        "title": (
            f"Potential shared node condition on {link_value}"
            if link_kind == "node"
            else f"Potential shared dependency condition involving {link_value}"
        ),
        "status": "active" if any(_normalise(item["status"]) == "active" for item in items) else "resolved",
        "severity": _severity(items),
        "relationship": relationship,
        "basis": basis,
        "episode_ids": [item["episode_id"] for item in sorted(items, key=lambda item: item["started_at"])],
    }


def derive_related_episode_groups(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return deterministic candidates backed by alert + independent shared context.

    A free-form title, summary, or an LLM assessment is intentionally never read
    here.  The result is a review cue, not an asserted causal relation.
    """

    buckets: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for profile in profiles:
        for alert in profile["alerts"]:
            for node in profile["nodes"]:
                buckets[(profile["cluster"], alert, "node", node)].append(profile)
            for dependency in profile["dependencies"]:
                buckets[(profile["cluster"], alert, "dependency", dependency)].append(profile)

    candidates: list[dict[str, Any]] = []
    for (cluster, alert, link_kind, link_value), members in buckets.items():
        for temporal_group in _time_clusters(members):
            candidate = _candidate(cluster, alert, link_kind, link_value, temporal_group)
            if candidate:
                candidates.append(candidate)

    # A node relation is usually the sharper operational cue.  Keep one compact
    # row when the exact same episode set has corroborating links.
    merged: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for candidate in sorted(candidates, key=lambda item: (item["basis"][1]["kind"] != "same_node", item["correlation_key"])):
        key = (candidate["cluster"], tuple(candidate["episode_ids"]))
        existing = merged.get(key)
        if not existing:
            merged[key] = candidate
            continue
        for basis in candidate["basis"]:
            if basis not in existing["basis"]:
                existing["basis"].append(basis)
    return sorted(merged.values(), key=lambda item: (item["status"] != "active", item["severity"] != "critical", item["correlation_key"]))


class RelatedEpisodeService:
    """Refresh related-episode cues only when retained episode inputs change."""

    def __init__(self, plane: Any) -> None:
        self.plane = plane
        self._fingerprint = ""

    def refresh(self) -> list[dict[str, Any]]:
        episodes = self.plane.store.list_episodes(limit=200)
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    [item["episode_id"], item["app_id"], item["updated_at"], item["report_count"], item["status"]]
                    for item in episodes
                ],
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if fingerprint == self._fingerprint:
            return self.plane.store.list_related_episode_groups()

        applications = {item["app_id"]: item for item in self.plane.store.list_applications()}
        profiles = []
        for episode in episodes:
            profile = episode_profile(
                episode,
                applications.get(episode["app_id"]),
                self.plane.investigator.entries(episode),
            )
            if profile:
                profiles.append(profile)
        for candidate in derive_related_episode_groups(profiles):
            self.plane.store.upsert_related_episode_group(candidate)
        self._fingerprint = fingerprint
        return self.plane.store.list_related_episode_groups()
