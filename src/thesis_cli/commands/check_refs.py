r"""The ``check-refs`` command: a four-way cross-reference audit.

LaTeX reports an undefined reference as a warning buried in the log and then
exits zero, so a thesis can be submitted with ``??`` where a figure number
should be.  About the other direction the toolchain says even less: a
bibliography entry that no chapter cites is not reported at all, and a key
defined differently in two ``.bib`` files leaves one ``Repeated entry`` line
in the ``.blg``.

This command compares the four sets that must agree:

==============================  =====================================  ========
Comparison                      What goes wrong                        Severity
==============================  =====================================  ========
``\cite`` key not in ``.bib``   the citation prints as ``[?]``         error
``.bib`` key never cited        the entry is left out of the list      warning
``\ref`` target not defined     the cross-reference prints as ``??``   error
``\label`` defined twice        the reference points at one of them    error
==============================  =====================================  ========

When an ``.aux`` file from a previous run is present it is read as well, so
a stale build (keys cited in the source that the last compile never saw) is
reported too.  The database and style named in the source, and the
``\bibdata``/``\bibstyle`` of the last build, are compared with
``bib.files`` and ``bib.style`` in ``thesis.toml``.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePath, PurePosixPath
from typing import Final

from ..config import ThesisConfig
from ..errors import ProjectLayoutError
from ..logging_utils import get_logger
from ..parsers.aux_parser import parse_aux
from ..parsers.bib_parser import parse_bib_files
from ..parsers.tex_scanner import TexDocument, scan_document
from ..report import CommandReport, Finding, ReportTable, Severity

__all__ = ["check_references"]

_LOG = get_logger(__name__)


_STYLE_RE: Final[re.Pattern[str]] = re.compile(r"\\bibliographystyle\s*\{([^{}]*)\}")


def _database_name(path: str | PurePath) -> str:
    r"""Spell a bibliography database the way ``\bibliography`` does.

    ``\bibliography`` and the ``\bibdata`` line LaTeX copies from it name the
    file relative to the directory TeX runs in, usually without ``.bib``.
    The whole relative path is compared, not only the file name, so
    ``bib/references`` and ``other/references`` stay apart.

    Examples
    --------
    >>> _database_name("./bib/references.bib"), _database_name("bib/references")
    ('bib/references', 'bib/references')
    """
    name = PurePosixPath(str(path).replace("\\", "/"))
    if name.suffix.lower() == ".bib":
        name = name.with_suffix("")
    return name.as_posix()


def _bibliography_styles(document: TexDocument) -> list[str]:
    r"""Return every ``\bibliographystyle`` in the document, in order, once each."""
    styles = (
        match.group(1).strip() for item in document.files for match in _STYLE_RE.finditer(item.code)
    )
    return list(dict.fromkeys(style for style in styles if style))


def _aux_path(config: ThesisConfig) -> Path:
    """Return the most plausible location of the main document's ``.aux``."""
    stem = Path(config.main).stem
    in_output = config.output_dir / f"{stem}.aux"
    if in_output.is_file():
        return in_output
    return config.main_path.with_suffix(".aux")


def check_references(
    config: ThesisConfig,
    *,
    document: TexDocument | None = None,
    use_aux: bool = True,
) -> CommandReport:
    """Audit citations, bibliography entries, references and labels.

    Parameters
    ----------
    config : ThesisConfig
        Project configuration.
    document : TexDocument or None, optional
        A previously scanned document, to avoid re-reading the sources.
    use_aux : bool, optional
        Also read the ``.aux`` file from the last build, when present.

    Returns
    -------
    CommandReport
        Findings plus a per-database summary table.

    Raises
    ------
    ProjectLayoutError
        When the main document does not exist.
    """
    main = config.main_path
    if not main.is_file():
        raise ProjectLayoutError(
            f"Main document not found: {main}",
            hint="Check the 'main' key in thesis.toml.",
        )

    if document is None:
        document = scan_document(
            main,
            config.root,
            macros=config.figure_macros,
            label_macros=config.label_macros,
        )

    database = parse_bib_files(config.bib_paths)
    findings: list[Finding] = []

    # -- 1. cited keys that no database defines --------------------------
    bib_keys = database.keys
    first_use: dict[str, str] = {}
    for citation in document.citations:
        first_use.setdefault(citation.key, citation.location.render(config.root))
    for key in sorted(first_use):
        if key not in bib_keys:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    kind="undefined-citation",
                    location=first_use[key],
                    message=f"\\cite{{{key}}} has no matching entry in any bibliography file.",
                    detail="LaTeX will typeset this citation as [?].",
                )
            )

    # -- 2. database entries nothing cites -------------------------------
    cited = document.cited_keys()
    if document.cite_all:
        _LOG.info("\\nocite{*} is present; every entry counts as cited.")
    else:
        for entry in sorted(database.entries, key=lambda item: item.key):
            if entry.key in cited:
                continue
            location = f"{entry.path.name}:{entry.line}" if entry.path is not None else "-"
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    kind="unused-bib-entry",
                    location=location,
                    message=f"Bibliography entry '{entry.key}' is never cited.",
                    detail="Unused entries do not appear in the reference list; "
                    "remove them or cite them.",
                )
            )

    # -- 3. references with no label -------------------------------------
    defined = document.label_names()
    first_ref: dict[str, tuple[str, str]] = {}
    for reference in document.references:
        first_ref.setdefault(
            reference.name, (reference.location.render(config.root), reference.command)
        )
    for name in sorted(first_ref):
        if name not in defined:
            location, command = first_ref[name]
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    kind="undefined-reference",
                    location=location,
                    message=f"\\{command}{{{name}}} points at a label that is never defined.",
                    detail="LaTeX will typeset this cross-reference as ??.",
                )
            )

    # -- 4. labels defined more than once --------------------------------
    for name, definitions in sorted(document.duplicate_labels().items()):
        places = ", ".join(item.location.render(config.root) for item in definitions)
        findings.append(
            Finding(
                severity=Severity.ERROR,
                kind="duplicate-label",
                location=definitions[0].location.render(config.root),
                message=f"Label '{name}' is defined {len(definitions)} times.",
                detail=f"Definitions at: {places}",
            )
        )

    # -- bibliography data quality ---------------------------------------
    for problem in database.problems:
        findings.append(
            Finding(
                severity=Severity.ERROR if problem.severity == "error" else Severity.WARNING,
                kind=problem.kind.value,
                location=problem.render_location(config.root),
                message=(f"[{problem.key}] {problem.message}" if problem.key else problem.message),
            )
        )

    # -- unresolvable \input targets -------------------------------------
    for missing in document.missing_inputs:
        findings.append(
            Finding(
                severity=Severity.ERROR,
                kind="missing-input",
                location=missing.location.render(config.root),
                message=f"\\{missing.command}{{{missing.target}}} cannot be resolved.",
            )
        )

    # -- the \bibliography command versus thesis.toml --------------------
    # bib_paths are resolved, so the root has to be as well: a relative root
    # or one reached through a symlink would not be a prefix of them.
    root = config.root.resolve()
    configured = {_database_name(path.relative_to(root)) for path in config.bib_paths}
    for declared in document.bib_commands:
        if _database_name(declared) not in configured:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    kind="bib-mismatch",
                    location=str(config.source_file or config.root),
                    message=(
                        f"The source says \\bibliography{{{declared}}} but thesis.toml lists "
                        f"{sorted(configured)}; the audit reads the files thesis.toml lists, "
                        "which may not be the ones the build finds."
                    ),
                )
            )
    for declared_style in _bibliography_styles(document):
        if declared_style != config.bib.style:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    kind="bib-mismatch",
                    location=str(config.source_file or config.root),
                    message=(
                        f"The source says \\bibliographystyle{{{declared_style}}} but "
                        f"thesis.toml has bib.style = {config.bib.style!r}."
                    ),
                )
            )

    # -- stale .aux -------------------------------------------------------
    aux_summary = "not read"
    if use_aux:
        aux_file = _aux_path(config)
        aux = parse_aux(aux_file)
        if aux.labels or aux.citations or aux.bibcite:
            aux_summary = str(aux_file)
            stale_keys = sorted(cited - aux.citation_keys)
            if stale_keys:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        kind="stale-aux",
                        location=str(aux_file),
                        message=(
                            f"{len(stale_keys)} citation(s) in the source are absent from the "
                            "last build's .aux; the PDF on disk is out of date."
                        ),
                        detail="Missing from .aux: " + ", ".join(stale_keys[:10]),
                    )
                )
            for declared in aux.bibdata:
                if _database_name(declared) not in configured:
                    findings.append(
                        Finding(
                            severity=Severity.WARNING,
                            kind="bib-mismatch",
                            location=str(aux_file),
                            message=(
                                f"The last build used \\bibdata{{{declared}}}, which is not in "
                                "thesis.toml's bib.files."
                            ),
                        )
                    )
            if aux.bibstyle and aux.bibstyle != config.bib.style:
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        kind="bib-mismatch",
                        location=str(aux_file),
                        message=(
                            f"The last build used \\bibstyle{{{aux.bibstyle}}}, but thesis.toml "
                            f"has bib.style = {config.bib.style!r}."
                        ),
                    )
                )

    tables = (
        ReportTable(
            title="Sources",
            columns=("kind", "count"),
            rows=(
                ("tex files scanned", str(len(document.files))),
                ("distinct \\cite keys", str(len(cited))),
                ("bibliography entries", str(len(database.entries))),
                ("labels defined", str(len(defined))),
                ("cross-references used", str(len(first_ref))),
            ),
        ),
    )

    summary = {
        "main": str(main),
        "bib_files": [str(path) for path in config.bib_paths],
        "aux": aux_summary,
        "cited_keys": len(cited),
        "bib_entries": len(database.entries),
        "labels": len(defined),
        "references": len(first_ref),
    }
    return CommandReport(
        command="check-refs",
        findings=tuple(findings),
        tables=tables,
        summary=summary,
    )
