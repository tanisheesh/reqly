"""Log output: plain text (default) or one JSON object per line
(LOG_FORMAT=json) for log pipelines such as Loki, CloudWatch or Datadog."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

# uvicorn configures these itself before the app is imported; they are
# pointed at the root handler so every line has the same format.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        # uvicorn's access line: (client, method, path, http version, status)
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, path, _version, status = record.args
            entry.update(client=client, method=method, path=path, status=status)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(log_format: str) -> None:
    if log_format != "json":
        logging.basicConfig(level=logging.INFO)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
