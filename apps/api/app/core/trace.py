"""Request-scoped trace id.

The gateway mints it and forwards ``X-Trace-Id``; when the engine is called
directly (tests, local dev) it mints its own. Log records and the response
header carry the same value, so one id ties a UI action to engine logs.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

TRACE_HEADER = "X-Trace-Id"
_trace_id: ContextVar[str] = ContextVar("trace_id", default="")


def current_trace_id() -> str:
    return _trace_id.get()


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(TRACE_HEADER, "").strip()
        trace_id = incoming[:32] if incoming else new_trace_id()
        token = _trace_id.set(trace_id)
        try:
            response = await call_next(request)
            response.headers[TRACE_HEADER] = trace_id
            return response
        finally:
            _trace_id.reset(token)


class TraceLogFilter(logging.Filter):
    """Puts ``trace`` on every record so the format string can print it."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace"):
            record.trace = current_trace_id() or "-"
        return True


def install_log_filter() -> None:
    root = logging.getLogger()
    if not any(isinstance(item, TraceLogFilter) for item in root.filters):
        root.addFilter(TraceLogFilter())
