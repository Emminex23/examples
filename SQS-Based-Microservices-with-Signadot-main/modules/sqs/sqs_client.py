import boto3
import os
from dotenv import load_dotenv
from botocore.exceptions import ClientError

from modules.logger.logger import get_logger

logger = get_logger(__name__)
load_dotenv()

# AWS Configuration (using environment variables)
AWS_REGION = os.environ.get("AWS_REGION", "")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
QUEUE_NAME = os.environ.get("SQS_QUEUE_NAME", "")


sqs_client = boto3.client(
    "sqs",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)


def create_queue() -> str | None:
    
    # Creates an SQS queue if it does not exist.

    # The `create_queue` action is idempotent. If a queue with the specified name
    # already exists, SQS returns its URL without creating a new one.


    # Returns:
    #     The URL of the queue, or None if an error occurred.
    
    try:
        logger.info(f"Attempting to create SQS queue: '{QUEUE_NAME}'")
        response = sqs_client.create_queue(QueueName=QUEUE_NAME)
        queue_url = response.get('QueueUrl')
        logger.info(f"Queue '{QUEUE_NAME}' is available at URL: {queue_url}")
        return queue_url
    except ClientError as e:
        logger.error(f"AWS client error while creating queue '{QUEUE_NAME}': {e}")
        return None


def get_queue_arn(queue_url: str) -> str | None:
    
    # Gets the ARN of an SQS queue.

    # Args:
    #     queue_url: The URL of the queue.

    # Returns:
    #     The ARN of the queue, or None if an error occurred.
    
    try:
        response = sqs_client.get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn']
        )
        return response['Attributes']['QueueArn']
    except ClientError as e:
        logger.error(f"Could not get queue ARN for '{queue_url}': {e}")
        return None