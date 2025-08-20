import json
import logging
import os
import re
from datetime import datetime

import pika
import redis
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# ---------------------------- Config & helpers ---------------------------- #

def get_rabbitmq_port() -> int:
    """
    Accepts either a plain port (e.g. "5672") or a k8s-style "tcp://IP:PORT".
    """
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
AMQP_ROUTING_KEY = os.environ.get("AMQP_ROUTING_KEY", "orders")  # topic key used by exchange binding(s)

logging.info("Using RabbitMQ at %s:%s", RABBITMQ_HOST, RABBITMQ_PORT)

# Redis (optional)
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
redis_client = None
try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    redis_client.ping()
    logging.info("Connected to Redis at %s:%s", REDIS_HOST, REDIS_PORT)
except Exception as e:
    logging.warning("Redis not available: %s. Events will not be logged.", e)

# When publisher runs inside a Signadot sandbox, this is injected.
SANDBOX_ROUTING_KEY = os.environ.get("SIGNADOT_SANDBOX_ROUTING_KEY", "").strip()

def log_event(event_type: str, data: dict) -> None:
    if not redis_client:
        return
    try:
        event = {"timestamp": datetime.utcnow().isoformat(), "type": event_type, "data": data}
        redis_client.lpush("events", json.dumps(event))
        redis_client.ltrim("events", 0, 999)  # keep last 1000
    except Exception as e:
        logging.error("Failed to log event to Redis: %s", e)

def get_rabbitmq_channel():
    conn = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT))
    ch = conn.channel()
    ch.exchange_declare(exchange=EXCHANGE_NAME, exchange_type="topic", durable=True)
    return conn, ch

def _extract_sd_routing_key(req) -> str:
    """
    Priority:
      1) x-signadot-routing-key (explicit)
      2) baggage: sd-routing-key=<value>
      3) SIGNADOT_SANDBOX_ROUTING_KEY (env, when running inside a sandbox)
      4) 'baseline'
    """
    # 1) explicit header
    rk = req.headers.get("x-signadot-routing-key")
    if rk:
        return rk.strip()

    # 2) baggage header
    baggage = req.headers.get("baggage", "")
    if baggage:
        for item in baggage.split(","):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                if k.strip() == "sd-routing-key":
                    return v.strip()

    # 3) injected env (sandboxed publisher)
    if SANDBOX_ROUTING_KEY:
        return SANDBOX_ROUTING_KEY

    # 4) baseline default
    return "baseline"

def _upsert_baggage(baggage_value: str, key: str, value: str) -> str:
    """
    Upsert key=value in a W3C baggage header string.
    """
    if not baggage_value:
        return f"{key}={value}"

    parts = []
    found = False
    for item in baggage_value.split(","):
        s = item.strip()
        if not s:
            continue
        if "=" in s:
            k, v = s.split("=", 1)
            if k.strip() == key:
                parts.append(f"{key}={value}")
                found = True
            else:
                parts.append(s)
        else:
            parts.append(s)
    if not found:
        parts.append(f"{key}={value}")
    return ",".join(parts)

# -------------------------------- Routes --------------------------------- #

@app.route("/publish", methods=["POST"])
def publish_message():
    # Parse input JSON (allow empty body)
    body = request.get_json(silent=True) or {}
    rk = _extract_sd_routing_key(request)

    # Prepare AMQP headers
    headers = {}
    # Only attach signadot-routing-key for non-baseline traffic
    if rk and rk != "baseline":
        headers["signadot-routing-key"] = rk
        # preserve/merge any incoming baggage, ensuring sd-routing-key is set
        incoming_baggage = request.headers.get("baggage", "")
        headers["baggage"] = _upsert_baggage(incoming_baggage, "sd-routing-key", rk)

    # Include the effective routing key in the payload (for easy debugging)
    body = dict(body)  # make a copy
    body["routing_key"] = rk

    # Publish
    conn, ch = get_rabbitmq_channel()
    try:
        ch.basic_publish(
            exchange=EXCHANGE_NAME,
            routing_key=AMQP_ROUTING_KEY,
            body=json.dumps(body),
            properties=pika.BasicProperties(
                headers=headers,
                delivery_mode=2,  # persistent
            ),
        )
        logging.info("Published message with routing_key=%s", rk)
        log_event("message_published", {"routing_key": rk, "message": body})
        return jsonify({"status": "published", "routing_key": rk, "message": body}), 200
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

# --------------------------------- Main ---------------------------------- #

if __name__ == "__main__":
    # Flask dev server (in k8s you’ll run behind a Service)
    app.run(host="0.0.0.0", port=8080)
