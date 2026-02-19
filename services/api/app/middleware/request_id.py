import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from structlog.contextvars import bind_contextvars, clear_contextvars

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        clear_contextvars()
        rid = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        bind_contextvars(request_id=rid)
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
