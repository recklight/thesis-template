"""The ``deps`` command: verify the TeX installation before compiling.

A TeX installation can be missing a package or a font and still produce a
PDF: the engine generates a substitute, exits zero, and the problem only
shows in the log, or on paper.  A CJK thesis is the most exposed, because a missing
sub-font quietly turns into the wrong typeface.

So ``deps`` checks before the build.  Every package the configured engine
needs is looked up with ``kpsewhich``, the programs are located on
``PATH``, and the CJK font is checked with ``fc-list`` for the Unicode
engines or through the Type 1 metric files on the ``CJK`` package route.

On a machine with no TeX at all the report is still complete: every check
is listed as missing or unverifiable, instead of the command stopping at
the first failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import ThesisConfig
from ..latex.engine import (
    BIBTEX_PROGRAM,
    DVIPDFMX_PROGRAM,
    LATEXMK_PROGRAM,
    engine_spec,
    find_executable,
    kpsewhich,
    run_command,
)
from ..logging_utils import get_logger
from ..report import CommandReport, Finding, ReportTable, Severity

__all__ = ["DependencyStatus", "check_dependencies"]

_LOG = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DependencyStatus:
    """Result of one dependency check.

    Attributes
    ----------
    name : str
        What was looked for.
    kind : str
        ``"program"``, ``"package"`` or ``"font"``.
    found : bool
        Whether it is installed.
    location : str
        Where it was found, or a short explanation when it was not.
    required : bool
        Whether a build fails without it.
    """

    name: str
    kind: str
    found: bool
    location: str = ""
    required: bool = True


def _check_font_fontconfig(font_name: str) -> DependencyStatus:
    """Look for a system font via ``fc-list``.

    Parameters
    ----------
    font_name : str
        Family name as XeLaTeX would request it.

    Returns
    -------
    DependencyStatus
        ``found`` is ``False`` when the font is absent *or* when
        ``fc-list`` itself is unavailable; ``location`` distinguishes the
        two, because a missing tool is not a missing font.
    """
    if find_executable("fc-list") is None:
        return DependencyStatus(
            name=font_name,
            kind="font",
            found=False,
            location="fc-list not available; cannot verify (install fontconfig)",
            required=False,
        )
    result = run_command(["fc-list", ":family"], cwd=Path.cwd(), timeout=60)
    if not result.ok:
        return DependencyStatus(
            name=font_name,
            kind="font",
            found=False,
            location="fc-list failed",
            required=False,
        )
    wanted = font_name.casefold()
    for line in result.stdout.splitlines():
        if wanted in line.casefold():
            return DependencyStatus(name=font_name, kind="font", found=True, location="fontconfig")
    return DependencyStatus(
        name=font_name,
        kind="font",
        found=False,
        location="not listed by fc-list",
    )


def check_dependencies(config: ThesisConfig) -> CommandReport:
    """Check every program, package and font the configured build needs.

    Parameters
    ----------
    config : ThesisConfig
        Project configuration; the engine choice determines which CJK
        packages are required.

    Returns
    -------
    CommandReport
        One table of checks, with a finding for each missing requirement.

    Examples
    --------
    >>> from pathlib import Path
    >>> report = check_dependencies(ThesisConfig.default(Path(".")))
    >>> report.command
    'deps'
    """
    spec = engine_spec(config.build.engine)
    statuses: list[DependencyStatus] = []

    # build falls back to a fixed pass sequence without latexmk, so its
    # absence is a warning, not a missing requirement.
    programs: list[tuple[str, bool]] = [
        (spec.program, True),
        (BIBTEX_PROGRAM, True),
        (LATEXMK_PROGRAM, False),
    ]
    if spec.needs_dvipdfmx:
        programs.append((DVIPDFMX_PROGRAM, True))
    programs.append(("kpsewhich", False))

    for name, required in programs:
        located = find_executable(name)
        statuses.append(
            DependencyStatus(
                name=name,
                kind="program",
                found=located is not None,
                location=str(located) if located else "not on PATH",
                required=required,
            )
        )

    kpsewhich_available = find_executable("kpsewhich") is not None
    for package in config.required_packages:
        if not kpsewhich_available:
            statuses.append(
                DependencyStatus(
                    name=f"{package}.sty",
                    kind="package",
                    found=False,
                    location="kpsewhich not available; cannot verify",
                    required=True,
                )
            )
            continue
        located = kpsewhich(f"{package}.sty", cwd=config.root)
        statuses.append(
            DependencyStatus(
                name=f"{package}.sty",
                kind="package",
                found=located is not None,
                location=str(located) if located else "not installed",
                required=True,
            )
        )

    if kpsewhich_available:
        class_name = "thesis_report.cls"
        located = kpsewhich(class_name, cwd=config.root)
        local = config.root / class_name
        statuses.append(
            DependencyStatus(
                name=class_name,
                kind="package",
                found=located is not None or local.is_file(),
                location=str(located or local) if (located or local.is_file()) else "not found",
                required=True,
            )
        )

    if config.build.engine.is_unicode_engine:
        statuses.append(_check_font_fontconfig(config.deps.cjk_font))
    else:
        for font_file in config.deps.legacy_font_files:
            if not kpsewhich_available:
                statuses.append(
                    DependencyStatus(
                        name=font_file,
                        kind="font",
                        found=False,
                        location="kpsewhich not available; cannot verify",
                        required=True,
                    )
                )
                continue
            located = kpsewhich(font_file, cwd=config.root)
            statuses.append(
                DependencyStatus(
                    name=font_file,
                    kind="font",
                    found=located is not None,
                    location=str(located) if located else "not installed",
                    required=True,
                )
            )

    findings: list[Finding] = []
    for status in statuses:
        if status.found:
            continue
        unverifiable = "cannot verify" in status.location
        findings.append(
            Finding(
                severity=(
                    Severity.WARNING if unverifiable or not status.required else Severity.ERROR
                ),
                kind=f"missing-{status.kind}",
                location=status.name,
                message=(
                    f"{status.kind.capitalize()} '{status.name}' is unavailable: {status.location}."
                ),
                detail=(
                    "Install a TeX distribution and re-run 'thesis-cli deps'."
                    if status.kind != "font"
                    else "A missing CJK font is what makes a build degrade silently."
                ),
            )
        )

    table = ReportTable(
        title=f"Dependencies for engine '{config.build.engine.value}'",
        columns=("name", "kind", "required", "status", "location"),
        rows=tuple(
            (
                status.name,
                status.kind,
                "yes" if status.required else "no",
                "ok" if status.found else "MISSING",
                status.location,
            )
            for status in statuses
        ),
        notes=(f"{len(config.required_packages)} LaTeX package(s) are required by this engine.",),
    )

    summary = {
        "engine": config.build.engine.value,
        "checked": len(statuses),
        "missing": sum(1 for status in statuses if not status.found),
        "packages_required": len(config.required_packages),
        "kpsewhich": kpsewhich_available,
    }
    _LOG.info("Checked %d dependencies; %d missing", len(statuses), summary["missing"])
    return CommandReport(
        command="deps",
        findings=tuple(findings),
        tables=(table,),
        summary=summary,
    )
