import argparse
import subprocess
import os
from modules.logger.logger import get_logger

logger = get_logger(__name__)

def run_producer(queue_url: str, topic_arn: str):

    # Launches the FastAPI producer app using Uvicorn as a subprocess with OTel auto-instrumentation
    # and handles graceful shutdown on KeyboardInterrupt (Ctrl+C).

    # Set the queue URL as an environment variable for the producer subprocess
    env = os.environ.copy()
    env["SQS_QUEUE_URL"] = queue_url
    env["SNS_TOPIC_ARN"] = topic_arn

    command = ["opentelemetry-instrument", "uvicorn", "apps.producer.app:app", "--host", "0.0.0.0", "--port", "8000"]
    logger.info(f"Starting producer server with command: {' '.join(command)}")
    # Pass the modified environment to the subprocess
    process = subprocess.Popen(command, env=env)
    try:
        # Wait for the subprocess to complete. This is a blocking call.
        process.wait()
    except KeyboardInterrupt:
        logger.info("\nShutting down producer server...")
        process.terminate()  # Send SIGTERM to Uvicorn for graceful shutdown
        process.wait()       # Wait for the process to fully terminate

def run_frontend():
    # Launches the FastAPI frontend app using Uvicorn as a subprocess with OTel auto-instrumentation
    # and handles graceful shutdown on KeyboardInterrupt (Ctrl+C).

    command = ["opentelemetry-instrument", "uvicorn", "apps.frontend.app:app", "--host", "0.0.0.0", "--port", "8000"]
    logger.info(f"Starting frontend server with command: {' '.join(command)}")
    # Pass the modified environment to the subprocess
    process = subprocess.Popen(command)
    try:
        # Wait for the subprocess to complete. This is a blocking call.
        process.wait()
    except KeyboardInterrupt:
        logger.info("\nShutting down frontend server...")
        process.terminate()  # Send SIGTERM to Uvicorn for graceful shutdown
        process.wait()       # Wait for the process to fully terminate

def main():
    parser = argparse.ArgumentParser(description="Run sub-applications")
    parser.add_argument('-p', '--producer', action='store_true', help='Run producer app (FastAPI with Uvicorn)')
    parser.add_argument('-C', '--consumer', action='store_true', help='Run Consumer app')
    parser.add_argument('-f', '--frontend', action='store_true', help='Run Frontend app')

    args = parser.parse_args()

    # If any app is selected that needs the queue, create it first.
    queue_url = None
    topic_arn = None
    
    if args.producer or args.consumer:
        # Lazy load the AWS modules only when the producer or consumer are
        # actually run. This prevents services that do not need them (like
        # the frontend) from crashing if AWS credentials are not configured
        # in their environment.
        from modules.sqs.sqs_client import create_queue, get_queue_arn
        from modules.sns.sns_client import create_topic, subscribe_sqs_to_sns

        logger.info("Initializing SQS queue...")
        queue_url = create_queue()
        if not queue_url:
            logger.error("Failed to create or get SQS queue. Exiting.")
            return

        logger.info("Initializing SNS topic and subscription...")
        topic_arn = create_topic()
        if not topic_arn:
            logger.error("Failed to create or get SNS topic. Exiting.")
            return

        queue_arn = get_queue_arn(queue_url)
        if not queue_arn:
            logger.error("Failed to get SQS queue ARN. Exiting.")
            return

        subscription_arn = subscribe_sqs_to_sns(topic_arn, queue_arn, queue_url)
        if not subscription_arn:
            logger.error("Failed to subscribe SQS queue to SNS topic. Exiting.")
            return
        logger.info(f"Successfully subscribed queue to topic. Subscription ARN: {subscription_arn}")

    ran_any = False
    if args.producer:
        run_producer(queue_url, topic_arn)
        ran_any = True
    if args.consumer:
        # Lazy load the AWS modules only when the producer or consumer are
        # actually run. This prevents services that do not need them (like
        # the frontend) from crashing if AWS credentials are not configured
        # in their environment.
        from apps.consumer.app import run_consumer
        run_consumer(queue_url)
        ran_any = True
    if args.frontend:
        run_frontend()
        ran_any = True

    if not ran_any:
        parser.print_help()

if __name__ == '__main__':
    main()