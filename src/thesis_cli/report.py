"""The report every audit command returns, and how it is printed.

Each command builds a :class:`CommandReport`, and :func:`render_report`
prints it as tables or as JSON.  The exit status is computed from the
findings rather than set by hand in each command, so any command run with
``--strict`` can serve as a CI gate.  The JSON has the same shape
for every command, so a script can read ``findings[].severity`` without
caring which command wrote it.

Tables are drawn with Rich when it can be imported, as plain text
otherwise.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final, TextIO

from .logging_utils import get_logger

__all__ = [
    "CommandReport",
    "Finding",
    "ReportTable",
    "Severity",
    "render_report",
]

_LOG = get_logger(__name__)


class Severity(str, Enum):
    """Severity of a single finding."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    OK = "ok"

    @property
    def rank(self) -> int:
        """int: sort key, lower is more severe."""
        return {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2, Severity.OK: 3}[self]


_SEVERITY_STYLE: Final[Mapping[Severity, str]] = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "yellow",
    Severity.INFO: "cyan",
    Severity.OK: "green",
}


@dataclass(frozen=True, slots=True, kw_only=True)
class Finding:
    """One problem (or confirmation) produced by a command.

    Attributes
    ----------
    severity : Severity
        How serious the finding is.
    kind : str
        Stable machine-readable category, e.g. ``"undefined-citation"``.
    message : str
        One-sentence description.
    location : str
        ``file:line`` when known, otherwise ``"-"``.
    detail : str
        Optional extra context shown under ``--verbose``.
    """

    severity: Severity
    kind: str
    message: str
    location: str = "-"
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-ready mapping.

        Returns
        -------
        dict of str to str
            Keys ``severity``, ``kind``, ``message``, ``location``, ``detail``.
        """
        return {
            "severity": self.severity.value,
            "kind": self.kind,
            "message": self.message,
            "location": self.location,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class ReportTable:
    """A tabular block inside a report."""

    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready mapping of the table."""
        return {
            "title": self.title,
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandReport:
    """The complete result of one command.

    Attributes
    ----------
    command : str
        Name of the command that produced this report.
    findings : tuple of Finding
        Problems discovered, in the order they should be shown.
    tables : tuple of ReportTable
        Supporting tabular output.
    summary : mapping
        Scalar values worth exposing to scripts (counts, totals, paths).
    """

    command: str
    findings: tuple[Finding, ...] = ()
    tables: tuple[ReportTable, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)

    @property
    def error_count(self) -> int:
        """int: number of error-severity findings."""
        return sum(1 for item in self.findings if item.severity is Severity.ERROR)

    @property
    def warning_count(self) -> int:
        """int: number of warning-severity findings."""
        return sum(1 for item in self.findings if item.severity is Severity.WARNING)

    @property
    def is_clean(self) -> bool:
        """bool: whether the command found nothing worse than information."""
        return self.error_count == 0 and self.warning_count == 0

    def sorted_findings(self) -> tuple[Finding, ...]:
        """Return the findings ordered by severity, kind, then location.

        Returns
        -------
        tuple of Finding
            Errors first, then warnings, information and ``ok``.
        """
        return tuple(
            sorted(self.findings, key=lambda item: (item.severity.rank, item.kind, item.location))
        )

    def exit_code(self, *, strict: bool = False) -> int:
        """Derive the process exit status.

        Parameters
        ----------
        strict : bool, optional
            Treat warnings as failures as well.

        Returns
        -------
        int
            ``0`` when the command passed, otherwise
            :attr:`thesis_cli.errors.CheckFailedError.exit_code`.

        Examples
        --------
        >>> report = CommandReport(
        ...     command="check-refs",
        ...     findings=(Finding(severity=Severity.WARNING, kind="k", message="m"),),
        ... )
        >>> report.exit_code(), report.exit_code(strict=True)
        (0, 7)
        """
        from .errors import CheckFailedError

        if self.error_count:
            return CheckFailedError.exit_code
        if strict and self.warning_count:
            return CheckFailedError.exit_code
        return 0

    def to_dict(self) -> dict[str, Any]:
        """Return the whole report as a JSON-ready mapping."""
        return {
            "command": self.command,
            "summary": dict(self.summary),
            "counts": {
                "error": self.error_count,
                "warning": self.warning_count,
                "info": sum(1 for item in self.findings if item.severity is Severity.INFO),
            },
            "findings": [item.to_dict() for item in self.sorted_findings()],
            "tables": [table.to_dict() for table in self.tables],
        }

    def to_json(self, *, indent: int = 2) -> str:
        """Serialise the report.

        Parameters
        ----------
        indent : int, optional
            JSON indentation.

        Returns
        -------
        str
            JSON text with Chinese and other non-ASCII characters written
            as they are, not as ``\\u`` escapes.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)


def _plain_table(table: ReportTable, stream: TextIO) -> None:
    """Render a table without Rich, aligning columns by width."""
    widths = [len(column) for column in table.columns]
    for row in table.rows:
        for index, cell in enumerate(row):
            if index < len(widths):
                widths[index] = max(widths[index], len(str(cell)))
    separator = "  ".join("-" * width for width in widths)
    print(f"\n{table.title}", file=stream)
    print("  ".join(column.ljust(widths[i]) for i, column in enumerate(table.columns)), file=stream)
    print(separator, file=stream)
    for row in table.rows:
        cells = [str(cell).ljust(widths[i]) for i, cell in enumerate(row) if i < len(widths)]
        print("  ".join(cells), file=stream)
    for note in table.notes:
        print(f"  {note}", file=stream)


def _render_plain(report: CommandReport, stream: TextIO, *, verbose: bool) -> None:
    """Render a whole report as plain text."""
    print(f"== {report.command} ==", file=stream)
    for table in report.tables:
        _plain_table(table, stream)
    findings = report.sorted_findings()
    if findings:
        rows = tuple(
            (item.severity.value, item.kind, item.location, item.message) for item in findings
        )
        _plain_table(
            ReportTable(
                title="\nFindings",
                columns=("severity", "kind", "location", "message"),
                rows=rows,
            ),
            stream,
        )
        if verbose:
            for item in findings:
                if item.detail:
                    print(f"  {item.location}: {item.detail}", file=stream)
    if report.summary:
        print("\nSummary", file=stream)
        for key, value in report.summary.items():
            print(f"  {key}: {value}", file=stream)
    print(
        f"\n{report.error_count} error(s), {report.warning_count} warning(s).",
        file=stream,
    )


def _render_rich(report: CommandReport, *, verbose: bool) -> bool:
    """Render with Rich; return ``False`` when Rich is unavailable."""
    try:
        from rich.console import Console
        from rich.table import Table as RichTable
    except ImportError:  # pragma: no cover - rich is a declared dependency
        return False

    console = Console()
    console.rule(f"[bold]{report.command}")

    for table in report.tables:
        rich_table = RichTable(title=table.title, header_style="bold", title_justify="left")
        for column in table.columns:
            rich_table.add_column(column, overflow="fold")
        for row in table.rows:
            rich_table.add_row(*(str(cell) for cell in row))
        console.print(rich_table)
        for note in table.notes:
            console.print(f"  [dim]{note}[/dim]")

    findings = report.sorted_findings()
    if findings:
        rich_table = RichTable(title="Findings", header_style="bold", title_justify="left")
        rich_table.add_column("severity")
        rich_table.add_column("kind")
        rich_table.add_column("location", overflow="fold")
        rich_table.add_column("message", overflow="fold")
        for item in findings:
            style = _SEVERITY_STYLE[item.severity]
            rich_table.add_row(
                f"[{style}]{item.severity.value}[/{style}]",
                item.kind,
                item.location,
                item.message,
            )
        console.print(rich_table)
        if verbose:
            for item in findings:
                if item.detail:
                    console.print(f"  [dim]{item.location}: {item.detail}[/dim]")

    if report.summary:
        summary_table = RichTable(
            title="Summary", header_style="bold", show_header=False, title_justify="left"
        )
        summary_table.add_column("key", style="bold")
        summary_table.add_column("value", overflow="fold")
        for key, value in report.summary.items():
            summary_table.add_row(str(key), str(value))
        console.print(summary_table)

    if report.error_count:
        console.print(
            f"[bold red]{report.error_count} error(s)[/bold red], "
            f"{report.warning_count} warning(s)."
        )
    elif report.warning_count:
        console.print(f"[yellow]{report.warning_count} warning(s)[/yellow], no errors.")
    else:
        console.print("[green]No problems found.[/green]")
    return True


def render_report(
    report: CommandReport,
    *,
    as_json: bool = False,
    verbose: bool = False,
    stream: TextIO | None = None,
) -> None:
    """Print *report* to the terminal.

    Parameters
    ----------
    report : CommandReport
        The report to render.
    as_json : bool, optional
        Print JSON instead of tables.
    verbose : bool, optional
        Include the ``detail`` field of each finding.
    stream : typing.TextIO or None, optional
        Destination; defaults to :data:`sys.stdout`.

    Notes
    -----
    JSON is written to *stdout* alone; all logging goes to *stderr*, so
    ``thesis-cli check-refs --json | jq`` works without filtering.
    """
    target = stream if stream is not None else sys.stdout
    if as_json:
        print(report.to_json(), file=target)
        return
    if stream is None and _render_rich(report, verbose=verbose):
        return
    _render_plain(report, target, verbose=verbose)
