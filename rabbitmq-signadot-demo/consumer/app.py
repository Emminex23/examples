import json
import logging
import os
import re
import signal
import threading
import time
from typing import Optional, Set

import pika
import requests

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# --------------------------------------------------------------------------- #
# RabbitMQ connection helpers
# --------------------------------------------------------------------------- #
def _parse_rabbitmq_port() -> int:
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
RABBITMQ_PORT = _parse_rabbitmq_port()
CUSTOMER_EXCHANGE = os.environ.get("CUSTOMER_EXCHANGE", "orders_exchange")  # topic exchange

logging.info("Using RabbitMQ at %s:%s", RABBITMQ_HOST, RABBITMQ_PORT)

# --------------------------------------------------------------------------- #
# RouteServer configuration (baseline only)
# --------------------------------------------------------------------------- #
ROUTESERVER_URL = os.environ.get("ROUTESERVER_URL", "http://routeserver.signadot.svc:7778")
BASELINE_KIND = os.environ.get("BASELINE_KIND", "Deployment")

def _detect_namespace() -> str:
    # Prefer explicit envs (e.g., injected via Downward API)
    if ns := os.environ.get("BASELINE_NAMESPACE"):
        return ns
    if ns := os.environ.get("POD_NAMESPACE"):
        return ns
    # Fallback to the service account namespace file (standard in k8s pods)
    try:
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "rabbitmq-demo"

BASELINE_NAMESPACE = _detect_namespace()
BASELINE_NAME = os.environ.get("BASELINE_NAME", "consumer")

# --------------------------------------------------------------------------- #
# Consumer implementation
# --------------------------------------------------------------------------- #
class SignadotConsumer:
    """
    Base consumer that applies Signadot selective-consumption rules.
    Subclasses should implement handle_message().
    """

    def __init__(self) -> None:
        # Sandbox identity (empty/absent => baseline)
        self.sandbox_routing_key: Optional[str] = os.environ.get("SIGNADOT_SANDBOX_ROUTING_KEY")
        self.sandbox_name: str = os.environ.get("SIGNADOT_SANDBOX_NAME", "") or "Baseline"
        self.is_baseline: bool = not bool(self.sandbox_routing_key)

        # Active sandbox keys (baseline uses this to skip messages addressed to active sandboxes)
        self._active_routes: Set[str] = set()
        self._routes_lock = threading.Lock()
        self._stop_event = threading.Event()

        # RabbitMQ connection
        params = pika.ConnectionParameters(host=RABBITMQ_HOST, port=RABBITMQ_PORT)
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()

        # Topic exchange allows us to bind queues to patterns while doing header-based filtering.
        self.channel.exchange_declare(exchange=CUSTOMER_EXCHANGE, exchange_type="topic", durable=True)

        logging.info("[%s] Initialized (is_baseline=%s)", self.sandbox_name, self.is_baseline)

    # ----------------------------- Route updater ---------------------------- #
    def _start_route_updater(self) -> None:
        """
        Baseline polls the RouteServer REST API for active routing rules (sandbox keys).
        Sandbox instances do not need this and return immediately.
        """
        if not self.is_baseline:
            return

        def update_routes() -> None:
            while not self._stop_event.is_set():
                try:
                    url = (
                        f"{ROUTESERVER_URL}/api/v1/workloads/routing-rules"
                        f"?baselineKind={BASELINE_KIND}"
                        f"&baselineNamespace={BASELINE_NAMESPACE}"
                        f"&baselineName={BASELINE_NAME}"
                    )
                    resp = requests.get(url, timeout=5)
                    resp.raise_for_status()
                    data = resp.json()
                    rules = data.get("routingRules", [])
                    active = {r.get("routingKey") for r in rules if r.get("routingKey")}

                    with self._routes_lock:
                        self._active_routes = active

                    logging.info("[%s] Updated active routes: %s", self.sandbox_name, self._active_routes)
                except requests.HTTPError as e:
                    logging.warning("[%s] RouteServer HTTP error: %s", self.sandbox_name, e)
                except Exception as e:
                    logging.error("[%s] Error updating routes: %s", self.sandbox_name, e)
                time.sleep(10)

        t = threading.Thread(target=update_routes, daemon=True)
        t.start()
        logging.info("[%s] Started RouteServer polling thread", self.sandbox_name)

    # ----------------------------- Header parsing --------------------------- #
    @staticmethod
    def _extract_routing_key(headers: Optional[dict]) -> Optional[str]:
        """
        Prefer 'signadot-routing-key'; fall back to W3C Baggage item 'sd-routing-key'.
        """
        if not headers:
            return None

        # AMQP headers can be bytes or str; normalize.
        def _normalize(v):
            return v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else v

        # 1) Preferred explicit header
        rk = headers.get("signadot-routing-key")
        rk = _normalize(rk)
        if rk:
            return rk

        # 2) W3C baggage (comma-separated key=value pairs)
        baggage = _normalize(headers.get("baggage", "")) or ""
        if baggage:
            for item in baggage.split(","):
                item = item.strip()
                if "=" in item:
                    k, v = item.split("=", 1)
                    if k.strip() == "sd-routing-key":
                        return v.strip()

        return None

    # -------------------------- Selective Consumption ----------------------- #
    def _should_process(self, properties: pika.BasicProperties) -> bool:
        headers = getattr(properties, "headers", None) or {}
        msg_key = self._extract_routing_key(headers)

        if not self.is_baseline:
            should = (msg_key == self.sandbox_routing_key)
            logging.info(
                "[%s] Message routing_key=%s, my_key=%s, processing=%s",
                self.sandbox_name, msg_key, self.sandbox_routing_key, should,
            )
            return should

        # Baseline:
        if not msg_key:
            logging.info("[%s] ACCEPTED message (no routing key)", self.sandbox_name)
            return True

        with self._routes_lock:
            is_active = msg_key in self._active_routes

        if not is_active:
            logging.info("[%s] ACCEPTED message for INACTIVE sandbox '%s'", self.sandbox_name, msg_key)
            return True

        logging.info("[%s] SKIPPED message for ACTIVE sandbox '%s'", self.sandbox_name, msg_key)
        return False

    # ------------------------------- Handlers ------------------------------- #
    def _on_message(self, ch, method, properties, body) -> None:
        try:
            if self._should_process(properties):
                try:
                    self.handle_message(body, getattr(properties, "headers", {}) or {})
                except Exception as e:
                    logging.error("[%s] Error in handle_message: %s", self.sandbox_name, e)
        finally:
            # Always ack to avoid requeue loops for skipped messages
            ch.basic_ack(delivery_tag=method.delivery_tag)

    # --------------------------- Lifecycle control -------------------------- #
    def start(self) -> None:
        """
        Declare queue/bindings and start consuming.
        Baseline: durable queue, no auto-delete
        Sandbox : ephemeral queue (auto-delete), non-durable
        """
        queue_name = os.environ.get("QUEUE_NAME", f"consumer-{self.sandbox_name}")
        durable = (os.environ.get("QUEUE_DURABLE", "true" if self.is_baseline else "false").lower() == "true")
        exclusive = (os.environ.get("QUEUE_EXCLUSIVE", "false").lower() == "true")
        auto_delete = (os.environ.get("QUEUE_AUTO_DELETE", "false" if self.is_baseline else "true").lower() == "true")

        res = self.channel.queue_declare(
            queue=queue_name, durable=durable, exclusive=exclusive, auto_delete=auto_delete
        )
        self.queue_name = res.method.queue
        logging.info(
            "[%s] Declared queue '%s' (durable=%s, exclusive=%s, auto_delete=%s)",
            self.sandbox_name, self.queue_name, durable, exclusive, auto_delete,
        )

        # Bind to all routing keys; isolation is done via headers & consumer logic
        self.channel.queue_bind(exchange=CUSTOMER_EXCHANGE, queue=self.queue_name, routing_key="#")

        # Baseline polls RouteServer for active routes
        self._start_route_updater()

        # Start consuming
        self.channel.basic_consume(queue=self.queue_name, on_message_callback=self._on_message)
        logging.info("[%s] Waiting for messages. Press CTRL+C to exit.", self.sandbox_name)

        # Handle SIGTERM gracefully (k8s)
        def _graceful_stop(signum, frame):
            logging.info("[%s] Received signal %s; stopping...", self.sandbox_name, signum)
            self.stop()

        signal.signal(signal.SIGTERM, _graceful_stop)
        signal.signal(signal.SIGINT, _graceful_stop)

        try:
            self.channel.start_consuming()
        except Exception as e:
            logging.error("[%s] Consuming stopped due to error: %s", self.sandbox_name, e)
            self.stop()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            if self.channel.is_open:
                self.channel.stop_consuming()
        finally:
            if self.connection.is_open:
                self.connection.close()
        logging.info("[%s] Consumer stopped gracefully", self.sandbox_name)

    # ------------------------ Business logic (override) --------------------- #
    def handle_message(self, body: bytes, headers: dict) -> None:
        """
        Override in subclass.
        """
        raise NotImplementedError("Application developers must implement 'handle_message'.")


class OrderProcessorConsumer(SignadotConsumer):
    """
    Example business logic consumer.
    """
    def handle_message(self, body: bytes, headers: dict) -> None:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            logging.error("[%s] Invalid JSON payload: %r", self.sandbox_name, body)
            return

        order_id = payload.get("order_id", "unknown")
        amount = payload.get("amount", 0)

        logging.info("[%s] --> PROCESSING ORDER %s amount=%s", self.sandbox_name, order_id, amount)
        # Simulate work
        time.sleep(1.0)
        logging.info("[%s] --> COMPLETED ORDER %s", self.sandbox_name, order_id)


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    consumer = OrderProcessorConsumer()
    consumer.start()
