#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Building users-service image..."
docker build -t signadot/neon-demo-users:latest -f ./docker/users-service.Dockerfile ./pkg/users-service

echo "Build complete!"
echo "Images built:"
docker images | grep signadot/neon-demo