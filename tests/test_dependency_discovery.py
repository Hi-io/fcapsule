import unittest
from unittest.mock import Mock

from fcapsule.adapters.kubernetes_adapter import KubernetesAdapter, evaluate_label_selector


class DependencyDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.adapter = KubernetesAdapter("http://kubernetes")
        self.adapter.transport = Mock()
        self.pod = {"name": "orders-1", "namespace": "shop", "raw_spec": {"containers": []}}

    def test_only_local_declared_endpoints_are_exposed(self):
        values = {"INVENTORY_URL": "http://inventory:8080/reserve", "DB_HOST": "mysql.shop.svc.cluster.local",
                  "EXTERNAL_URL": "https://external.example.com", "OTHER_HOST": "other.foreign.svc.cluster.local",
                  "IP_HOST": "192.168.0.1", "SECRET_URL": "http://private", "COMMENT": "http://unrelated",
                  "OVERRIDE_HOST": "should-not-read", "BROKEN_URL": "http://[broken"}
        self.adapter.transport.request.return_value = {"data": values}
        self.pod["raw_spec"]["containers"] = [{"envFrom": [{"configMapRef": {"name": "runtime"}}], "env": [
            {"name": "OVERRIDE_HOST", "valueFrom": {"secretKeyRef": {"name": "secret", "key": "host"}}},
            {"name": "WORKER_URL", "value": "http://worker.shop:8080"}]}]
        result = self.adapter.declared_services(self.pod)
        self.assertEqual({item["service"] for item in result}, {"inventory", "mysql", "worker"})
        self.adapter.transport.request.assert_called_once_with("/api/v1/namespaces/shop/configmaps/runtime")
        inventory = next(item for item in result if item["service"] == "inventory")
        self.assertEqual(inventory["configured_endpoint"], {
            "host": "inventory", "scheme": "http", "port": 8080, "port_source": "explicit",
        })
        self.assertEqual(inventory["configured_via"], "ConfigMap/runtime:INVENTORY_URL")
        self.assertTrue(inventory["observed_at"].endswith("Z"))
        self.assertNotIn("/reserve", str(result))
        self.assertNotIn("external.example.com", str(result))

    def test_configmap_key_reference_does_not_expose_other_endpoints(self):
        self.adapter.transport.request.return_value = {"data": {"selected": "mysql", "UNUSED_HOST": "other"}}
        self.pod["raw_spec"]["containers"] = [{"env": [{"name": "DB_HOST", "valueFrom": {
            "configMapKeyRef": {"name": "runtime", "key": "selected"}}}]}]
        self.assertEqual([item["service"] for item in self.adapter.declared_services(self.pod)], ["mysql"])

    def test_default_endpoint_ports_are_explicitly_marked_as_inferred(self):
        self.adapter.transport.request.return_value = {"data": {
            "TLS_ENDPOINT": "https://inventory.shop.svc.cluster.local/reserve",
            "UNKNOWN_HOST": "inventory",
        }}
        self.pod["raw_spec"]["containers"] = [{"envFrom": [{"configMapRef": {"name": "runtime"}}]}]

        result = self.adapter.declared_services(self.pod)

        self.assertEqual(result[0]["configured_endpoint"], {
            "host": "inventory.shop.svc.cluster.local", "scheme": "https", "port": 443,
            "port_source": "https_default",
        })
        self.assertEqual(result[1]["configured_endpoint"]["port"], None)
        self.assertEqual(result[1]["configured_endpoint"]["port_source"], "not_declared")

    def test_host_endpoint_pairs_only_its_declared_port_variable(self):
        self.adapter.transport.request.return_value = {"data": {
            "INVENTORY_HOST": "inventory", "INVENTORY_PORT": "8081",
            "UNRELATED_PORT": "9443", "AUTH_TOKEN": "must-not-appear",
        }}
        self.pod["raw_spec"]["containers"] = [{"envFrom": [{"configMapRef": {"name": "runtime"}}]}]

        result = self.adapter.declared_services(self.pod)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["configured_endpoint"], {
            "host": "inventory", "scheme": None, "port": 8081,
            "port_source": "paired_environment",
        })
        self.assertEqual(result[0]["port_configured_via"], "ConfigMap/runtime:INVENTORY_PORT")
        self.assertNotIn("9443", str(result))
        self.assertNotIn("must-not-appear", str(result))

    def test_paired_port_must_be_bounded_ascii_decimal(self):
        for port in ("0", "65536", "１２３４", "1" * 10000):
            with self.subTest(port=port[:20]):
                self.adapter.transport.request.return_value = {"data": {
                    "INVENTORY_HOST": "inventory", "INVENTORY_PORT": port,
                }}
                self.pod["raw_spec"]["containers"] = [{"envFrom": [{"configMapRef": {"name": "runtime"}}]}]
                endpoint = self.adapter.declared_services(self.pod)[0]["configured_endpoint"]
                self.assertIsNone(endpoint["port"])
                self.assertEqual(endpoint["port_source"], "not_declared")

    def test_credentials_and_query_values_are_not_retained(self):
        self.adapter.transport.request.return_value = {"data": {
            "INVENTORY_URL": "http://user:private@inventory:8080/reserve?api_token=never-retain",
        }}
        self.assertEqual(self.adapter.declared_services(self.pod), [])

    def test_service_selector_cannot_cross_namespaces_or_select_unrelated_pods(self):
        self.adapter.transport.request.return_value = {"metadata": {"resourceVersion": "41", "labels": {"tier": "backend"}},
            "spec": {"selector": {"app": "inventory"}, "ports": [
                {"name": "http", "port": 8080, "targetPort": 8081},
                {"name": "metrics", "port": 9090, "targetPort": "metrics"},
            ]}}
        pods = [{"namespace": "shop", "name": "good", "labels": {"app": "inventory"}},
                {"namespace": "other", "name": "foreign", "labels": {"app": "inventory"}},
                {"namespace": "shop", "name": "unrelated", "labels": {"app": "orders"}}]
        resolution = self.adapter.resolve_service("shop", "inventory", pods)
        self.assertEqual([item["name"] for item in resolution["pods"]], ["good"])
        self.assertEqual(resolution["service"]["ports"], [
            {"name": "http", "port": 8080, "target_port": 8081, "protocol": "TCP"},
            {"name": "metrics", "port": 9090, "target_port": "metrics", "protocol": "TCP"},
        ])
        self.assertEqual(resolution["service"]["selector"], {"app": "inventory"})
        self.assertEqual(resolution["service"]["resource_version"], "41")
        self.assertTrue(resolution["service"]["observed_at"].endswith("Z"))
        for spec in ({"type": "ExternalName", "selector": {"app": "inventory"}}, {}):
            self.adapter.transport.request.return_value = {"spec": spec}
            with self.assertRaises(ValueError):
                self.adapter.resolve_service("shop", "inventory", pods)
        self.adapter.transport.reset_mock()
        with self.assertRaises(ValueError):
            self.adapter.service_pods("shop", "../foreign", pods)
        self.adapter.transport.request.assert_not_called()

    def test_label_selector_uses_and_semantics_and_unknown_for_unsupported_rules(self):
        labels = {"app": "api", "tier": "frontend"}
        result = evaluate_label_selector(labels, {"app": "api"}, [
            {"key": "tier", "operator": "In", "values": ["frontend", "worker"]},
            {"key": "optional", "operator": "NotIn", "values": ["disabled"]},
            {"key": "app", "operator": "Exists", "values": []},
            {"key": "retired", "operator": "DoesNotExist", "values": []},
        ])
        self.assertEqual(result["status"], "matched")
        mismatch = evaluate_label_selector(labels, {}, [
            {"key": "tier", "operator": "NotIn", "values": ["frontend"]},
        ])
        self.assertEqual(mismatch["status"], "not_matched")
        self.assertEqual(mismatch["requirements"][0]["observed"], "frontend")
        unsupported = evaluate_label_selector(labels, {}, [
            {"key": "tier", "operator": "GreaterThan", "values": ["1"]},
        ])
        self.assertEqual(unsupported["status"], "unknown")
        sensitive = evaluate_label_selector({"api-token": "secret"}, {"api-token": "secret"}, [])
        self.assertEqual(sensitive["status"], "unknown")
        self.assertNotIn("secret", str(sensitive))
