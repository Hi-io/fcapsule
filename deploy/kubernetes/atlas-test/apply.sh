#!/usr/bin/env bash
set -euo pipefail

manifest_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
namespace=fcapsule-atlas-test

kubectl apply -f "$manifest_dir/namespace.yaml"
kubectl -n "$namespace" get secret fcapsule-atlas-runtime >/dev/null
kubectl -n "$namespace" get secret fcapsule-atlas-postgres >/dev/null
kubectl -n "$namespace" apply -f "$manifest_dir/config.yaml"

source_ref=$(kubectl -n "$namespace" get configmap fcapsule-atlas-test-runtime \
  -o jsonpath='{.data.FCAPSULE_SOURCE_REF}' 2>/dev/null || true)
if [[ ! "$source_ref" =~ ^[0-9a-f]{40}$ ]]; then
  printf '%s\n' "Set FCAPSULE_SOURCE_REF in $manifest_dir/config.yaml to a full 40-character commit SHA before applying." >&2
  exit 1
fi

kubectl apply -k "$manifest_dir"
kubectl -n "$namespace" rollout status deployment/atlas-postgres --timeout=180s
kubectl -n "$namespace" rollout status deployment/atlas --timeout=180s
kubectl -n "$namespace" rollout status deployment/fcapsule-dev-a --timeout=600s
kubectl -n "$namespace" rollout status deployment/fcapsule-dev-b --timeout=600s
