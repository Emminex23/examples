import os, json, time, uuid, datetime, logging
import pika, redis
from flask import Flask, request, jsonify

# Helper to parse Kubernetes service env vars
def get_port(env_var, default):
    value = os.getenv(env_var, str(default))
    if value.startswith('tcp://'):
        # Parse Kubernetes format: tcp://10.x.x.x:5672
        return int(value.split(':')[-1])
    return int(value)

# Basic configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=getattr(logging, LOG_LEVEL))
log = logging.getLogger("publisher")

RABBIT_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBIT_PORT = get_port("RABBITMQ_PORT", 5672)
RABBIT_USER = os.getenv("RABBITMQ_USER", "guest")
RABBIT_PASS = os.getenv("RABBITMQ_PASSWORD", "guest")

EXCHANGE = os.getenv("EXCHANGE_NAME", "demo-direct")   # Direct exchange

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = get_port("REDIS_PORT", 6379)
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# RabbitMQ connection helper
def rmq_connect(retries=5, delay=2):
    cred = pika.PlainCredentials(RABBIT_USER, RABBIT_PASS)
    param = pika.ConnectionParameters(RABBIT_HOST, RABBIT_PORT, credentials=cred)
    for i in range(retries):
        try:
            return pika.BlockingConnection(param)
        except pika.exceptions.AMQPConnectionError as e:
            log.warning("RabbitMQ not ready (%s)... retry %d/%d", e, i+1, retries)
            time.sleep(delay)
    raise RuntimeError("RabbitMQ still unreachable")

def ensure_exchange():
    conn = rmq_connect()
    ch = conn.channel()
    ch.exchange_declare(EXCHANGE, exchange_type="direct", durable=True)
    ch.close()
    conn.close()
    log.info(f"Exchange '{EXCHANGE}' declared successfully")

def log_evt(ev_type, msg, route):
    evt = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "event_type": ev_type,
        "message": msg,
        "routing": route,
        "service": "publisher",
    }
    redis_client.lpush("events", json.dumps(evt))
    redis_client.ltrim("events", 0, 999)

# Flask application
app = Flask(__name__)

@app.route("/health")
def health():
    try:
        conn = rmq_connect()
        conn.close()
        redis_client.ping()
        return jsonify({"status": "ok", "service": "publisher"})
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 503

@app.route("/publish", methods=["POST"])
def publish():
    body = request.get_json() or {}
    text = body.get("message", "Hello RabbitMQ!")
    routing_key = request.headers.get("x-signadot-routing-key", "baseline")

    payload = {
        "id": str(uuid.uuid4()),
        "content": text,
        "time": datetime.datetime.utcnow().isoformat(),
        "route": routing_key,
    }

    conn = rmq_connect()
    ch = conn.channel()
    ch.basic_publish(
        exchange=EXCHANGE,
        routing_key=routing_key,  # Direct routing key
        body=json.dumps(payload),
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2  # Persistent
        )
    )
    ch.close()
    conn.close()
    
    log_evt("published", text, routing_key)
    log.info(f"Published message to '{routing_key}': {text}")
    
    return jsonify({
        "ok": True,
        "id": payload["id"],
        "routing_key": routing_key
    })

@app.route("/events")
def events():
    return jsonify([json.loads(e) for e in redis_client.lrange("events", 0, 49)])

@app.route("/stats")
def stats():
    events = [json.loads(e) for e in redis_client.lrange("events", 0, -1)]
    stats = {
        "total": len(events),
        "by_type": {},
        "by_routing": {}
    }
    for e in events:
        evt_type = e.get("event_type", "unknown")
        routing = e.get("routing", "unknown")
        stats["by_type"][evt_type] = stats["by_type"].get(evt_type, 0) + 1
        stats["by_routing"][routing] = stats["by_routing"].get(routing, 0) + 1
    return jsonify(stats)

if __name__ == "__main__":
    log.info(f"Starting publisher - RabbitMQ: {RABBIT_HOST}:{RABBIT_PORT}, Redis: {REDIS_HOST}:{REDIS_PORT}")
    ensure_exchange()
    app.run(host="0.0.0.0", port=8080)