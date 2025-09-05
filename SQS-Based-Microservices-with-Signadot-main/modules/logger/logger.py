import logging
import sys
import os

log_level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
log_level = logging.getLevelName(log_level_name)

# If the provided log level is invalid, default to INFO
if not isinstance(log_level, int):
    log_level = logging.INFO

# basicConfig does nothing if the root logger already has handlers configured.
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)