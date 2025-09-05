import boto3
import os
import sys
import asyncio
import threading
import json
import time
from dotenv import load_dotenv

# Add the project root to the Python path to allow for absolute imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, project_root)


from modules.sqs.sqs_client import sqs_client, create_queue, QUEUE_NAME
from modules.pull_router.router_api import RoutesAPIClient, FILTER_ATTRIBUTE_NAME, ENV_SANDBOX_KEY
from modules.otel.baggage import extract_routing_key_from_baggage
from modules.events.event import register_event
from modules.logger.logger import get_logger

logger = get_logger(__name__)

load_dotenv()

SANDBOX_NAME=os.getenv(ENV_SANDBOX_KEY, "")

def consume_message(sqs_queue_url: str, router_api: RoutesAPIClient):
    
    # Consumes messages from the SQS queue, applying a filter based on a message attribute.
    
    while True:
        try:
            response = sqs_client.receive_message(
                QueueUrl=sqs_queue_url,
                MaxNumberOfMessages=1,
                MessageAttributeNames=[FILTER_ATTRIBUTE_NAME],
                VisibilityTimeout=60,
                WaitTimeSeconds=20,
            )

            if "Messages" in response:
                message = response["Messages"][0]
                message_body_dict = json.loads(message["Body"])
                message_attributes = message.get("MessageAttributes", {})

                routing_key = extract_routing_key_from_baggage(message_attributes)

                if not router_api.should_process(routing_key):
                    # This message is not for this consumer instance. Make it immediately visible again for other consumers.
                    logger.info(f"Skipping message with routing_key: '{routing_key}'. Releasing back to queue.")
                    sqs_client.change_message_visibility(
                        QueueUrl=sqs_queue_url,
                        ReceiptHandle=message["ReceiptHandle"],
                        VisibilityTimeout=0,
                    )
                    continue

                try:
                    register_event(
                        'Consumed message from sqs queue',
                        message_body_dict,
                        routing_key
                    )
                except IOError as e:
                    logger.error(f"Failed to register event: {e}")
                    # Not re-throwing, as the original code just logs the error and continues.

                logger.info(f"Processing message: {message_body_dict}")
                sqs_client.delete_message(
                    QueueUrl=sqs_queue_url, ReceiptHandle=message["ReceiptHandle"]
                )
            else:
                logger.info("No messages available")

        except Exception as e:
            logger.error(f"Error during consumption: {e}")

def start_async_loop(coro):
    
    # Creates a new event loop in the current thread, runs the given
    # coroutine until it completes, and then closes the loop.
    # This is a utility to run an asyncio task in a separate thread.
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(coro)
    loop.close()

def run_consumer(queue_url: str):
    
    # Initializes and runs the SQS message consumer and its background tasks.
    
    if queue_url:
        # --- Start asyncio background task in a separate thread ---
        routes_client = RoutesAPIClient(sandbox_name=SANDBOX_NAME)
        cache_updater_coro = routes_client._periodic_cache_updater()
        asyncio_thread = threading.Thread(
            target=start_async_loop,
            args=(cache_updater_coro,),
            daemon=True,
        )
        asyncio_thread.start()
        logger.info("Started background cache updater.")

        # --- Start SQS consumer in its own thread ---
        logger.info(f"Starting consumer for queue: {QUEUE_NAME}")
        consumer_thread = threading.Thread(
            target=consume_message, daemon=True, args=(queue_url, routes_client)
        )
        consumer_thread.start()

        # Keep the main thread alive to allow daemon threads to run, and exit gracefully
        try:
            consumer_thread.join()
        except KeyboardInterrupt:
            logger.info("\nShutting down consumer.")
    else:
        logger.error("Could not get queue URL for consumer. Exiting.")