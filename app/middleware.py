"""One access-log line per request, plus the correlation id everything else hangs off.

This is the only place an HTTP request is logged. A router logs the *event*
("a challenge was created"); the middleware logs the *transaction* (method,
path, status, how long it took, who asked). Keeping the two apart is what
stops every handler from repeating the same five fields.

Three deliberate choices:

* **The request id is accepted from the edge.** Liara's proxy (and any other)
  may already have stamped ``X-Request-ID``; reusing it means a line in our
  log and a line in the proxy's are the same request. Otherwise one is
  minted. It goes back out on the response, so a member reporting a broken
  page can hand over the id from their network tab.
* **The user is read from the session cookie, not from the database.**
  ``verify_session_cookie`` is an HMAC check with no I/O, so attributing a
  line to a member costs nothing. It is also why the id can be logged for a
  request whose handler never took a ``get_current_user`` dependency.
* **Static files are not logged.** They are the majority of requests and
  carry no information; logging them buries the ones that matter.

Slow requests are logged at ``WARNING`` with the same event name, so the
"is anything slow" question is a level filter rather than a separate metric
pipeline -- the simplest thing that answers it without adding a dependency.
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.auth import verify_session_cookie
from app.logging_config import log_event, request_id_var, user_id_var

logger = logging.getLogger("app.request")

#: Above this, a request is logged at WARNING instead of INFO. Generous on
#: purpose -- an SSR page that renders a year of check-ins is allowed to be
#: slow-ish, and a threshold that fires on healthy traffic gets ignored.
SLOW_REQUEST_MS = 1000

_UNLOGGED_PREFIXES = ("/static/", "/favicon.ico")


class RequestLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        id_token = request_id_var.set(request_id)
        user_token = user_id_var.set(verify_session_cookie(request.cookies.get("session")))
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # The exception handlers below turn this into a 500 response; here
            # it is logged with its traceback *and* the request context, which
            # the handler no longer has. Re-raised untouched.
            logger.exception(
                "http.unhandled_error",
                extra=self._fields(request, 500, started),
            )
            raise
        finally:
            request_id_var.reset(id_token)
            user_id_var.reset(user_token)

        response.headers["X-Request-ID"] = request_id
        if not request.url.path.startswith(_UNLOGGED_PREFIXES):
            fields = self._fields(request, response.status_code, started)
            log_event(
                logger,
                "http.request",
                level=self._level(response.status_code, fields["duration_ms"]),
                request_id=request_id,
                **fields,
            )
        return response

    @staticmethod
    def _level(status: int, duration_ms: float) -> int:
        if status >= 500:
            return logging.ERROR
        if status >= 400 or duration_ms >= SLOW_REQUEST_MS:
            return logging.WARNING
        return logging.INFO

    @staticmethod
    def _fields(request: Request, status: int, started: float) -> dict:
        return {
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            "client_ip": (request.client.host if request.client else None),
        }
