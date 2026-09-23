import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from fcapsule.pipeline import investigate_case
from fcapsule.incident_report import build_incident_report
from fcapsule.models.schemas import CaseBundle
from fcapsule.processing.metrics_analyzer import analyze_metrics
from tests.common import REFERENCE_CASE


class PipelineRegressionTests(unittest.TestCase):
    def test_metric_baseline_basis_distinguishes_alert_and_fallback_windows(self):
        series = {
            "metric": "pod_cpu_cores",
            "labels": {"pod": "checkout"},
            "values": [["2026-09-20T00:00:00Z", 0.1], ["2026-09-20T00:01:00Z", 0.9]],
        }
        metadata = {"case_id": "baseline-test", "window": {"start": "2026-09-20T00:00:00Z", "end": "2026-09-20T00:02:00Z"}}
        bundle = lambda alert, values: CaseBundle(Path("."), metadata, [{"startsAt": alert}], [{**series, "values": values}], [], [], "")
        self.assertEqual(analyze_metrics(bundle("2026-09-20T00:00:30Z", series["values"]))[0]["baseline_basis"], "pre_alert")
        self.assertEqual(analyze_metrics(bundle("2026-09-20T00:02:00Z", series["values"]))[0]["baseline_basis"], "split_window")
        self.assertEqual(analyze_metrics(bundle("2026-09-20T00:02:00Z", series["values"][:1]))[0]["baseline_basis"], "single_sample")

    def test_reference_case_generates_complete_grounded_capsule(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            result = investigate_case(REFERENCE_CASE, output)
            expected = {"capsule.md", "capsule.json", "evidence.json", "evaluation.json", "baselines.json", "fcapsule_case_001.zip"}
            self.assertTrue(expected.issubset({path.name for path in output.iterdir()}))
            self.assertEqual(result["verified_hypotheses"], result["hypotheses"])
            capsule_json = json.loads((output / "capsule.json").read_text(encoding="utf-8"))
            self.assertTrue({"fault_events", "log_text", "time_series_metrics", "topology_metadata"}.issubset(capsule_json["domain_summary"]))
            for metric in capsule_json["metric_anomalies"]:
                self.assertIn(metric["baseline_basis"], {"pre_alert", "split_window", "single_sample"})
                self.assertLessEqual(metric["baseline_start"], metric["baseline_end"])
            evaluation = json.loads((output / "evaluation.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(evaluation["log_compression_ratio"], 0.9)
            self.assertGreaterEqual(evaluation["important_signal_preservation"], 0.9)
            self.assertEqual(evaluation["hypothesis_grounding_score"], 1.0)
            capsule = (output / "capsule.md").read_text(encoding="utf-8")
            self.assertIn("## 4. Operational Signal Domains", capsule)
            self.assertIn("## 12. Retention Note", capsule)
            self.assertIn("does not assert a final root cause", capsule)

    def test_archive_excludes_raw_telemetry(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            investigate_case(REFERENCE_CASE, output)
            with ZipFile(output / "fcapsule_case_001.zip") as archive:
                names = set(archive.namelist())
            self.assertNotIn("opensearch_logs.json", names)
            self.assertEqual(names, {"capsule.md", "capsule.json", "evidence.json", "evaluation.json", "baselines.json"})

    def test_incident_report_surfaces_generic_kubernetes_metrics(self):
        anomaly = {
            "metric_id": "metric_001",
            "metric": "pod_memory_working_set_bytes",
            "baseline_median": 1048576,
            "baseline_start": "2026-09-20T00:00:00Z",
            "baseline_end": "2026-09-20T00:00:00Z",
            "baseline_basis": "pre_alert",
            "incident_peak": 4194304,
            "peak_timestamp": "2026-09-20T00:01:00Z",
            "percentage_change": 300,
            "anomaly_score": 0.8,
            "labels": {"pod": "checkout-abc"},
        }
        capsule = {
            "case": {"case_id": "k8s-case", "service": "checkout", "cluster": "go15", "namespace": "default"},
            "selected_evidence": [
                {"evidence_id": "ev_metric_001", "source_id": "metric_001", "type": "metric_anomaly"}
            ],
            "metric_anomalies": [anomaly],
            "hypotheses": [],
            "alerts": [],
            "domain_summary": {},
            "evaluation": {},
        }
        source_metrics = [
            {
                "metric": "pod_memory_working_set_bytes",
                "labels": {"pod": "checkout-abc"},
                "values": [["2026-09-20T00:00:00Z", 1048576], ["2026-09-20T00:01:00Z", 4194304]],
            }
        ]

        report = build_incident_report(capsule, {"incident_id": "k8s-case"}, source_metrics)

        self.assertEqual(report["impact"][0]["label"], "Pod memory working set")
        self.assertEqual(report["impact"][0]["value"], "4.0 MiB")
        self.assertEqual(report["pm_signals"][0]["metric"], "pod_memory_working_set_bytes")
        self.assertEqual(report["pm_signals"][0]["baseline_start"], "2026-09-20T00:00:00Z")
        self.assertEqual(report["pm_signals"][0]["baseline_basis"], "pre_alert")

    def test_incident_report_displays_restart_counter_as_window_increase(self):
        anomaly = {
            "metric_id": "metric_001",
            "metric": "pod_container_restarts_total",
            "baseline_median": 1,
            "incident_peak": 0,
            "peak_timestamp": "2026-09-20T00:01:00Z",
            "percentage_change": -100,
            "anomaly_score": 1,
            "labels": {"pod": "checkout-abc"},
        }
        capsule = {
            "case": {"case_id": "restart-case", "service": "checkout"},
            "selected_evidence": [
                {"evidence_id": "ev_metric_001", "source_id": "metric_001", "type": "metric_anomaly"}
            ],
            "metric_anomalies": [anomaly],
            "hypotheses": [],
            "alerts": [],
            "domain_summary": {},
            "evaluation": {},
        }
        source_metrics = [
            {
                "metric": "pod_container_restarts_total",
                "labels": {"pod": "checkout-abc"},
                "values": [["2026-09-20T00:00:00Z", 2], ["2026-09-20T00:01:00Z", 3]],
            }
        ]

        report = build_incident_report(capsule, {"incident_id": "restart-case"}, source_metrics)

        self.assertEqual(report["impact"][0]["value"], "1")
        self.assertEqual(report["impact"][0]["component"], "checkout-abc")
        self.assertEqual(report["pm_signals"][0]["peak_value"], 1)


if __name__ == "__main__":
    unittest.main()
