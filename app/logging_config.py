"""لاگ‌گیری — one place that decides how a log line looks and what it carries.

Three pieces, and nothing else:

* :func:`configure_logging` — called once from ``app/main.py`` at import time,
  before anything else logs. Human-readable lines in development, one JSON
  object per line anywhere else, because a deploy's logs are read by a
  collector (Liara, Loki, CloudWatch) and a collector cannot parse prose.
* the **request context** (:data:`request_id_var` / :data:`user_id_var`) — a
  pair of ``ContextVar``s stamped onto *every* record by a filter. This is
  what lets a domain event written deep inside a router be tied back to the
  HTTP request that caused it without threading a ``request`` argument
  through every function that might want to log.
* :func:`log_event` — the only way a domain event is written. It keeps the
  message a stable machine-readable **name** (``challenge.created``) and puts
  the variable parts in structured fields, so a search is ``event=...``
  rather than a regex over a sentence. Rewording a log line then never breaks
  a dashboard, which is the same trade `app/notifications.py` makes.

Two rules worth keeping:

**Levels mean urgency, not verbosity.** ``INFO`` is a business event that
happened as designed; ``WARNING`` is a refusal a member could cause (a wrong
password, a rate limit, a 4xx); ``ERROR`` is something an operator has to
look at. Nothing routine is logged at ``ERROR`` or the level stops meaning
anything and alerts get muted.

**No secrets and no bare PII.** Ids are the join key. Where a human needs to
recognise the row -- support looking up a failed login -- the value is masked
(:func:`mask_email`, and ``app.phone.mask_mobile`` for numbers). Passwords,
OTP codes, session cookies and API keys never reach a formatter.

Performance: formatting is skipped entirely below the active level (``logger.
log`` returns immediately), the access log is one line per request rather
than per query, and static files are not logged at all -- see
``app/middleware.py``.
"""

import json
import logging
import sys
import time
from contextvars import ContextVar
from datetime import UTC, datetime

from app.config import settings

#: Correlation id for the request being served. Set by ``RequestLogMiddleware``
#: and read by the filter below, so every line a request produces carries it.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

#: The signed-in member behind the request, when there is one.
user_id_var: ContextVar[int | None] = ContextVar("user_id", default=None)

# Attributes ``logging`` puts on every record. Anything *not* here is a field
# we added, which is how the formatters find the structured payload without
# each call site having to declare it.
_STANDARD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}


def log_event(logger: logging.Logger, event: str, /, level: int = logging.INFO, **fields) -> None:
    """Write one domain event: a dotted name plus structured fields.

    ``log_event(logger, "enrollment.joined", challenge_id=7)`` rather than
    ``logger.info("user joined challenge %s", 7)`` -- the name is what a
    filter or a metric is built on, and the fields are what a human reads.
    """
    logger.log(level, event, extra=fields)


def mask_email(email: str | None) -> str | None:
    """``mohammad@example.com`` -> ``m***@example.com``.

    Enough for support to recognise an address in a failed-login line without
    the log file becoming a mailing list.
    """
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


class _ContextFilter(logging.Filter):
    """Stamp the current request's correlation fields onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        # `not hasattr` and not a plain assignment: a call site that passed an
        # explicit user_id (a login, where the cookie is not set yet, or an
        # admin acting on somebody else) means that one, and the ambient
        # context must not overwrite it.
        request_id = request_id_var.get()
        if request_id is not None and not hasattr(record, "request_id"):
            record.request_id = request_id
        user_id = user_id_var.get()
        if user_id is not None and not hasattr(record, "user_id"):
            record.user_id = user_id
        return True


def _extra_fields(record: logging.LogRecord) -> dict:
    return {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}


class JsonFormatter(logging.Formatter):
    """One JSON object per line -- the shape a log collector ingests."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            **_extra_fields(record),
        }
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """``12:03:41 INFO  app.routers.auth  auth.login_ok user_id=3`` -- for a terminal."""

    def format(self, record: logging.LogRecord) -> str:
        # Local time on purpose -- this formatter only ever runs in a
        # terminal, where the reader's own clock is the useful one. The JSON
        # side stays UTC, because a collector's is not.
        stamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        fields = " ".join(f"{k}={v}" for k, v in _extra_fields(record).items())
        line = f"{stamp} {record.levelname:<7} {record.name:<28} {record.getMessage()}"
        if fields:
            line = f"{line}  {fields}"
        if record.exc_info:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def configure_logging() -> None:
    """Install the root handler. Idempotent, so an import twice is harmless.

    Everything goes to **stdout** and nothing to a file: the app runs in a
    container, where the process's stdout *is* the log stream and a file
    inside the image is a log nobody will ever read.
    """
    root = logging.getLogger()
    if any(getattr(h, "_chalesh", False) for h in root.handlers):
        return

    json_output = settings.log_format == "json" or (
        settings.log_format == "auto" and settings.environment != "development"
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else TextFormatter())
    handler.addFilter(_ContextFilter())
    handler._chalesh = True  # marks it ours, so a re-run does not stack handlers

    root.handlers = [h for h in root.handlers if not getattr(h, "_chalesh", False)]
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # uvicorn ships its own access log with no request id, no user and no
    # duration. Ours replaces it (see `app/middleware.py`), so this one is
    # silenced rather than duplicated. `uvicorn.error` stays -- it is where
    # startup and connection failures come out.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True

    # Third-party libraries that narrate at INFO. SQLAlchemy logs every
    # statement when echo is on, and httpx logs a line per outbound call --
    # a page render should be one access line, not forty queries, and the SMS
    # gateway already logs its own outcome in `app/sms.py`.
    for noisy in ("sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
