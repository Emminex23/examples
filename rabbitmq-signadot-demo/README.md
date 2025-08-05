# Testing RabbitMQ Microservices with Signadot Sandboxes

## Introduction

Testing async microservices is tough. If multiple consumers listen to the same RabbitMQ queue, they might steal messages from each other. But cloning your entire message broker for every feature branch? That doesn’t scale.

Signadot sandboxes offer a smarter solution. Instead of duplicating infra, each sandboxed consumer gets its own private queue. Messages are routed based on custom headers, so only the right version processes them.

In this demo, you'll:

* Build a RabbitMQ publisher and consumer using routing keys
* Deploy them to Kubernetes
* Create Signadot sandboxes to isolate test traffic
* Verify that baseline and sandbox environments stay separate

⏱️ **Time:** 45–60 minutes

---

## Prerequisites

* [Signadot CLI](https://www.signadot.com/docs/getting-started/installation)
* Docker Desktop (with Kubernetes enabled)
* `kubectl` configured for your cluster
* Python 3.8+
* Basic understanding of RabbitMQ (queues, exchanges, routing keys)

---

## How It Works

We use RabbitMQ's *direct exchange* pattern. Publishers tag each message with a routing key. Each consumer binds to a specific queue based on that key.

* **Baseline consumer** listens to `demo-baseline`
* **Sandbox consumer** listens to `demo-<sandbox-name>`
* **Publisher** adds the `x-signadot-routing-key` header

Signadot routes traffic based on that header. Consumers use the [Routes API](https://docs.signadot.com/docs/routing) to decide whether to process or reject a message.

---

## Project Layout

```
rabbitmq-signadot-demo/
├── publisher/        # Flask app for publishing messages
├── consumer/         # Python consumer that filters messages
├── k8s/              # Kubernetes manifests (RabbitMQ, Redis, services)
└── signadot/
    ├── sandboxes/    # Sandbox definitions
    └── routegroups/  # RouteGroup config
```

---

## 1. Clone the Project

```bash
git clone https://github.com/signadot/examples.git
cd examples/rabbitmq-signadot-demo
```

---

## 2. Build and Push Images

```bash
# Publisher
docker build -t your-registry/rabbitmq-publisher ./publisher
docker push your-registry/rabbitmq-publisher

# Consumer
docker build -t your-registry/rabbitmq-consumer ./consumer
docker push your-registry/rabbitmq-consumer
```

---

## 3. Deploy the Baseline Stack

```bash
# Create namespace
kubectl create namespace rabbitmq-demo

# Deploy RabbitMQ and Redis
kubectl apply -f k8s/rabbitmq.yaml
kubectl apply -f k8s/redis.yaml

# Deploy publisher and consumer
kubectl apply -f k8s/publisher.yaml
kubectl apply -f k8s/consumer.yaml
```

Check that everything is running:

```bash
kubectl get pods -n rabbitmq-demo
kubectl logs -l app=consumer -n rabbitmq-demo --tail=20 | grep Processing
```

---

## 4. Test Baseline Message Flow

```bash
# Port forward the publisher service
kubectl port-forward svc/publisher 8080:8080 -n rabbitmq-demo &

# Send a test message
curl -X POST http://localhost:8080/publish \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello baseline!"}'

# View recent events
curl http://localhost:8080/events | jq
```

Baseline consumer should log:

```
Processing message: Hello baseline! | queue=demo-baseline
```

---

## 5. Create Sandboxes

```bash
export CLUSTER_NAME=<your-signadot-cluster>

# Apply sandbox forks
signadot sandbox apply -f signadot/sandboxes/publisher.yaml --set cluster=$CLUSTER_NAME
signadot sandbox apply -f signadot/sandboxes/consumer.yaml  --set cluster=$CLUSTER_NAME

# Confirm they're running
signadot sandbox list
```

---

## 6. Create Route Group

```bash
signadot routegroup apply -f signadot/routegroups/demo.yaml --set cluster=$CLUSTER_NAME
```

Get the route group key:

```bash
signadot routegroup get demo
```

---

## 7. Send Messages to the Sandbox

Use the routing key shown in `signadot sandbox list` or `routegroup get`.

```bash
curl -X POST http://localhost:8080/publish \
  -H "Content-Type: application/json" \
  -H "x-signadot-routing-key: consumer-v2" \
  -d '{"message": "Hello sandbox!"}'
```

Now:

* **Baseline consumer** will ignore this message
* **Sandbox consumer** will process it:

```
Processing message: Hello sandbox! | queue=demo-consumer-v2
```

---

## 8. How Routing Works

1. Publisher tags each message with `x-signadot-routing-key`
2. RabbitMQ copies the message to all bound queues
3. Consumers call the Signadot Routes API to get their routing keys
4. Messages are **processed** if key matches, or **requeued** if not

---

## Conclusion

Using Signadot sandboxes, you can safely test changes to message-driven microservices without affecting your production-like baseline. This pattern works not just for RabbitMQ, but any message bus that supports routing keys or headers.
