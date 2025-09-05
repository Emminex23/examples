import os
import json
from datetime import datetime, timezone
import sys
from modules.logger.logger import get_logger
from modules.pull_router.router_api import ENV_SANDBOX_KEY

# Import the initialized redis_client
# The application should handle the case where redis_client is None.
from .init_redis import redis_client
logger = get_logger(__name__)

# Get configuration from environment variables, with defaults.
# This replaces the import from a config file in the original JS.
BASELINE_NAMESPACE = os.environ.get('BASELINE_NAMESPACE', 'default')
BASELINE_NAME = os.environ.get('BASELINE_NAME', '')
SANDBOX_NAME = os.environ.get(ENV_SANDBOX_KEY, '')

def _check_redis_client():
    # Helper to check if Redis client is available.
    if not redis_client:
        raise ConnectionError("Redis client is not initialized or connected.")

def construct_event(log_entry, message, routing_key):
    # Constructs an event dictionary.
    return {
        # Use ISO 8601 format with UTC timezone for consistency
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'logEntry': log_entry,
        'context': {
            'baselineWorkload': {
                'namespace': BASELINE_NAMESPACE,
                'name': BASELINE_NAME,
            },
            'routingKey': routing_key,
            'sandboxName': SANDBOX_NAME,
            'message': message,
        }
    }

def generate_event_id():
    
    # Generates a new unique event ID by incrementing a Redis counter.
    # Raises:
    #     redis.exceptions.RedisError: If the INCR command fails.
    
    _check_redis_client()
    try:
        return redis_client.incr("event_counter")
    except Exception as e:
        logger.error(f"Couldn't generate event ID: {e}")
        raise

def store_event(event):
    
    # Stores an event in Redis with a generated ID and a 30-second expiry.
    # Args:
    #     event (dict): The event object to store.
    # Raises:
    #     Exception: If storing the event fails.
    
    _check_redis_client()
    try:
        event_id = generate_event_id()
        event_data = {
            'id': event_id,
            'body': event,
        }
        # SETEX sets a key with an expiration time in seconds.
        redis_client.setex(
            f"event-{event_id}",
            30,
            json.dumps(event_data)
        )
        logger.info(f"Successfully stored event id={event_id}, body={json.dumps(event)} in redis")
        return event_id
    except Exception as e:
        logger.error(f"Couldn't store event in redis: {e}")
        raise

def register_event(log_entry, message, routing_key):
    
    # High-level function to construct and store an event.
    # This is the Pythonic equivalent of the JS version that used callbacks.
    # Errors are handled via exceptions.
    
    try:
        event = construct_event(log_entry, message, routing_key)
        store_event(event)
    except Exception as e:
        logger.error(f"Failed to register event: {e}")
        raise

def get_events(events_cursor=0, cursor=0):
    
    # Scans and retrieves all new events from Redis since the last cursor.
    # This function is more Pythonic, using a loop and returning results
    # instead of using nested callbacks.
    # Args:
    #     events_cursor (int): The ID of the last event seen by the client.
    #                          Only events with a higher ID will be returned.
    #     cursor (int): The Redis SCAN cursor to start from.
    # Returns:
    #     A tuple containing (list_of_events, last_event_id).
    # Raises:
    #     Exception: If reading from Redis fails.
    
    _check_redis_client()
    events = []
    last_event_id = 0
    
    try:
        current_cursor = cursor
        while True:
            current_cursor, keys = redis_client.scan(current_cursor, match='event-*', count=10)
            
            if keys:
                values = redis_client.mget(keys)
                for value in values:
                    if not value: continue
                    event_data = json.loads(value)
                    if event_data.get('id', 0) <= events_cursor: continue
                    if event_data.get('id', 0) > last_event_id:
                        last_event_id = event_data['id']
                    if 'body' in event_data:
                        events.append(event_data['body'])
            
            if current_cursor == 0:
                break
        
        events.sort(key=lambda e: e.get('timestamp', ''))
        return events, last_event_id
        
    except Exception as e:
        logger.error(f"Couldn't get events from Redis: {e}")
        raise

def set_keys(key, value):
    # Sets a simple key-value pair in Redis.
    _check_redis_client()
    redis_client.set(key, value)

def get_keys(key):
    # Gets a value for a key from Redis.
    _check_redis_client()
    return redis_client.get(key)
