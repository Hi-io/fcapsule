import unittest
from pathlib import Path

import yaml


class HttpsDeploymentTests(unittest.TestCase):
    def test_optional_proxy_preserves_application_and_limits_privileges(self):
        root = Path(__file__).resolve().parents[1] / "deploy" / "kubernetes"
        patch = yaml.safe_load((root / "https-proxy-patch.yaml").read_text())
        spec = patch["spec"]["template"]["spec"]
        self.assertEqual([c["name"] for c in spec["containers"]], ["https-proxy"])
        self.assertNotIn("initContainers", spec)
        self.assertNotIn("nodeSelector", spec)
        proxy = spec["containers"][0]
        self.assertTrue(proxy["securityContext"]["readOnlyRootFilesystem"])
        self.assertTrue(proxy["securityContext"]["runAsNonRoot"])
        self.assertFalse(proxy["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(proxy["securityContext"]["capabilities"]["add"], ["NET_BIND_SERVICE"])
        self.assertEqual(proxy["resources"]["limits"]["memory"], "96Mi")
        self.assertEqual(proxy["readinessProbe"]["httpGet"]["scheme"], "HTTPS")
        self.assertEqual(spec["volumes"][1]["secret"]["defaultMode"], 0o440)

    def test_https_uses_server_secret_and_loopback_upstream(self):
        root = Path(__file__).resolve().parents[1] / "deploy" / "kubernetes"
        config, service = list(yaml.safe_load_all((root / "https-proxy.yaml").read_text()))
        caddyfile = config["data"]["Caddyfile"]
        self.assertIn("tls /etc/fcapsule-tls/tls.crt /etc/fcapsule-tls/tls.key", caddyfile)
        self.assertIn("reverse_proxy 127.0.0.1:8765", caddyfile)
        self.assertNotIn("tls internal", caddyfile)
        self.assertEqual(service["spec"]["ports"][0]["nodePort"], 30767)


if __name__ == "__main__":
    unittest.main()
