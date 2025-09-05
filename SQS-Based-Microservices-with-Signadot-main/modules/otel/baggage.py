from typing import Optional
from opentelemetry import baggage
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.propagators.textmap import Getter, Setter
from modules.pull_router.router_api import ROUTING_KEY, FILTER_ATTRIBUTE_NAME

# Custom Getter for SQS headers
class SQSGetter(Getter):
    def get(self, carrier, key):
        if key in carrier:
            return [carrier[key]["StringValue"]]
        return []
    def keys(self, carrier):
        return carrier.keys()

# Custom Getter for HTTP headers
class HTTPGetter(Getter):
    def get(self, carrier, key):
        return [carrier.get(key)] if carrier.get(key) else []
    def keys(self, carrier):
        return carrier.keys()

sqs_getter = SQSGetter()
http_getter = HTTPGetter()

def extract_routing_key_from_baggage(message_attributes: dict, getter: Optional[Getter] = sqs_getter) -> Optional[str]:

    ctx = W3CBaggagePropagator().extract(
        carrier=message_attributes,
        getter=getter
    )

    return baggage.get_baggage(ROUTING_KEY, ctx)