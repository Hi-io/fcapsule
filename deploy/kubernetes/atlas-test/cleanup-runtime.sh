#!/usr/bin/env bash
set -euo pipefail

namespace=fcapsule-atlas-test

# Keep the namespace, configuration, Secrets, PVCs, PVs, and hostPath data.
kubectl -n "$namespace" delete deployment \
  atlas atlas-postgres fcapsule-dev-a fcapsule-dev-b --ignore-not-found
kubectl -n "$namespace" delete service \
  atlas atlas-postgres fcapsule-dev-a fcapsule-dev-b --ignore-not-found
