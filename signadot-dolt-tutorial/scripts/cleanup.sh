#!/bin/bash
# cleanup.sh
# Removes the sandbox, Resource Plugin, and Dolt infrastructure.
# Run this when you are done with the tutorial.
#
# Order matters:
#   1. Delete sandbox first (releases the Resource Plugin reference)
#   2. Wait for sandbox deletion (the Resource Plugin cannot be deleted
#      while any sandbox references it)
#   3. Delete Resource Plugin
#   4. Delete Dolt Kubernetes resources
#
# Note on sandbox deletion:
#   "signadot sandbox delete" is a blocking command. It waits for the
#   Resource Plugin's delete workflow (runner pod) to complete. If that
#   workflow fails (e.g., can't install mysql-client, can't reach Dolt),
#   the command hangs indefinitely. The script handles this with a
#   background process and a timeout.
#
# Usage:
#   ./scripts/cleanup.sh [sandbox-name]

# Do NOT use "set -e" in cleanup scripts. Resources may already be deleted,
# and we need the script to continue past individual failures.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SANDBOX_NAME="${1:-dolt-sandbox-demo}"
DELETE_TIMEOUT=60

echo "=== Step 1: Delete Sandbox ==="
if signadot sandbox get "${SANDBOX_NAME}" > /dev/null 2>&1; then
  echo "Deleting sandbox '${SANDBOX_NAME}'..."
  echo "(timeout: ${DELETE_TIMEOUT}s — the CLI blocks until the Resource Plugin's delete workflow finishes)"

  # Run delete in background because the command blocks until the
  # Resource Plugin's delete workflow completes. If the runner pod
  # fails (e.g., apt-get hangs, Dolt unreachable), the CLI hangs forever.
  signadot sandbox delete "${SANDBOX_NAME}" &
  DELETE_PID=$!

  # Wait up to DELETE_TIMEOUT seconds for the delete to finish.
  ELAPSED=0
  while kill -0 "${DELETE_PID}" 2>/dev/null; do
    if [ "${ELAPSED}" -ge "${DELETE_TIMEOUT}" ]; then
      echo ""
      echo "WARNING: sandbox delete timed out after ${DELETE_TIMEOUT}s."
      echo "The Resource Plugin's delete workflow runner pod is likely stuck."
      echo ""
      echo "To unblock, try one of:"
      echo "  1. Delete from the Signadot Dashboard: https://app.signadot.com"
      echo "  2. Check the runner pod: kubectl get pods -n hotrod | grep runner"
      echo "  3. Kill the stuck runner pod, then retry this script."
      echo ""
      kill "${DELETE_PID}" 2>/dev/null
      wait "${DELETE_PID}" 2>/dev/null
      break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
  done

  # Check if delete succeeded
  if ! signadot sandbox get "${SANDBOX_NAME}" > /dev/null 2>&1; then
    echo "Sandbox '${SANDBOX_NAME}' deleted."
  fi
else
  echo "Sandbox '${SANDBOX_NAME}' not found or already deleted."
fi
echo ""

echo "=== Step 2: Wait for sandbox resources to be cleaned up ==="
sleep 10
echo ""

echo "=== Step 3: Delete Resource Plugin ==="
# A ResourcePlugin cannot be deleted while any sandbox references it.
# If Step 1 failed, this step will also fail.
signadot resourceplugin delete dolt-branch \
  && echo "Resource Plugin 'dolt-branch' deleted." \
  || echo "Could not delete Resource Plugin 'dolt-branch'. A sandbox may still reference it."
echo ""

echo "=== Step 4: Delete Dolt Kubernetes resources ==="
kubectl delete -f "${ROOT_DIR}/dolt/dolt-deployment.yaml" --ignore-not-found
kubectl delete -f "${ROOT_DIR}/dolt/dolt-service.yaml" --ignore-not-found
kubectl delete -f "${ROOT_DIR}/dolt/dolt-init-configmap.yaml" --ignore-not-found
kubectl delete secret dolt-credentials -n hotrod --ignore-not-found
echo ""

echo "=== Step 5: Delete Dolt PVC (removes all data) ==="
read -p "Delete Dolt PVC (dolt-data)? All database data will be lost. [y/N] " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
  kubectl delete pvc dolt-data -n hotrod --ignore-not-found
  echo "PVC deleted."
else
  echo "PVC retained. Delete manually with: kubectl delete pvc dolt-data -n hotrod"
fi
echo ""

echo "=== Cleanup complete ==="