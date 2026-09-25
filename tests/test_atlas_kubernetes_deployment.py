import unittest
from pathlib import Path

import yaml


OVERLAY = Path(__file__).resolve().parents[1] / "deploy" / "kubernetes" / "atlas-test"


def load_resources(filename):
    return [resource for resource in yaml.safe_load_all((OVERLAY / filename).read_text()) if resource]


class AtlasKubernetesDeploymentTests(unittest.TestCase):
    def test_test_services_are_cluster_internal_and_fixed_to_worker_1(self):
        resources = []
        for filename in ("postgres.yaml", "atlas.yaml", "fcapsule-dev.yaml"):
            resources.extend(load_resources(filename))
        services = [resource for resource in resources if resource["kind"] == "Service"]
        deployments = [resource for resource in resources if resource["kind"] == "Deployment"]

        self.assertEqual({service["metadata"]["name"] for service in services}, {
            "atlas", "atlas-postgres", "fcapsule-dev-a", "fcapsule-dev-b"
        })
        self.assertTrue(all(service["spec"]["type"] == "ClusterIP" for service in services))
        for deployment in deployments:
            self.assertEqual(deployment["spec"]["replicas"], 1)
            self.assertEqual(
                deployment["spec"]["template"]["spec"]["nodeSelector"],
                {"kubernetes.io/hostname": "worker-1"},
            )
        self.assertEqual(
            {pv["metadata"]["name"] for pv in load_resources("storage.yaml") if pv["kind"] == "PersistentVolume"},
            {"atlas-test-postgres", "fcapsule-atlas-test-state-a", "fcapsule-atlas-test-state-b"},
        )

    def test_fcapsule_instances_have_distinct_state_and_atlas_identity(self):
        deployments = {
            resource["metadata"]["name"]: resource
            for resource in load_resources("fcapsule-dev.yaml")
            if resource["kind"] == "Deployment"
        }
        self.assertEqual(set(deployments), {"fcapsule-dev-a", "fcapsule-dev-b"})
        state_claims = set()
        instance_ids = set()
        atlas_urls = set()
        for deployment in deployments.values():
            pod_spec = deployment["spec"]["template"]["spec"]
            container = next(c for c in pod_spec["containers"] if c["name"] == "fcapsule")
            state_claims.add(next(v["persistentVolumeClaim"]["claimName"] for v in pod_spec["volumes"] if v["name"] == "state"))
            env = {entry["name"]: entry for entry in container["env"]}
            instance_ids.add(env["FCAPSULE_ATLAS_INSTANCE_ID"]["value"])
            token = env["FCAPSULE_ATLAS_TOKEN"]["valueFrom"]["secretKeyRef"]
            self.assertEqual(token, {"name": "fcapsule-atlas-runtime", "key": "ATLAS_API_TOKEN"})
            config = load_resources("config.yaml")[0]["data"]
            atlas_urls.add(config["FCAPSULE_ATLAS_URL"])
            self.assertEqual(config["FCAPSULE_LIVE_ENABLED"], "false")
            self.assertEqual(config["FCAPSULE_ATLAS_READ"], "true")
            self.assertEqual(config["FCAPSULE_ATLAS_PUBLISH"], "true")

            source_volume = next(v for v in pod_spec["volumes"] if v["name"] == "source")
            self.assertIn("emptyDir", source_volume)
            self.assertTrue(any(init["name"] == "install-source" for init in pod_spec["initContainers"]))
            self.assertTrue(pod_spec["containers"][0]["readinessProbe"])

        self.assertEqual(state_claims, {"fcapsule-dev-a-state", "fcapsule-dev-b-state"})
        self.assertEqual(instance_ids, {"atlas-dev-a", "atlas-dev-b"})
        self.assertEqual(atlas_urls, {"http://atlas.fcapsule-atlas-test.svc.cluster.local:8080"})

    def test_database_and_atlas_credentials_are_external_secret_references(self):
        postgres = next(
            resource for resource in load_resources("postgres.yaml")
            if resource["kind"] == "Deployment"
        )
        atlas = next(resource for resource in load_resources("atlas.yaml") if resource["kind"] == "Deployment")
        postgres_env = {entry["name"]: entry for entry in postgres["spec"]["template"]["spec"]["containers"][0]["env"]}
        atlas_env = {entry["name"]: entry for entry in atlas["spec"]["template"]["spec"]["containers"][0]["env"]}
        self.assertEqual(
            postgres_env["POSTGRES_PASSWORD"]["valueFrom"]["secretKeyRef"],
            {"name": "fcapsule-atlas-postgres", "key": "POSTGRES_PASSWORD"},
        )
        for key in ("DATABASE_URL", "ATLAS_API_TOKEN"):
            self.assertIn("valueFrom", atlas_env[key])
            self.assertEqual(atlas_env[key]["valueFrom"]["secretKeyRef"]["name"], "fcapsule-atlas-runtime")
        self.assertEqual(atlas_env["ATLAS_API_TOKEN"]["valueFrom"]["secretKeyRef"]["key"], "ATLAS_API_TOKEN")


if __name__ == "__main__":
    unittest.main()
