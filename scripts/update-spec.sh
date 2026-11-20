#!/usr/bin/env bash
# Refresh the vendored SignPath OpenAPI document and artifact-configuration XSD.
#
# Usage: scripts/update-spec.sh [--check]
#   --check   exit 1 if the upstream files differ from the vendored copies (for scheduled CI)
set -euo pipefail

cd "$(dirname "$0")/.."
RES=src/signpath_manager/resources
OPENAPI_URL="https://app.signpath.io/Api/swagger/v1/swagger.json"
XSD_URL="https://app.signpath.io/Web/artifact-configuration/v1.xsd"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

curl -fsSL -o "$tmp/signpath-openapi.json" "$OPENAPI_URL"
curl -fsSL -o "$tmp/artifact-configuration-v1.xsd" "$XSD_URL"

status=0
for f in signpath-openapi.json artifact-configuration-v1.xsd; do
  if ! cmp -s "$tmp/$f" "$RES/$f"; then
    echo "changed: $f"
    status=1
    if [[ "${1:-}" != "--check" ]]; then
      cp "$tmp/$f" "$RES/$f"
    fi
  fi
done

if [[ "${1:-}" == "--check" ]]; then
  exit $status
fi
echo "done"
