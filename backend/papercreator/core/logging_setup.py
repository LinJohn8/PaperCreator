"""Logging configuration.

Three sinks, all installed by :func:`setup_logging` exactly once at startup:

* **stderr** - human-readable, level from ``PC_LOG_LEVEL``. This is what the
  Electron main process captures and shows in the app's Output panel.
* ``<home>/logs/papercreator.log`` - rotating (5 MB x 5), always DEBUG so a bug
  report has detail even when the console was quiet.
* ``<home>/logs/errors.log`` - rotating (2 MB x 3), WARNING and above only.
  First place to look when triaging.

A :class:`SecretFilter` scrubs anything that looks like an API key from every
record before it reaches a sink. Logs of HTTP requests include full URLs, and
some providers take keys as query parameters, so redaction happens centrally
here rather than trusting every call site.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

from .paths import get_paths

_configured = False

# sk-..., github/hf style tokens, Bearer headers, and ?api_key= / &key= params.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(gh[pousr]_[A-Za-z0-9]{16,})"),
    re.compile(r"(hf_[A-Za-z0-9]{16,})"),
    re.compile(r"((?:Bearer|Basic)\s+)([A-Za-z0-9._\-+/=]{12,})", re.IGNORECASE),
    re.compile(r"([?&](?:api_?key|key|token|access_token|apikey)=)([^&\s\"']+)",
               re.IGNORECASE),
)


def scrub(text: str) -> str:
    """Replace credential-looking substrings with ``***``.

    Keeps any leading group (``Bearer ``, ``?api_key=``) so the shape of the
    message stays readable.
    """
    out = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            out = pattern.sub(lambda m: f"{m.group(1)}***", out)
        else:
            out = pattern.sub("***", out)
    return out


class SecretFilter(logging.Filter):
    """Scrub the formatted message and args of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and record.msg:
            record.msg = scrub(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: scrub(v) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    scrub(a) if isinstance(a, str) else a for a in record.args
                )
        return True


_CONSOLE_FMT = "%(asctime)s %(levelname)-7s %(name)-28s %(message)s"
_FILE_FMT = "%(asctime)s %(levelname)-7s %(name)-32s [%(threadName)s] %(message)s"


def setup_logging(level: str = "INFO", logs_dir: Path | None = None) -> Path:
    """Install the sinks. Idempotent; returns the main log file path."""
    global _configured
    directory = logs_dir or get_paths().logs_dir
    directory.mkdir(parents=True, exist_ok=True)
    main_log = directory / "papercreator.log"
    if _configured:
        return main_log

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # sinks filter individually
    secret_filter = SecretFilter()

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))
    console.setFormatter(logging.Formatter(_CONSOLE_FMT, datefmt="%H:%M:%S"))
    console.addFilter(secret_filter)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        main_log, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_FILE_FMT))
    file_handler.addFilter(secret_filter)
    root.addHandler(file_handler)

    error_handler = logging.handlers.RotatingFileHandler(
        directory / "errors.log", maxBytes=2 * 1024 * 1024, backupCount=3,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(logging.Formatter(_FILE_FMT))
    error_handler.addFilter(secret_filter)
    root.addHandler(error_handler)

    # These libraries log one line per request at INFO, which drowns out ours
    # during a 10-provider search.
    for noisy in ("httpx", "httpcore", "urllib3", "numba", "matplotlib",
                  "sentence_transformers", "transformers", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # uvicorn.access duplicates our own request log middleware.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    _configured = True
    return main_log


def get_logger(name: str) -> logging.Logger:
    """Namespaced logger. ``name`` is usually ``__name__``."""
    if not name.startswith("papercreator"):
        name = f"papercreator.{name}"
    return logging.getLogger(name)
