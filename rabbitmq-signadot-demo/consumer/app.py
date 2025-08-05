import os, json, time, logging, signal, sys, threading, pika, redis
import datetime

# Helper to parse Kubernetes service env vars
def get_port(env_var, default):
    value = os.getenv(env_var, str(default))
    if value.startswith('tcp://'):
        # Parse Kubernetes format: tcp://10.x.x.x:5672
        return int(value.split(':')[-1])
    return int(value)

# Configuration
LOG_LVL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=getattr(logging, LOG_LVL))
log = logging.getLogger("consumer")

SANDBOX = os.getenv("SANDBOX_NAME", "")               # "" = baseline
ROUTING_KEY = SANDBOX if SANDBOX else "baseline"      # One key per consumer
QUEUE_NAME = f"demo-{ROUTING_KEY}"

RABBIT_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq")
RABBIT_PORT = get_port("RABBITMQ_PORT", 5672)
RABBIT_USER = os.getenv("RABBITMQ_USER", "guest")
RABBIT_PASS = os.getenv("RABBITMQ_PASSWORD", "guest")
EXCHANGE = os.getenv("EXCHANGE_NAME", "demo-direct")

REDIS_HOST = os.getenv("REDIS_HOST", "redis")
REDIS_PORT = get_port("REDIS_PORT", 6379)
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

# RabbitMQ connection
def rmq_connect():
    cred = pika.PlainCredentials(RABBIT_USER, RABBIT_PASS)
    param = pika.ConnectionParameters(RABBIT_HOST, RABBIT_PORT, credentials=cred)
    while True:
        try:
            return pika.BlockingConnection(param)
        except pika.exceptions.AMQPConnectionError as e:
            log.warning("RabbitMQ not ready... %s", e)
            time.sleep(2)

def setup_queue(ch):
    ch.exchange_declare(EXCHANGE, "direct", durable=True)
    ch.queue_declare(QUEUE_NAME, durable=True)
    ch.queue_bind(queue=QUEUE_NAME, exchange=EXCHANGE, routing_key=ROUTING_KEY)
    log.info(f"Queue '{QUEUE_NAME}' bound to exchange '{EXCHANGE}' with key '{ROUTING_KEY}'")

def log_evt(ev_type, msg_content, msg_id):
    evt = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "event_type": ev_type,
        "message": msg_content,
        "message_id": msg_id,
        "service": f"consumer-{ROUTING_KEY}",
        "queue": QUEUE_NAME
    }
    redis_client.lpush("events", json.dumps(evt))
    redis_client.ltrim("events", 0, 999)

# Message handler
def handle(ch, method, prop, body):
    payload = json.loads(body)
    msg_id = payload.get("id", "unknown")
    content = payload.get("content", "")
    
    log.info(f"[{ROUTING_KEY}] Processing: {content}")
    
    # Simulate processing
    time.sleep(0.05)
    
    log_evt("processed", content, msg_id)
    ch.basic_ack(method.delivery_tag)

def serve():
    conn = rmq_connect()
    ch = conn.channel()
    setup_queue(ch)
    ch.basic_qos(prefetch_count=20)
    ch.basic_consume(QUEUE_NAME, handle)
    log.info(f"Consumer ready | queue={QUEUE_NAME} | key={ROUTING_KEY}")
    ch.start_consuming()

# Health check server
def health_server():
    from http.server import HTTPServer, BaseHTTPRequestHandler
    
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/health':
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                status = {
                    "status": "up",
                    "service": "consumer",
                    "routing_key": ROUTING_KEY,
                    "queue": QUEUE_NAME
                }
                self.wfile.write(json.dumps(status).encode())
            else:
                self.send_response(404)
                self.end_headers()
        
        def log_message(self, *args):
            pass  # Suppress logs
    
    server = HTTPServer(("0.0.0.0", 8081), HealthHandler)
    log.info("Health server started on port 8081")
    server.serve_forever()

# Main
if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    
    log.info(f"Starting consumer - RabbitMQ: {RABBIT_HOST}:{RABBIT_PORT}, Redis: {REDIS_HOST}:{REDIS_PORT}")
    
    # Start health server
    threading.Thread(target=health_server, daemon=True).start()
    
    # Wait for dependencies
    time.sleep(5)
    
    # Start consuming
    serve()