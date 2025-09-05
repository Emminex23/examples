import boto3
import os
import json
from dotenv import load_dotenv
from botocore.exceptions import ClientError

from modules.sqs.sqs_client import sqs_client
from modules.logger.logger import get_logger

logger = get_logger(__name__)

load_dotenv()

# AWS Configuration
AWS_REGION = os.environ.get("AWS_REGION", "")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
TOPIC_NAME = os.environ.get("SNS_TOPIC_NAME", "")


sns_client = boto3.client(
    "sns",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)

def create_topic() -> str | None:
    
    # Creates an SNS topic if it does not exist.

    # Args:
    #    topic_name: The name of the topic to create.

    # Returns:
        # The ARN of the topic, or None if an error occurred.
        
    try:
        logger.info(f"Attempting to create SNS topic: '{TOPIC_NAME}'")
        response = sns_client.create_topic(Name=TOPIC_NAME)
        topic_arn = response.get('TopicArn')
        logger.info(f"Topic '{TOPIC_NAME}' is available at ARN: {topic_arn}")
        return topic_arn
    except ClientError as e:
        logger.error(f"AWS client error while creating topic '{TOPIC_NAME}': {e}")
        return None

def subscribe_sqs_to_sns(topic_arn: str, queue_arn: str, queue_url: str) -> str | None:

    # Subscribes an SQS queue to an SNS topic.

    # This involves:
    # 1. Creating an IAM policy to allow the SNS topic to send messages to the SQS queue.
    # 2. Setting the policy on the SQS queue.
    # 3. Creating the subscription in SNS with RawMessageDelivery.

    # Args:
    #     topic_arn: The ARN of the SNS topic.
    #     queue_arn: The ARN of the SQS queue.
    #     queue_url: The URL of the SQS queue.
    
    # Returns:
    #     The subscription ARN, or None if an error occurred.

    try:
        # Create a policy to allow SNS to send messages to the SQS queue
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": "sqs:SendMessage",
                    "Resource": queue_arn,
                    "Condition": {
                        "ArnEquals": {
                            "aws:SourceArn": topic_arn
                        }
                    }
                }
            ]
        }

        sqs_client.set_queue_attributes(QueueUrl=queue_url, Attributes={'Policy': json.dumps(policy)})
        logger.info(f"Set permission for topic {topic_arn} to send to queue {queue_arn}")

        # Create the subscription with RawMessageDelivery enabled
        subscription = sns_client.subscribe(
            TopicArn=topic_arn, Protocol='sqs', Endpoint=queue_arn, ReturnSubscriptionArn=True,
            Attributes={'RawMessageDelivery': 'true'}
        )
        logger.info(f"Subscribed queue {queue_arn} to topic {topic_arn}.")
        return subscription.get('SubscriptionArn')
    except ClientError as e:
        logger.error(f"Error subscribing SQS queue to SNS topic: {e}")
        return None
