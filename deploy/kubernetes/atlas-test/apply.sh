#!/usr/bin/env bash
set -euo pipefail

manifest_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
namespace=fcapsule-atlas-test
source_ref=${SOURCE_REF:-${1:-}}

if [[ ! "$source_ref" =~ ^[0-9a-f]{40}$ ]]; then
  printf '%s\n' "Usage: SOURCE_REF=<40-character-commit-sha> $0" >&2
  exit 1
fi

kubectl apply -f "$manifest_dir/namespace.yaml"
kubectl -n "$namespace" get secret fcapsule-atlas-runtime >/dev/null
kubectl -n "$namespace" get secret fcapsule-atlas-postgres >/dev/null
kubectl -n "$namespace" create configmap fcapsule-atlas-test-source \
  --from-literal="FCAPSULE_SOURCE_REF=$source_ref" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k "$manifest_dir"
kubectl -n "$namespace" rollout restart deployment/atlas \
  deployment/fcapsule-dev-a deployment/fcapsule-dev-b
kubectl -n "$namespace" rollout status deployment/atlas-postgres --timeout=180s
kubectl -n "$namespace" rollout status deployment/atlas --timeout=180s
kubectl -n "$namespace" rollout status deployment/fcapsule-dev-a --timeout=600s
kubectl -n "$namespace" rollout status deployment/fcapsule-dev-b --timeout=600s
