import unittest
from unittest.mock import Mock

from fcapsule.adapters.kubernetes_adapter import KubernetesAdapter


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
        self.assertNotIn("http", str(result))

    def test_configmap_key_reference_does_not_expose_other_endpoints(self):
        self.adapter.transport.request.return_value = {"data": {"selected": "mysql", "UNUSED_HOST": "other"}}
        self.pod["raw_spec"]["containers"] = [{"env": [{"name": "DB_HOST", "valueFrom": {
            "configMapKeyRef": {"name": "runtime", "key": "selected"}}}]}]
        self.assertEqual([item["service"] for item in self.adapter.declared_services(self.pod)], ["mysql"])

    def test_service_selector_cannot_cross_namespaces_or_select_unrelated_pods(self):
        self.adapter.transport.request.return_value = {"spec": {"selector": {"app": "inventory"}}}
        pods = [{"namespace": "shop", "name": "good", "labels": {"app": "inventory"}},
                {"namespace": "other", "name": "foreign", "labels": {"app": "inventory"}},
                {"namespace": "shop", "name": "unrelated", "labels": {"app": "orders"}}]
        self.assertEqual([item["name"] for item in self.adapter.service_pods("shop", "inventory", pods)], ["good"])
        for spec in ({"type": "ExternalName", "selector": {"app": "inventory"}}, {}):
            self.adapter.transport.request.return_value = {"spec": spec}
            with self.assertRaises(ValueError):
                self.adapter.service_pods("shop", "inventory", pods)
        self.adapter.transport.reset_mock()
        with self.assertRaises(ValueError):
            self.adapter.service_pods("shop", "../foreign", pods)
        self.adapter.transport.request.assert_not_called()
