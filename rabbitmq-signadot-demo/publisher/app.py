import json
import logging
import os
import re
from datetime import datetime

import pika
import redis
from flask import Flask, request, jsonify
from opentelemetry.baggage import get_baggage  # read-only; OTel extracts it for us

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def get_rabbitmq_port() -> int:
    raw = os.environ.get("RABBITMQ_PORT", "5672")
    if raw.startswith("tcp://"):
        m = re.search(r":(\d+)$", raw)
        if m:
            return int(m.group(1))
        logging.warning("Could not parse RABBITMQ_PORT '%s'; falling back to 5672", raw)
        return 5672
    try:
        return int(raw)
    except ValueError:
        logging.warning("Could not parse RABBITMQ_PORT '%s'; falling back to 5672", raw)
        return 5672

RABBITMQ_HOST = os.environ.get("RABBITMQ_HOST", "localhost")
RABBITMQ_PORT = get_rabbitmq_port()
EXCHANGE_NAME = os.environ.get("CUSTOMER_EXCHANGE", "orders_exchange")
AMQP_ROUTING_KEY = os.environ.get("AMQP_ROUTING_KEY", "orders")

REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

redis_client = None
try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    redis_client.ping()
    logging.info("Connected to Redis at %s:%s", REDIS_HOST, REDIS_PORT)
except Exception as e:
    logging.warning("Redis not available: %s. Events will not be logged.", e)

def log_event(event_type: str, data: dict) -> None:
    if not redis_client:
        return
    try:
        event = {"timestamp": datetime.utcnow().isoformat(), "type": event_type, "data": data}
        redis_client.lpush("events", json.dumps(event))
        redis_client.ltrim("events", 0, 999)
    except Exception as e:
        logging.error("Failed to log event to Redis: %s", e)

def get_rabbitmq_channel():
    conn = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT))
    ch = conn.channel()
    ch.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
    return conn, ch

@app.route("/publish", methods=["POST"])
def publish_message():
    """
    OTel Flask instrumentation has already extracted baggage (if any) from the HTTP request.
    OTel pika instrumentation will inject baggage into AMQP automatically.
    """
    body_in = request.get_json(silent=True) or {}

    # Read-only view of the effective routing key for visibility
    rk = get_baggage("sd-routing-key")
    effective_rk = rk or "baseline"

    body = dict(body_in)
    body["routing_key"] = effective_rk

    conn, ch = get_rabbitmq_channel()
    try:
        ch.basic_publish(
            exchange=EXCHANGE_NAME,
            routing_key=AMQP_ROUTING_KEY,
            body=json.dumps(body),
            properties=pika.BasicProperties(
                delivery_mode=2,  # persistent
            ),
        )
        logging.info("Published (routing_key=%s)", effective_rk)
        log_event("message_published", {"routing_key": effective_rk, "message": body})
        return jsonify({"status": "published", "routing_key": effective_rk, "message": body}), 200
    finally:
        conn.close()

@app.route("/events", methods=["GET"])
def get_events():
    if not redis_client:
        return jsonify({"error": "Redis not available"}), 503
    try:
        events = redis_client.lrange("events", 0, 99)
        return jsonify([json.loads(e) for e in events]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/stats", methods=["GET"])
def get_stats():
    if not redis_client:
        return jsonify({"error": "Redis not available"}), 503
    try:
        events = redis_client.lrange("events", 0, -1)
        total = len(events)
        counts = {}
        for e in events:
            data = json.loads(e)
            if data.get("type") == "message_published":
                k = data["data"].get("routing_key", "unknown")
                counts[k] = counts.get(k, 0) + 1
        return jsonify({"total_messages": total, "by_routing_key": counts}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
