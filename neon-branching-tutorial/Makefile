.PHONY: build deploy clean setup-neon-secret

IMAGE_NAME := signadot/neon-demo-users:latest

build:
	docker build -t $(IMAGE_NAME) -f ./docker/users-service.Dockerfile ./pkg/users-service

deploy:
	kubectl apply -f ./k8s

clean:
	kubectl delete -f ./k8s --ignore-not-found

setup-neon-secret:
	@echo "Creating Neon API credentials secret in signadot namespace..."
	@read -p "Enter your Neon API key: " NEON_API_KEY; \
	kubectl create secret generic neon-api-credentials \
		--namespace=signadot \
		--from-literal=NEON_API_KEY=$$NEON_API_KEY \
		--dry-run=client -o yaml | kubectl apply -f -

setup-db-secret:
	@echo "Creating database credentials secret..."
	@read -p "Enter your Neon connection string: " DATABASE_URL; \
	kubectl create secret generic users-db-credentials \
		--from-literal=DATABASE_URL="$$DATABASE_URL" \
		--dry-run=client -o yaml | kubectl apply -f -

all: build deploy