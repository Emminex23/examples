from pydantic import BaseModel

# --- Pydantic Models for Request/Response validation ---
class ProduceMessage(BaseModel):
    id: str
    body: str

class ErrorDetail(BaseModel):
    message: str

class ErrorResponse(BaseModel):
    error: ErrorDetail