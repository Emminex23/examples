import os
import redis

from modules.logger.logger import get_logger

# Get Redis connection details from environment variables.
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis')
REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
REDIS_DB = int(os.environ.get('REDIS_DB', 0))

redis_client = None
logger = get_logger(__name__)

try:
    # Created the client by passing connection parameters directly.
    redis_client = redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        decode_responses=True
    )

    # The 'connect' and 'ready' events in redis can be simulated by
    # pinging the server. If ping() doesn't raise an exception, we are connected.
    redis_client.ping()
    logger.info(f'Connected to Redis at {REDIS_HOST}:{REDIS_PORT}')
    logger.info('Redis is ready')

except redis.exceptions.ConnectionError as e:
    logger.error(f'Redis error: {e}')
    # The client will be None, so other parts of the application can check for this.
except Exception as e:
    logger.error(f'An unexpected error occurred during Redis initialization: {e}')

# In redis-py, runtime connection errors or other issues raise exceptions
# at the time a command is called, which should be handled with try...except blocks
