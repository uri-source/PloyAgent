from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import IO, Any

import structlog

from ploy_agent.common.config import settings


class _TeeIO:
    """Mirror structlog output to stdout and the agent log file (line-safe with flush)."""

    __slots__ = ("streams",)

    def __init__(self, *streams: IO[Any]) -> None:
        self.streams = streams

    def write(self, data: str) -> int:
        for s in self.streams:
            s.write(data)
            s.flush()
        return len(data)

    def flush(self) -> None:
        for s in self.streams:
            s.flush()


_log_file_handle: IO[Any] | None = None
_configured = False


def configure_logging() -> None:
    global _log_file_handle, _configured
    if _configured:
        return  # Already configured — avoid duplicate file handles
    log_path = Path(settings.agent_log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("a", encoding="utf-8")
    _log_file_handle = log_file
    _configured = True
    out: IO[Any] = _TeeIO(sys.stdout, log_file)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        timestamper,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
    ]
    if settings.log_json:
        structlog.configure(
            processors=shared
            + [
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=out),
            cache_logger_on_first_use=True,
        )
    else:
        structlog.configure(
            processors=shared
            + [
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(file=out),
            cache_logger_on_first_use=True,
        )


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
