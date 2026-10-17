"""The ``clean`` command: remove build artefacts and nothing else.

A directory that has been compiled in for a while holds the sources mixed
with every artefact a TeX run produces, plus editor backups, thumbnail
databases and often the leftovers of an unrelated document built in the same
folder.  Deleting by extension is the obvious way to tidy that, and it is
also how a chapter gets lost.

The rules that make automatic deletion safe live in
:mod:`thesis_cli.latex.artifacts`; this module turns a plan into a report
and, unless asked otherwise, into deletions.
"""

from __future__ import annotations

from ..config import ThesisConfig
from ..latex.artifacts import CleanPlan, execute_clean, plan_clean
from ..logging_utils import get_logger
from ..report import CommandReport, Finding, ReportTable, Severity

__all__ = ["clean_project"]

_LOG = get_logger(__name__)


def clean_project(
    config: ThesisConfig,
    *,
    dry_run: bool = False,
    include_outputs: bool | None = None,
    show_skipped: bool = False,
) -> CommandReport:
    """Remove build artefacts from the project.

    Parameters
    ----------
    config : ThesisConfig
        Project configuration supplying the extension and pattern lists.
    dry_run : bool, optional
        Report the plan without deleting anything.
    include_outputs : bool or None, optional
        Also remove the finished PDF.  Defaults to ``clean.remove_outputs``.
    show_skipped : bool, optional
        List the files that matched an artefact extension but were spared
        for lack of a matching source file.

    Returns
    -------
    CommandReport
        A table of what was (or would be) removed, plus a finding for every
        deletion that failed.

    Notes
    -----
    An artefact is only removed while the source that produced it is
    present.  An ``orphan.aux`` with no ``orphan.tex`` is left alone: nothing
    shows that a TeX run in this project wrote it, and it may belong to
    another document built in the same folder.
    """
    plan: CleanPlan = plan_clean(config, include_outputs=include_outputs)
    removed, failures = execute_clean(plan, dry_run=dry_run)

    rows = tuple(
        (
            candidate.relative_to(plan.root),
            candidate.reason.value,
            f"{candidate.size / 1024:.1f}",
            (
                candidate.source.name
                if candidate.source is not None
                else ("-" if candidate.reason.value != "engine-artifact" else "?")
            ),
        )
        for candidate in plan.candidates
    )

    tables = [
        ReportTable(
            title="Files to remove" if dry_run else "Removed files",
            columns=("file", "reason", "KiB", "produced by"),
            rows=rows,
            notes=(
                ("Nothing was deleted (--dry-run).",)
                if dry_run
                else (f"Freed {plan.total_bytes / 1024:.1f} KiB.",)
            ),
        )
    ]

    if show_skipped and plan.skipped:
        tables.append(
            ReportTable(
                title="Spared",
                columns=("file", "reason"),
                rows=tuple(
                    (
                        path.relative_to(plan.root).as_posix()
                        if path.is_relative_to(plan.root)
                        else str(path),
                        reason,
                    )
                    for path, reason in plan.skipped
                ),
            )
        )

    findings = [
        Finding(
            severity=Severity.ERROR,
            kind="delete-failed",
            location=str(path),
            message=message,
        )
        for path, message in failures
    ]

    if not plan.candidates:
        findings.append(
            Finding(
                severity=Severity.INFO,
                kind="already-clean",
                location=str(plan.root),
                message="No build artefacts were found.",
            )
        )

    summary = {
        "root": str(plan.root),
        "dry_run": dry_run,
        "candidates": len(plan.candidates),
        "removed": len(removed),
        "failed": len(failures),
        "spared": len(plan.skipped),
        "bytes": plan.total_bytes,
        "by_reason": plan.by_reason(),
    }
    return CommandReport(
        command="clean",
        findings=tuple(findings),
        tables=tuple(tables),
        summary=summary,
    )
