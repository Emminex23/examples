.PHONY: help setup build deploy plugin-secret sandbox-create sandbox-delete test clean db-setup db-connect

help:
	@echo "Xata + Signadot Database Branching Tutorial"
	@echo ""
	@echo "Setup:"
	@echo "  make setup          - Check prerequisites"
	@echo "  make build          - Build Docker image"
	@echo "  make deploy         - Deploy to Kubernetes"
	@echo "  make db-setup       - Create schema and seed data"
	@echo ""
	@echo "Signadot:"
	@echo "  make plugin-secret  - Create secret and install Resource Plugin"
	@echo "  make sandbox-create - Create test sandbox"
	@echo "  make sandbox-delete - Delete test sandbox"
	@echo ""
	@echo "Utility:"
	@echo "  make test           - Test baseline deployment"
	@echo "  make db-connect     - Connect to database with psql"
	@echo "  make clean          - Remove all resources"

setup:
	@echo "Checking prerequisites..."
	@command -v kubectl >/dev/null 2>&1 || { echo "ERROR: kubectl not found"; exit 1; }
	@command -v xata >/dev/null 2>&1 || { echo "ERROR: xata CLI not found"; exit 1; }
	@command -v signadot >/dev/null 2>&1 || { echo "ERROR: signadot CLI not found"; exit 1; }
	@command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found"; exit 1; }
	@command -v psql >/dev/null 2>&1 || { echo "ERROR: psql not found"; exit 1; }
	@command -v jq >/dev/null 2>&1 || { echo "ERROR: jq not found"; exit 1; }
	@echo "All prerequisites found."
	@echo ""
	@xata auth status >/dev/null 2>&1 || { echo "ERROR: Not authenticated with Xata. Run 'xata auth login'"; exit 1; }
	@echo "Xata: authenticated"
	@signadot cluster list >/dev/null 2>&1 || { echo "ERROR: Not authenticated with Signadot. Run 'signadot auth login'"; exit 1; }
	@echo "Signadot: authenticated"
	@echo ""
	@echo "Setup complete!"

build:
	@echo "Building Docker image..."
	docker build -t users-service:latest -f docker/users-service.Dockerfile .
	@echo "Build complete."

deploy:
	@echo "Deploying to Kubernetes..."
	@test -f .xata/project.json || { echo "ERROR: .xata/project.json not found. Run 'xata init' first."; exit 1; }
	kubectl apply -f k8s/namespace.yaml
	kubectl delete secret db-creds -n demo --ignore-not-found
	kubectl create secret generic db-creds \
		--namespace=demo \
		--from-literal=DATABASE_URL="$$(xata branch url)"
	kubectl apply -f k8s/deployment.yaml
	kubectl apply -f k8s/service.yaml
	@echo "Waiting for deployment..."
	kubectl rollout status deployment/users-service -n demo --timeout=120s
	@echo "Deployment complete."

db-setup:
	@echo "Setting up database..."
	@test -f .xata/project.json || { echo "ERROR: .xata/project.json not found. Run 'xata init' first."; exit 1; }
	psql "$$(xata branch url)" -f db/schema.sql
	psql "$$(xata branch url)" -f db/seed.sql
	@echo "Database setup complete."

db-connect:
	@psql "$$(xata branch url)"

plugin-secret:
	@echo "Creating Xata credentials secret..."
	@test -f .xata/project.json || { echo "ERROR: .xata/project.json not found. Run 'xata init' first."; exit 1; }
	@test -f .xata/branch.json || { echo "ERROR: .xata/branch.json not found. Run 'xata init' first."; exit 1; }
	@if [ -z "$${XATA_API_KEY}" ]; then \
		echo "XATA_API_KEY not set."; \
		echo "Generate one at https://app.xata.io/settings/api-keys"; \
		echo ""; \
		read -p "Enter your Xata API key: " XATA_API_KEY; \
		export XATA_API_KEY; \
	fi && \
	echo "" && \
	echo "Reading configuration..." && \
	XATA_ORG_ID="$$(jq -r '.organizationId' .xata/project.json)" && \
	XATA_PROJECT_ID="$$(jq -r '.projectId' .xata/project.json)" && \
	XATA_DATABASE_NAME="$$(jq -r '.databaseName' .xata/branch.json)" && \
	echo "XATA_ORG_ID=$$XATA_ORG_ID" && \
	echo "XATA_PROJECT_ID=$$XATA_PROJECT_ID" && \
	echo "XATA_DATABASE_NAME=$$XATA_DATABASE_NAME" && \
	echo "XATA_API_KEY=(set)" && \
	echo "" && \
	kubectl create namespace signadot --dry-run=client -o yaml | kubectl apply -f - && \
	kubectl delete secret xata-credentials -n signadot --ignore-not-found && \
	kubectl create secret generic xata-credentials \
		--namespace=signadot \
		--from-literal=XATA_API_KEY="$$XATA_API_KEY" \
		--from-literal=XATA_ORG_ID="$$XATA_ORG_ID" \
		--from-literal=XATA_PROJECT_ID="$$XATA_PROJECT_ID" \
		--from-literal=XATA_DATABASE_NAME="$$XATA_DATABASE_NAME"
	@echo ""
	@echo "Secret created."
	@echo ""
	@echo "Installing Resource Plugin..."
	signadot resourceplugin delete xata-branch 2>/dev/null || true
	signadot resourceplugin apply -f signadot/xata-branch-plugin.yaml
	@echo "Plugin installed."

sandbox-create:
	@echo "Creating test sandbox..."
	@CLUSTER="$$(signadot cluster list -o json | jq -r '.[0].name')" && \
	echo "Cluster: $$CLUSTER" && \
	signadot sandbox apply -f signadot/users-sandbox.yaml \
		--set sandbox-name=xata-test \
		--set cluster="$$CLUSTER"

sandbox-delete:
	@echo "Deleting test sandbox..."
	signadot sandbox delete xata-test

test:
	@echo "Testing baseline deployment..."
	@kubectl port-forward svc/users-service 3000:3000 -n demo &
	@sleep 3
	@echo ""
	@echo "=== Health ==="
	@curl -s http://localhost:3000/health | jq .
	@echo ""
	@echo "=== Users ==="
	@curl -s http://localhost:3000/users | jq .
	@echo ""
	@echo "=== Info ==="
	@curl -s http://localhost:3000/info | jq .
	@echo ""
	@pkill -f "port-forward.*3000" || true

clean:
	@echo "Cleaning up..."
	-signadot sandbox delete xata-test 2>/dev/null
	-signadot resourceplugin delete xata-branch 2>/dev/null
	-kubectl delete secret xata-credentials -n signadot --ignore-not-found
	-kubectl delete -f k8s/ --ignore-not-found
	-kubectl delete secret db-creds -n demo --ignore-not-found
	@echo "Cleanup complete."