import os
import asyncio
import time # For monotonic time
import aiohttp
from typing import List, Any, Optional, Set, Dict, Type
from urllib.parse import urlencode, urlunparse, urlparse, ParseResult
from opentelemetry import baggage
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from modules.logger.logger import get_logger

logger = get_logger(__name__)

# Default config values mirroring the python example (can be overridden by env vars)
DEFAULT_ROUTE_SERVER_ADDR = "http://localhost:8080" # Default address for the routing rules API
DEFAULT_BASELINE_KIND = "Deployment"
DEFAULT_BASELINE_NAMESPACE = "default"
DEFAULT_BASELINE_NAME = "consumer" # Placeholder, configure as needed
DEFAULT_REFRESH_INTERVAL = 5 # seconds
ROUTING_KEY = "sd-routing-key"
FILTER_ATTRIBUTE_NAME = "baggage"
ENV_SANDBOX_KEY = "SIGNADOT_SANDBOX_NAME"

class RoutesAPIClient:

    def __init__(self, sandbox_name: str):
        self.sandbox_name = sandbox_name # Current sandbox name, empty for baseline

        # Configuration from environment variables or defaults
        self.route_server_addr_base = os.getenv("ROUTES_API_ROUTE_SERVER_ADDR", DEFAULT_ROUTE_SERVER_ADDR)
        parsed_addr = urlparse(self.route_server_addr_base)
        self.route_server_scheme = parsed_addr.scheme or "http"
        self.route_server_netloc = parsed_addr.netloc
        
        self.baseline_kind = os.getenv("ROUTES_API_BASELINE_KIND", DEFAULT_BASELINE_KIND)
        self.baseline_namespace = os.getenv("BASELINE_NAMESPACE", DEFAULT_BASELINE_NAMESPACE)
        self.baseline_name = os.getenv("BASELINE_NAME", DEFAULT_BASELINE_NAME)
        
        self.refresh_interval = int(os.getenv("ROUTES_API_REFRESH_INTERVAL_SECONDS", str(DEFAULT_REFRESH_INTERVAL)))

        self._routing_keys_cache: Set[str] = set()
        self._cache_update_lock = asyncio.Lock() # Lock for initiating an update
        self._cache_updated_event = asyncio.Event() # Event to signal update completion
        self._last_successful_update_time: float = 0.0 # Using time.monotonic()
        self._is_first_update_done = False # Tracks if the first fetch attempt has completed        

    def _build_routes_url(self) -> str:
        
        query_params = {
            'baselineKind': self.baseline_kind,
            'baselineNamespace': self.baseline_namespace,
            'baselineName': self.baseline_name
        }
        if self.sandbox_name:
            query_params['destinationSandboxName'] = self.sandbox_name
        
        path = '/api/v1/workloads/routing-rules'
        
        url_parts = ParseResult(
            scheme=self.route_server_scheme,
            netloc=self.route_server_netloc,
            path=path,
            params='',
            query=urlencode(query_params),
            fragment=''
        )
        return urlunparse(url_parts)

    async def _perform_fetch_and_update(self) -> None:
        
        url = self._build_routes_url()
        logger.info(f"RoutesAPIClient: Fetching routes from {url}")
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        new_routing_keys = set()                        
                        if isinstance(data, dict) and 'routingRules' in data and isinstance(data['routingRules'], list):
                            for rule in data['routingRules']:
                                if isinstance(rule, dict) and 'routingKey' in rule and rule['routingKey'] is not None:
                                    new_routing_keys.add(str(rule['routingKey']))
                        
                        self._routing_keys_cache = new_routing_keys
                        self._last_successful_update_time = time.monotonic()
                        self._is_first_update_done = True
                        logger.info(f"RoutesAPIClient: Routing keys updated: {list(self._routing_keys_cache)}")
                    else:
                        logger.error(f"RoutesAPIClient: Error fetching routes. Status: {response.status}, Body: {await response.text()}")
        except aiohttp.ClientError as e:
            logger.error(f"RoutesAPIClient: HTTP client error fetching routes: {e}")
        except Exception as e:
            logger.error(f"RoutesAPIClient: Error during route fetch/parse: {e}")

    async def _ensure_cache_fresh(self) -> None:

        current_time = time.monotonic()
        needs_update = not self._is_first_update_done or \
                       (current_time - self._last_successful_update_time > self.refresh_interval)
        if needs_update:
            if self._cache_update_lock.locked():
                logger.info("RoutesAPIClient: Update in progress by another task, waiting...")
                await self._cache_updated_event.wait()
                logger.info("RoutesAPIClient: Update completed by another task or wait timed out.")
            else:
                async with self._cache_update_lock:
                    current_time_after_lock = time.monotonic() # Re-check time after acquiring lock
                    if not self._is_first_update_done or \
                       (current_time_after_lock - self._last_successful_update_time > self.refresh_interval):
                        
                        self._cache_updated_event.clear()
                        try:
                            await self._perform_fetch_and_update()
                        finally:
                            self._cache_updated_event.set()
                            logger.info("RoutesAPIClient: Cache update cycle finished, event set.")
                    else:
                        # Cache was updated by another task while this one was waiting for the lock
                        if not self._cache_updated_event.is_set(): self._cache_updated_event.set()

    async def _periodic_cache_updater(self):
        logger.info("RoutesAPIClient: Starting periodic cache updater...")
        try:
            while True:
                logger.info(f"RoutesAPIClient: Periodic cache updater triggering refresh for sandbox '{self.sandbox_name or 'baseline'}'.")
                await self._ensure_cache_fresh()
                await asyncio.sleep(self.refresh_interval)
        except asyncio.CancelledError:
            logger.info(f"RoutesAPIClient: Periodic cache updater for sandbox '{self.sandbox_name or 'baseline'}' cancelled.")
        except Exception as e:
            logger.error(f"RoutesAPIClient: Periodic cache updater for sandbox '{self.sandbox_name or 'baseline'}' error: {e}")
            # Depending on the error, you might want to add retry logic or stop

    def should_process(self, routing_key: Optional[str]) -> bool:
        
        # await self._ensure_cache_fresh()
        current_cached_keys = self._routing_keys_cache

        if self.sandbox_name: # This is a sandboxed workload
            if routing_key is None:
                logger.info(f"RoutesAPIClient (Sandbox: {self.sandbox_name}): Skipping task, no routing key provided.")
                return False
            
            should = routing_key in current_cached_keys
            log_action = "Processing" if should else "Skipping"
            logger.info(f"RoutesAPIClient (Sandbox: {self.sandbox_name}): {log_action} task with routing key '{routing_key}'. Key in cache: {should}. Cache: {list(current_cached_keys)}.")
            return should
        else: # This is a baseline workload
            if routing_key is None:
                logger.info(f"RoutesAPIClient (Baseline): Processing task, no routing key provided.")
                return True 
            
            should = routing_key not in current_cached_keys
            log_action = "Processing" if should else "Skipping"
            logger.info(f"RoutesAPIClient (Baseline): {log_action} task with routing key '{routing_key}'. Key in cache: {not should}. Cache: {list(current_cached_keys)}.")
            return should