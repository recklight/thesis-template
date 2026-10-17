r"""The ``doctor`` command: can this machine build the thesis?

The only reliable way to know that a machine can typeset Chinese is to
typeset some.  The smoke test writes a minimal CJK document from a
constant, builds it in a temporary directory so nothing is left behind, and
judges the result by whether the engine wrote its output file and what the
log says.

Around that smoke test it checks the things that make a project fail on
somebody else's machine: the Python environment, the configuration file, the
presence of every template file the main document pulls in, and the
toolchain (delegated to :mod:`thesis_cli.commands.deps`).  Without a
``thesis.toml`` there is no project to look at, so the layout check is left
out and only the machine is checked.
"""

from __future__ import annotations

import platform
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..config import CONFIG_FILENAME, ThesisConfig, find_config_file
from ..latex.engine import (
    build_environment,
    engine_spec,
    find_executable,
    manual_sequence,
    run_command,
)
from ..latex.log_parser import parse_log
from ..logging_utils import get_logger
from ..report import CommandReport, Finding, ReportTable, Severity
from .deps import check_dependencies

__all__ = [
    "MINIMAL_DOCUMENT",
    "SmokeResult",
    "run_doctor",
    "run_smoke_test",
    "write_smoke_document",
]

_LOG = get_logger(__name__)

MINIMAL_DOCUMENT: Final[
    str
] = r"""% Minimal CJK document used by 'thesis-cli doctor' as a smoke test.
% It is deliberately tiny: if this does not compile, nothing else will.
\documentclass[12pt,a4paper]{article}
\usepackage{iftex}
\ifPDFTeX
  \usepackage[utf8]{inputenc}
  \usepackage{CJKutf8}
  \newcommand{\SmokeBegin}{\begin{CJK}{UTF8}{bsmi}}
  \newcommand{\SmokeEnd}{\end{CJK}}
\else
  \usepackage{xeCJK}
  \newcommand{\SmokeBegin}{}
  \newcommand{\SmokeEnd}{}
\fi
\usepackage{amsmath}
\begin{document}
\SmokeBegin
中文編排測試：一二三四五六七八九十。
This line mixes English with 中文 in one paragraph.
\begin{equation}
  E = mc^{2}
\end{equation}
\SmokeEnd
\end{document}
"""
"""The document compiled by the smoke test, kept in one place so the test
fixture on disk and the code cannot drift apart."""

_EXPECTED_TEMPLATE_FILES: Final[tuple[str, ...]] = (
    "thesis.tex",
    "thesis_report.cls",
    "config/names.tex",
    "config/definitions.tex",
    "config/chapter_numbering.tex",
    "config/watermark.tex",
    "config/headerfooter.tex",
    "config/cjk.tex",
    "macros/figure_macros.tex",
    "structure/frontpages.tex",
    "structure/backpages.tex",
    "structure/chapters.tex",
    "structure/coverbridge.tex",
)


@dataclass(frozen=True, slots=True)
class SmokeResult:
    """Outcome of the minimal-document compilation."""

    attempted: bool
    succeeded: bool
    message: str
    pdf: Path | None = None
    errors: tuple[str, ...] = ()


def write_smoke_document(directory: Path, *, name: str = "minimal.tex") -> Path:
    """Write the minimal CJK document into *directory*.

    Parameters
    ----------
    directory : pathlib.Path
        Destination directory; created when absent.
    name : str, optional
        File name to write.

    Returns
    -------
    pathlib.Path
        The file that was written.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_text(MINIMAL_DOCUMENT, encoding="utf-8")
    return target


def run_smoke_test(config: ThesisConfig, *, timeout: float = 300.0) -> SmokeResult:
    """Compile the minimal document in a temporary directory.

    Parameters
    ----------
    config : ThesisConfig
        Supplies the engine to test.
    timeout : float, optional
        Seconds allowed for each pass.

    Returns
    -------
    SmokeResult
        Whether the compilation was attempted, whether it worked, and the
        first few errors from the log when it did not.

    Notes
    -----
    A single pass is enough: the document has no cross-references and no
    bibliography, so the only thing it tests is whether the engine and its
    CJK support are installed and working.
    """
    spec = engine_spec(config.build.engine)
    if find_executable(spec.program) is None:
        return SmokeResult(
            attempted=False,
            succeeded=False,
            message=f"Engine '{spec.program}' is not installed; skipping the smoke test.",
        )

    with tempfile.TemporaryDirectory(prefix="thesis-cli-doctor-") as raw_temp:
        workspace = Path(raw_temp)
        source = write_smoke_document(workspace)
        commands = manual_sequence(config, source, output_dir=workspace, passes=2)
        engine_call = commands[0]
        result = run_command(
            engine_call, cwd=workspace, timeout=timeout, env=build_environment(config)
        )
        log = parse_log(workspace / "minimal.log")
        produced = workspace / f"minimal{spec.intermediate_suffix}"
        errors = tuple(item.message for item in log.errors[:5])

        if not produced.is_file():
            return SmokeResult(
                attempted=True,
                succeeded=False,
                message=(
                    f"{spec.program} produced no {spec.intermediate_suffix} file "
                    f"(exit status {result.returncode})."
                ),
                errors=errors,
            )
        if errors:
            return SmokeResult(
                attempted=True,
                succeeded=False,
                message=f"{spec.program} reported {len(log.errors)} error(s).",
                errors=errors,
            )
        return SmokeResult(
            attempted=True,
            succeeded=True,
            message=f"{spec.program} compiled the minimal CJK document successfully.",
        )


def _python_environment_rows() -> tuple[tuple[str, ...], ...]:
    """Collect interpreter and dependency versions for the report."""
    rows: list[tuple[str, ...]] = [
        ("python", sys.version.split()[0], sys.executable),
        ("platform", platform.platform(), ""),
    ]
    for module_name in ("numpy", "typer", "rich", "tomli", "pytest"):
        try:
            import importlib.metadata as metadata

            rows.append((module_name, metadata.version(module_name), ""))
        except Exception:  # a missing optional package is data, not an error
            rows.append((module_name, "not installed", ""))
    return tuple(rows)


def run_doctor(
    config: ThesisConfig,
    *,
    smoke: bool = True,
    template_dir: Path | None = None,
) -> CommandReport:
    """Diagnose the environment, the project and the toolchain.

    Parameters
    ----------
    config : ThesisConfig
        Project configuration.
    smoke : bool, optional
        Compile the minimal CJK document as a final check.
    template_dir : pathlib.Path or None, optional
        Directory whose completeness should be verified; defaults to the
        project root, which for a project created by ``init`` holds the
        template files directly.  With no ``thesis.toml`` and no
        *template_dir*, the layout is not checked.

    Returns
    -------
    CommandReport
        Findings from every check, with tables for the environment, the
        project layout (when one was checked) and the toolchain.
    """
    findings: list[Finding] = []
    tables: list[ReportTable] = [
        ReportTable(
            title="Python environment",
            columns=("component", "version", "location"),
            rows=_python_environment_rows(),
        )
    ]

    config_file = config.source_file or find_config_file(config.root)
    if config_file is None:
        findings.append(
            Finding(
                severity=Severity.WARNING,
                kind="missing-config",
                location=str(config.root),
                message=f"No {CONFIG_FILENAME} found; built-in defaults are in use.",
                detail="Run 'thesis-cli init .' to create one.",
            )
        )

    # Run from a clone or a home folder, the layout check would report every
    # template file and the main document as missing, and a machine with a
    # complete TeX installation would fail.
    if config_file is not None or template_dir is not None:
        base = Path(template_dir) if template_dir is not None else config.root
        layout_rows: list[tuple[str, ...]] = []
        for relative in _EXPECTED_TEMPLATE_FILES:
            candidate = base / relative
            present = candidate.is_file()
            layout_rows.append((relative, "ok" if present else "MISSING"))
            if not present:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        kind="missing-template-file",
                        location=relative,
                        message="Expected template file is absent from the project.",
                    )
                )
        if config_file is not None:
            main = config.root / config.main
            layout_rows.append((config.main, "ok" if main.is_file() else "MISSING"))
            if not main.is_file():
                findings.append(
                    Finding(
                        severity=Severity.ERROR,
                        kind="missing-main",
                        location=str(main),
                        message="The main document configured in thesis.toml does not exist.",
                    )
                )
        tables.append(
            ReportTable(
                title="Project layout",
                columns=("file", "status"),
                rows=tuple(layout_rows),
            )
        )

    dependency_report = check_dependencies(config)
    tables.extend(dependency_report.tables)
    findings.extend(dependency_report.findings)

    smoke_result = SmokeResult(attempted=False, succeeded=False, message="skipped")
    if smoke:
        smoke_result = run_smoke_test(config)
        severity = (
            Severity.OK
            if smoke_result.succeeded
            else (Severity.WARNING if not smoke_result.attempted else Severity.ERROR)
        )
        if severity is not Severity.OK:
            findings.append(
                Finding(
                    severity=severity,
                    kind="smoke-test",
                    location="minimal.tex",
                    message=smoke_result.message,
                    detail="; ".join(smoke_result.errors),
                )
            )
        tables.append(
            ReportTable(
                title="Smoke test",
                columns=("check", "result"),
                rows=(
                    ("minimal CJK document", "pass" if smoke_result.succeeded else "not passed"),
                    ("detail", smoke_result.message),
                ),
            )
        )

    summary = {
        "config_file": str(config_file) if config_file else "none",
        "engine": config.build.engine.value,
        "latexmk": bool(find_executable("latexmk")),
        "smoke_attempted": smoke_result.attempted,
        "smoke_passed": smoke_result.succeeded,
        "disk_free_mb": round(shutil.disk_usage(config.root).free / 1_048_576, 1)
        if config.root.exists()
        else 0.0,
    }
    return CommandReport(
        command="doctor",
        findings=tuple(findings),
        tables=tuple(tables),
        summary=summary,
    )
