#!/bin/bash
# deploy.sh
# Deploys Dolt database server and Signadot Resource Plugin.
# Run this BEFORE creating any sandboxes.
#
# Prerequisites:
#   - kubectl configured with access to your cluster
#   - hotrod namespace exists with the HotROD application deployed
#   - Signadot CLI installed (https://docs.signadot.com/docs/getting-started/installation/signadot-cli)
#
# Usage:
#   ./scripts/deploy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Step 1: Authenticate Signadot CLI ==="
if signadot auth status 2>/dev/null | grep -q "Authenticated"; then
  echo "Signadot CLI is already authenticated."
else
  echo "The Signadot CLI is not authenticated."
  echo "You need a Signadot API key. Generate one at:"
  echo "  https://app.signadot.com/settings/apikeys"
  echo ""
  read -s -p "Paste your Signadot API key: " SIGNADOT_API_KEY
  echo ""
  if [ -z "${SIGNADOT_API_KEY}" ]; then
    echo "Error: API key cannot be empty."
    exit 1
  fi
  signadot auth login --with-api-key "${SIGNADOT_API_KEY}"
  echo "Signadot CLI authenticated."
fi
echo ""

echo "=== Step 2: Create Dolt credentials Secret ==="
read -s -p "Enter a password for the Dolt root user: " DOLT_PASSWORD
echo ""
if [ -z "${DOLT_PASSWORD}" ]; then
  echo "Error: password cannot be empty."
  exit 1
fi
kubectl create secret generic dolt-credentials \
  -n hotrod \
  --from-literal=username=root \
  --from-literal=password="${DOLT_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "Secret 'dolt-credentials' created in namespace 'hotrod'."
echo ""

echo "=== Step 3: Create init-data ConfigMap ==="
kubectl apply -f "${ROOT_DIR}/dolt/dolt-init-configmap.yaml"
echo ""

echo "=== Step 4: Deploy Dolt SQL server ==="
kubectl apply -f "${ROOT_DIR}/dolt/dolt-deployment.yaml"
echo ""

echo "=== Step 5: Create Dolt ClusterIP Service ==="
kubectl apply -f "${ROOT_DIR}/dolt/dolt-service.yaml"
echo ""

echo "=== Step 6: Wait for Dolt pod to be ready ==="
kubectl rollout status deployment/dolt-db -n hotrod --timeout=120s
echo ""

echo "=== Step 7: Verify Dolt is accepting connections ==="
DOLT_POD=$(kubectl get pod -n hotrod -l app=dolt-db -o jsonpath='{.items[0].metadata.name}')
echo "Dolt pod: ${DOLT_POD}"
kubectl exec -n hotrod "${DOLT_POD}" -c dolt -- \
  bash -c "cd /var/lib/dolt/location && dolt sql -q 'SELECT * FROM locations;'"
echo ""

echo "=== Step 8: Apply Signadot Resource Plugin ==="
signadot resourceplugin apply -f "${ROOT_DIR}/signadot/dolt-branch-plugin.yaml"
echo ""

echo "=== Deployment complete ==="
echo "You can now create sandboxes with:"
echo "  signadot sandbox apply -f signadot/sandbox.yaml --set cluster=<YOUR_CLUSTER>"