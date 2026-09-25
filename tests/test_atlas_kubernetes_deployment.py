import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


OVERLAY = Path(__file__).resolve().parents[1] / "deploy" / "kubernetes" / "atlas-test"
ATLAS_SERVICE = Path(__file__).resolve().parents[1] / "deploy" / "kubernetes" / "atlas"


def load_resources(filename, overlay=OVERLAY):
    return [resource for resource in yaml.safe_load_all((overlay / filename).read_text()) if resource]


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
            install_source = next(init for init in pod_spec["initContainers"] if init["name"] == "install-source")
            self.assertIn(
                "fcapsule-atlas-test-source",
                [entry["configMapRef"]["name"] for entry in install_source["envFrom"]],
            )
            self.assertTrue(pod_spec["containers"][0]["readinessProbe"])

        self.assertEqual(state_claims, {"fcapsule-dev-a-state", "fcapsule-dev-b-state"})
        self.assertEqual(instance_ids, {"atlas-dev-a", "atlas-dev-b"})
        self.assertEqual(atlas_urls, {"http://atlas.fcapsule-atlas-test.svc.cluster.local:8080"})
        atlas_deployment = next(resource for resource in load_resources("atlas.yaml") if resource["kind"] == "Deployment")
        atlas_source = next(
            init for init in atlas_deployment["spec"]["template"]["spec"]["initContainers"]
            if init["name"] == "install-source"
        )
        self.assertIn("#subdirectory=atlas", atlas_source["args"][0])
        self.assertEqual(atlas_source["envFrom"][0]["configMapRef"]["name"], "fcapsule-atlas-test-source")

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

    def test_portable_atlas_profile_uses_durable_pvc_and_no_node_local_assumptions(self):
        resources = []
        for filename in ("postgres.yaml", "atlas.yaml", "storage.yaml"):
            resources.extend(load_resources(filename, ATLAS_SERVICE))
        services = [resource for resource in resources if resource["kind"] == "Service"]
        deployments = [resource for resource in resources if resource["kind"] == "Deployment"]
        claim = next(resource for resource in resources if resource["kind"] == "PersistentVolumeClaim")

        self.assertTrue(all(service["spec"]["type"] == "ClusterIP" for service in services))
        self.assertEqual(claim["spec"]["storageClassName"], "replace-with-storage-class")
        self.assertEqual(claim["spec"]["resources"]["requests"]["storage"], "10Gi")
        for deployment in deployments:
            pod_spec = deployment["spec"]["template"]["spec"]
            self.assertNotIn("nodeSelector", pod_spec)
            self.assertFalse(any("hostPath" in volume for volume in pod_spec["volumes"]))
            self.assertEqual(deployment["spec"]["replicas"], 1)

        atlas = next(resource for resource in deployments if resource["metadata"]["name"] == "atlas")
        source = next(init for init in atlas["spec"]["template"]["spec"]["initContainers"] if init["name"] == "install-source")
        self.assertIn("#subdirectory=atlas", source["args"][0])
        self.assertEqual(source["envFrom"][0]["configMapRef"]["name"], "atlas-source")

        apply_script = (ATLAS_SERVICE / "apply.sh").read_text()
        self.assertIn("SOURCE_REF", apply_script)
        self.assertIn("storageClassName: replace-with-storage-class", (ATLAS_SERVICE / "storage.yaml").read_text())

    def test_test_apply_script_uses_pinned_sha_without_editing_manifests(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            kubectl = fake_bin / "kubectl"
            kubectl.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$KUBECTL_LOG\"\n")
            kubectl.chmod(0o755)
            log_path = root / "kubectl.log"
            source_ref = "a" * 40
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "KUBECTL_LOG": str(log_path),
                "SOURCE_REF": source_ref,
            }
            result = subprocess.run(
                [str(OVERLAY / "apply.sh")],
                cwd=OVERLAY.parents[2],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            commands = log_path.read_text().splitlines()
            self.assertTrue(any("--from-literal=FCAPSULE_SOURCE_REF=" + source_ref in line for line in commands))
            self.assertTrue(any("apply -k " + str(OVERLAY) in line for line in commands))
            restart = next(i for i, command in enumerate(commands) if "rollout restart" in command)
            self.assertTrue(any("rollout status deployment/fcapsule-dev-b" in line for line in commands[restart + 1 :]))

    def test_test_apply_script_rejects_unpinned_source_before_kubectl(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            kubectl = fake_bin / "kubectl"
            kubectl.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$KUBECTL_LOG\"\n")
            kubectl.chmod(0o755)
            log_path = root / "kubectl.log"
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "KUBECTL_LOG": str(log_path),
                "SOURCE_REF": "master",
            }
            result = subprocess.run(
                [str(OVERLAY / "apply.sh")],
                cwd=OVERLAY.parents[2],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("40-character-commit-sha", result.stderr)
            self.assertFalse(log_path.exists())


if __name__ == "__main__":
    unittest.main()
