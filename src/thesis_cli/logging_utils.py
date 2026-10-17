"""Logging setup for the command line front-end.

Library modules never configure logging; they only call
``logging.getLogger(__name__)``.  The CLI sets it up here when it starts.
Diagnostics go to *stderr* so that ``--json`` output on *stdout* stays
machine readable.
"""

from __future__ import annotations

import logging
import sys
from typing import Final

__all__ = ["configure_logging", "get_logger"]

_ROOT_LOGGER_NAME: Final[str] = "thesis_cli"
_LEVELS: Final[tuple[int, ...]] = (logging.WARNING, logging.INFO, logging.DEBUG)


def configure_logging(verbosity: int = 0, *, quiet: bool = False) -> logging.Logger:
    """Install a single stderr handler on the package logger.

    Parameters
    ----------
    verbosity : int, optional
        ``0`` shows warnings and errors, ``1`` adds progress information,
        ``2`` or more adds debug output.  Values are clamped to the
        available range.
    quiet : bool, optional
        Suppress everything below :data:`logging.ERROR`.  Overrides
        *verbosity*.

    Returns
    -------
    logging.Logger
        The configured package-level logger.

    Notes
    -----
    Handlers from a previous call are removed first, so calling this again,
    as the tests do, never prints a message twice.
    """
    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    level = logging.ERROR if quiet else _LEVELS[min(max(verbosity, 0), len(_LEVELS) - 1)]

    handler: logging.Handler
    try:  # pragma: no cover - depends on the optional rich console
        from rich.console import Console
        from rich.logging import RichHandler

        # Without an explicit console RichHandler writes to rich's global
        # one, which is stdout, and a warning would end up inside --json.
        handler = RichHandler(
            console=Console(stderr=True),
            show_path=False,
            show_time=False,
            rich_tracebacks=False,
            markup=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
    except ImportError:  # pragma: no cover - plain fallback
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))

    handler.setLevel(level)
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return the module logger for *name*.

    Parameters
    ----------
    name : str
        Usually ``__name__`` of the calling module.

    Returns
    -------
    logging.Logger
        A child of the ``thesis_cli`` logger.
    """
    return logging.getLogger(name)
