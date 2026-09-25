#!/usr/bin/env bash
set -euo pipefail

manifest_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
namespace=fcapsule-atlas
source_ref=${SOURCE_REF:-${1:-}}

if [[ ! "$source_ref" =~ ^[0-9a-f]{40}$ ]]; then
  printf '%s\n' "Usage: SOURCE_REF=<40-character-commit-sha> $0" >&2
  exit 1
fi
if grep -q 'storageClassName: replace-with-storage-class' "$manifest_dir/storage.yaml"; then
  printf '%s\n' "Set storageClassName in $manifest_dir/storage.yaml to an approved durable StorageClass." >&2
  exit 1
fi

kubectl apply -f "$manifest_dir/namespace.yaml"
kubectl -n "$namespace" get secret atlas-runtime >/dev/null
kubectl -n "$namespace" get secret atlas-postgres >/dev/null
kubectl -n "$namespace" create configmap atlas-source \
  --from-literal="FCAPSULE_SOURCE_REF=$source_ref" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k "$manifest_dir"
kubectl -n "$namespace" rollout restart deployment/atlas
kubectl -n "$namespace" rollout status deployment/atlas-postgres --timeout=300s
kubectl -n "$namespace" rollout status deployment/atlas --timeout=600s
