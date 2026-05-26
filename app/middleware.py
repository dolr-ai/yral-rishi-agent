import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id

        import sentry_sdk

        sentry_sdk.set_tag("request_id", request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
