import os
import sys
from fastapi import FastAPI, Request, Form
import json
 
from dotenv import load_dotenv

# Add the project root to the Python path to allow for absolute imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, project_root)

from modules.sqs.sqs_client import sqs_client, QUEUE_NAME
from modules.sns.sns_client import sns_client
from modules.pull_router.router_api import FILTER_ATTRIBUTE_NAME
from modules.otel.baggage import extract_routing_key_from_baggage, http_getter
from modules.events.event import register_event
from modules.logger.logger import get_logger
from modules.DataTransferObjects.RequestResponseDto import ProduceMessage, ErrorResponse

load_dotenv()

app = FastAPI()

SQS_QUEUE_URL = os.environ.get("SQS_QUEUE_URL", "")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")
SNS_FANOUT_PUBLISH = os.environ.get("SNS_FANOUT_PUBLISH", None)

# --- Logging ---
logger = get_logger(__name__)


# --- API Endpoints ---
@app.post(
    "/api/produce",
    status_code=200,
    responses={500: {"model": ErrorResponse}}
)
async def produce_message(message: ProduceMessage, request: Request):
    try:
        """
        Receives a message and forwards it to an SQS queue or an SNS topic.
        """
        
        msg_dict = message.model_dump()

        event_description = (
            'Sending produce request to SNS topic'
            if SNS_FANOUT_PUBLISH
            else 'Sending produce request to SQS queue'
        )        

        try:
            register_event(
                event_description,
                msg_dict,
                extract_routing_key_from_baggage(request.headers, http_getter)
            )
        except IOError as e:
            logger.error(f"Failed to register event: {e}")
            # Not re-throwing, as the original code just logs the error and continues.

        if SNS_FANOUT_PUBLISH:
            # Publish message to SNS topic
            logger.info(f"Publishing message to SNS topic: {SNS_TOPIC_ARN}")
            response = sns_client.publish(
                TopicArn=SNS_TOPIC_ARN,
                Message=json.dumps(msg_dict)
            )
        else:
            # Send message to SQS queue
            logger.info(f"Sending message to SQS queue: {SQS_QUEUE_URL}")
            response = sqs_client.send_message(
                QueueUrl=SQS_QUEUE_URL,
                MessageBody=json.dumps(msg_dict)
            )
        logger.info(response)
        return {}

    except Exception as e:  # Catch exceptions for error handling
        logger.error(f"Exception occurred: {e}")