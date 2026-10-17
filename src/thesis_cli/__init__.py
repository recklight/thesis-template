"""thesis-cli: a LaTeX thesis template and the tools that build and check it.

``template/`` is the thesis skeleton itself: the document class, the front
matter, the figure macros and the spine sheet.  This package compiles a
project made from it and audits its references, figures and word count.

Public API
----------
Everything below is importable straight from ``thesis_cli`` and is stable:

Configuration
    :class:`ThesisConfig`, :func:`load_config`, :class:`Engine`,
    :class:`TocStyle`, :class:`WatermarkMode`
Parsing
    :func:`scan_document`, :func:`parse_aux`, :func:`parse_bib_files`,
    :func:`count_text`, :func:`parse_log`
Commands
    :func:`build_document`, :func:`check_references`, :func:`audit_figures`,
    :func:`collect_statistics`, :func:`clean_project`,
    :func:`check_dependencies`, :func:`run_doctor`, :func:`init_project`
Reporting
    :class:`CommandReport`, :class:`Finding`, :func:`render_report`

Examples
--------
>>> from pathlib import Path
>>> from thesis_cli import ThesisConfig, Engine
>>> config = ThesisConfig.default(Path("."))
>>> config.build.engine is Engine.XELATEX
True
"""

from __future__ import annotations

__version__ = "0.1.0"
__author__ = "RL"

from .config import (
    CONFIG_FILENAME,
    BibConfig,
    BuildConfig,
    CleanConfig,
    DepsConfig,
    Engine,
    FiguresConfig,
    StatsConfig,
    TemplateConfig,
    ThesisConfig,
    TocStyle,
    WatermarkMode,
    load_config,
)
from .errors import (
    BuildFailedError,
    CheckFailedError,
    ConfigError,
    ProjectLayoutError,
    ThesisCliError,
    ToolNotFoundError,
    ValidationError,
)
from .latex.artifacts import CleanPlan, execute_clean, plan_clean
from .latex.log_parser import LogReport, MessageKind, parse_log, parse_log_text
from .logging_utils import configure_logging, get_logger
from .parsers.aux_parser import AuxData, parse_aux, parse_aux_text
from .parsers.bib_parser import (
    BibDatabase,
    BibEntry,
    BibProblem,
    parse_bib,
    parse_bib_files,
    parse_bib_text,
)
from .parsers.cjk import TextCounts, count_text, is_cjk
from .parsers.tex_scanner import (
    Citation,
    GraphicRef,
    LabelDef,
    RefUse,
    TexDocument,
    clean_source,
    scan_document,
)
from .report import CommandReport, Finding, ReportTable, Severity, render_report

__all__ = [
    "CONFIG_FILENAME",
    "AuxData",
    "BibConfig",
    "BibDatabase",
    "BibEntry",
    "BibProblem",
    "BuildConfig",
    "BuildFailedError",
    "CheckFailedError",
    "Citation",
    "CleanConfig",
    "CleanPlan",
    "CommandReport",
    "ConfigError",
    "DepsConfig",
    "Engine",
    "FiguresConfig",
    "Finding",
    "GraphicRef",
    "LabelDef",
    "LogReport",
    "MessageKind",
    "ProjectLayoutError",
    "RefUse",
    "ReportTable",
    "Severity",
    "StatsConfig",
    "TemplateConfig",
    "TexDocument",
    "TextCounts",
    "ThesisCliError",
    "ThesisConfig",
    "TocStyle",
    "ToolNotFoundError",
    "ValidationError",
    "WatermarkMode",
    "__version__",
    "audit_figures",
    "build_document",
    "check_dependencies",
    "check_references",
    "clean_project",
    "clean_source",
    "collect_statistics",
    "configure_logging",
    "count_text",
    "execute_clean",
    "get_logger",
    "init_project",
    "is_cjk",
    "load_config",
    "parse_aux",
    "parse_aux_text",
    "parse_bib",
    "parse_bib_files",
    "parse_bib_text",
    "parse_log",
    "parse_log_text",
    "plan_clean",
    "render_report",
    "run_doctor",
    "scan_document",
]


def __getattr__(name: str) -> object:
    """Import command entry points lazily.

    Parameters
    ----------
    name : str
        Attribute being looked up.

    Returns
    -------
    object
        The requested command function.

    Raises
    ------
    AttributeError
        When *name* is not part of the public API.

    Notes
    -----
    A command module is imported the first time one of its names is used,
    so code that only needs the parsers never loads the build, figure and
    clean commands.
    """
    lazy = {
        "audit_figures": ("thesis_cli.commands.figures", "audit_figures"),
        "build_document": ("thesis_cli.commands.build", "build_document"),
        "check_dependencies": ("thesis_cli.commands.deps", "check_dependencies"),
        "check_references": ("thesis_cli.commands.check_refs", "check_references"),
        "clean_project": ("thesis_cli.commands.clean", "clean_project"),
        "collect_statistics": ("thesis_cli.commands.stats", "collect_statistics"),
        "init_project": ("thesis_cli.commands.init_project", "init_project"),
        "run_doctor": ("thesis_cli.commands.doctor", "run_doctor"),
    }
    if name in lazy:
        import importlib

        module_name, attribute = lazy[name]
        return getattr(importlib.import_module(module_name), attribute)
    raise AttributeError(f"module 'thesis_cli' has no attribute {name!r}")
