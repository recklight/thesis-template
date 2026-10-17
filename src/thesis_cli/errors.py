"""Exception hierarchy for :mod:`thesis_cli`.

Every error raised deliberately by this package derives from
:class:`ThesisCliError` and carries an ``exit_code``.  The command line
front-end catches the base class once, prints ``str(exc)`` plus the optional
``hint`` and exits with that code, so a script can tell from the exit status
of ``thesis-cli`` what kind of failure it was.  An uncaught traceback would
always exit with 1.

Exit codes
----------
====  =================================================================
Code  Meaning
====  =================================================================
0     Success (no error raised).
1     Generic :class:`ThesisCliError`.
2     :class:`ConfigError`: ``thesis.toml`` missing or invalid.
3     :class:`ProjectLayoutError`: expected file or directory absent.
4     :class:`ToolNotFoundError`: external program not on ``PATH``.
5     :class:`BuildFailedError`: the TeX engine reported a fatal error.
6     :class:`ValidationError`: the input is unusable.
7     :class:`CheckFailedError`: an audit found errors, or warnings under
      ``--strict``.
====  =================================================================
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance
    from .report import CommandReport

__all__ = [
    "BuildFailedError",
    "CheckFailedError",
    "ConfigError",
    "ProjectLayoutError",
    "ThesisCliError",
    "ToolNotFoundError",
    "ValidationError",
]


class ThesisCliError(Exception):
    """Base class for every deliberate failure in :mod:`thesis_cli`.

    Parameters
    ----------
    message : str
        Human readable description of what went wrong.
    hint : str or None, optional
        What to do about it, shown on its own line by the CLI; often the
        command to run next.
    report : CommandReport or None, optional
        Partial findings gathered before the failure.  ``build`` attaches
        the preflight and log tables here so the CLI can still show the
        diagnosis instead of discarding it along with the error.

    Attributes
    ----------
    exit_code : int
        Process exit status the CLI uses for this class of failure.
    """

    exit_code: int = 1

    def __init__(
        self,
        message: str,
        *,
        hint: str | None = None,
        report: CommandReport | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.report: Any = report

    def __str__(self) -> str:
        return self.message


class ConfigError(ThesisCliError):
    """``thesis.toml`` is missing, unparseable, or holds an invalid value."""

    exit_code = 2


class ProjectLayoutError(ThesisCliError):
    """A file or directory the project configuration points at does not exist."""

    exit_code = 3


class ToolNotFoundError(ThesisCliError):
    """A required external executable could not be located on ``PATH``."""

    exit_code = 4


class BuildFailedError(ThesisCliError):
    """The TeX engine (or a helper such as ``bibtex``) exited with an error."""

    exit_code = 5


class ValidationError(ThesisCliError):
    """The input cannot be used as given.

    Raised, for example, when ``build`` is pointed at a LaTeX fragment with
    no preamble, or ``init --set`` names a field that does not exist.  Both
    are refused before anything runs.
    """

    exit_code = 6


class CheckFailedError(ThesisCliError):
    """An audit command found errors, or warnings while ``--strict`` was in effect."""

    exit_code = 7
