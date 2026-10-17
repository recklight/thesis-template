"""One module per subcommand.

Each module exposes a single function that takes a
:class:`thesis_cli.config.ThesisConfig` and returns a
:class:`thesis_cli.report.CommandReport`.  The functions never print and
never call :func:`sys.exit`, so they can be used as a library and tested
without a terminal; rendering and exit codes belong to
:mod:`thesis_cli.cli`.
"""

from __future__ import annotations

__all__ = [
    "build",
    "check_refs",
    "clean",
    "deps",
    "doctor",
    "figures",
    "init_project",
    "stats",
]
