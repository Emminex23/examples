import os
import sys
import httpx
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

# Add the project root to the Python path to allow for absolute imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, project_root)


from modules.otel.baggage import extract_routing_key_from_baggage, http_getter
from modules.events.event import register_event, get_events
from modules.pull_router.router_api import FILTER_ATTRIBUTE_NAME
from modules.DataTransferObjects.RequestResponseDto import ProduceMessage, ErrorResponse
from modules.logger.logger import get_logger

logger = get_logger(__name__)


load_dotenv()

PRODUCER_HOST = os.getenv("PRODUCER_HOST", "")
PRODUCER_PORT = int(os.getenv("PRODUCER_PORT", ""))

# --- FastAPI App ---
app = FastAPI()

# --- Static Files ---
# Serve static files from the 'public' folder
public_path = Path(__file__).parent / "public"
app.mount("/static", StaticFiles(directory=public_path), name="static")

# --- Custom Exception Handler ---
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"An unhandled exception occurred: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": {"message": "Internal Server Error"}},
    )

# --- API Endpoints ---
@app.post(
    "/api/produce",
    status_code=200,
    responses={500: {"model": ErrorResponse}}
)
async def produce_message(message: ProduceMessage, request: Request):
    """
    Receives a message and forwards it to the producer service.
    """    
    msg_dict = message.model_dump()

    try:
        register_event(
            'Sending produce request to producer API',
            msg_dict,
            extract_routing_key_from_baggage(request.headers, http_getter)
        )
    except IOError as e:
        logger.error(f"Failed to register event: {e}")
        # Not re-throwing, as the original code just logs the error and continues.

    producer_url = f"http://{PRODUCER_HOST}:{PRODUCER_PORT}/api/produce"
    logger.info(f"Forwarding request to producer at {producer_url}")

    async with httpx.AsyncClient() as client:
        try:
            producer_req = await client.post(
                producer_url,
                json=msg_dict,
                headers={'Content-Type': 'application/json'}
            )
            producer_req.raise_for_status() # Raise exception for 4xx/5xx responses
        except httpx.RequestError as e:
            logger.error(f"Error calling producer API: {e}")
            raise HTTPException(
                status_code=500,
                detail={"message": f"Error connecting to producer service: {e}"}
            ) from e
    
    return {}

@app.get("/api/events", responses={500: {"model": ErrorResponse}})
async def list_events(cursor: int = 0):
    """
    Reads events from the event store starting from the given cursor.
    """
    try:
        events, new_cursor = get_events(events_cursor=cursor)
        return {"events": events, "cursor": new_cursor}
    except IOError as e:
        logger.error(f"Error getting events: {e}")
        raise HTTPException(status_code=500, detail={"message": str(e)}) from e

@app.get("/", include_in_schema=False)
async def read_root():
    """Serves the main index.html page."""
    return FileResponse(public_path / "index.html")