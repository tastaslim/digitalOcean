import json
import logging
import time

from app.common.middleware.requestIdMiddleware import REQUEST_ID_VAR


class JsonFormatter(logging.Formatter):
    """
    Emits every log record as a single JSON line.

    Fields:
      ts        — ISO-8601 UTC timestamp
      level     — DEBUG / INFO / WARNING / ERROR / CRITICAL
      logger    — dotted logger name (e.g. app.core.shadowWorker)
      message   — the formatted log message
      requestId — value from REQUEST_ID_VAR; "-" when outside a request context
      exception — formatted traceback (only present when exc_info is set)
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "requestId": REQUEST_ID_VAR.get("-"),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)
