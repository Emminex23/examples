#!/bin/bash
# verify.sh
# Verifies the Dolt deployment, Resource Plugin, and sandbox are working.
# Run this after deploy.sh and after creating a sandbox.
#
# Usage:
#   ./scripts/verify.sh [sandbox-name]

set -euo pipefail

SANDBOX_NAME="${1:-dolt-sandbox-demo}"

echo "=== Dolt Server Status ==="
kubectl get deployment dolt-db -n hotrod
kubectl get pod -n hotrod -l app=dolt-db
echo ""

echo "=== Dolt Branches ==="
kubectl exec -n hotrod deploy/dolt-db -c dolt -- \
  bash -c "cd /var/lib/dolt/location && dolt sql -q 'SELECT * FROM dolt_branches;'" \
  2>/dev/null || echo "(Could not list branches)"
echo ""

echo "=== Main Branch Data ==="
# Explicitly specify location/main because the Resource Plugin may have
# changed the default branch to the sandbox branch.
kubectl exec -n hotrod deploy/dolt-db -c dolt -- \
  bash -c "cd /var/lib/dolt/location && dolt sql -q \
  \"USE \\\`location/main\\\`; SELECT * FROM locations;\"" \
  2>/dev/null || echo "(Could not query locations)"
echo ""

echo "=== Signadot Resource Plugin ==="
signadot resourceplugin get dolt-branch 2>/dev/null || echo "(Plugin not found)"
echo ""

echo "=== Sandbox Status ==="
signadot sandbox get "${SANDBOX_NAME}" 2>/dev/null || echo "(Sandbox '${SANDBOX_NAME}' not found)"
echo ""

echo "=== Sandbox Preview Endpoints ==="
PREVIEW_URL=$(signadot sandbox get "${SANDBOX_NAME}" -o json 2>/dev/null \
  | grep -o '"url":"[^"]*"' | head -1 | cut -d'"' -f4)
if [ -n "${PREVIEW_URL:-}" ]; then
  echo "Preview URL: ${PREVIEW_URL}"
  echo ""
  echo "To test locally using signadot local proxy:"
  echo "  signadot local proxy --sandbox ${SANDBOX_NAME} \\"
  echo "    --map http://location.hotrod.svc:8081@localhost:8081"
  echo "  curl http://localhost:8081/location?locationID=123"
else
  echo "(No preview URL found)"
fi
echo ""

echo "=== Verification complete ==="