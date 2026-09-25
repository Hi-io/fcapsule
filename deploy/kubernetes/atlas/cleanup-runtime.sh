#!/usr/bin/env bash
set -euo pipefail

namespace=fcapsule-atlas

# Keep the namespace, configuration, Secrets, PVC, and database data.
kubectl -n "$namespace" delete deployment atlas atlas-postgres --ignore-not-found
kubectl -n "$namespace" delete service atlas atlas-postgres --ignore-not-found
