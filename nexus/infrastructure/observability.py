'''Structured logging configuration for Nexus.

Configures structlog with orjson serialization, asyncio-safe context
variable binding, and ISO 8601 UTC timestamps. Call configure_logging()
once at process startup before any other initialization.
'''

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import orjson
import structlog

__all__ = [
    'bind_context',
    'bound_context',
    'clear_context',
    'configure_logging',
    'get_logger',
]


def _orjson_dumps_str(*args: Any, **kwargs: Any) -> str:
    '''Serialize to JSON string via orjson for stdlib ProcessorFormatter.

    Returns:
        str: JSON-encoded string.
    '''

    return orjson.dumps(*args, **kwargs).decode()


def configure_logging(log_level: str = 'INFO') -> None:
    '''Configure structlog with orjson JSON rendering to stdout.

    Args:
        log_level: Minimum log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    '''

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt='iso', utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.JSONRenderer(serializer=orjson.dumps),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.BytesLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(serializer=_orjson_dumps_str),
        ],
        foreign_pre_chain=[
            structlog.stdlib.ExtraAdder(),
            *shared_processors,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)


def bind_context(**kwargs: Any) -> None:
    '''Bind key-value pairs to the asyncio-safe structlog context.

    Args:
        **kwargs: Context fields (account_id, trade_id, strategy_id, etc.).
    '''

    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    '''Clear all bound context variables.'''

    structlog.contextvars.clear_contextvars()


@contextmanager
def bound_context(**kwargs: Any) -> Iterator[None]:
    '''Bind kwargs to the structlog context for the lifetime of the with-block.

    Wraps `structlog.contextvars.bound_contextvars` so callers do not
    need to import structlog directly and so the per-iteration scope
    is leak-proof: keys bound on entry are reset on exit (including
    the exception path), restoring whatever the caller's context was
    before the bind. Use this around per-action loop iterations so
    every downstream emit carries the action's correlation fields
    without each emit site having to thread them through `extra={...}`.

    Args:
        **kwargs: Context fields to bind for the with-block lifetime.

    Yields:
        None.
    '''

    with structlog.contextvars.bound_contextvars(**kwargs):
        yield


def get_logger(name: str) -> Any:
    '''Return a structlog logger bound to the given name.

    Args:
        name: Logger name, typically __name__.

    Returns:
        Configured structlog bound logger.
    '''

    return structlog.get_logger(name)
