r"""The ``stats`` command: CJK-aware word counts per chapter.

``wc -w`` is useless on a Chinese thesis (no spaces, so a whole paragraph
is one word) and ``wc -m`` is useless on the English abstract (it counts
letters, not words).  This command reports both figures side by side, after
removing everything that is markup rather than prose: comments, math,
listings, command and environment names, and the arguments that hold an
identifier or a setting instead of text (labels, citation keys, file names,
widths, the figure macros' file and label arguments).

Counting starts from the *prose* view of
:func:`thesis_cli.parsers.tex_scanner.clean_source`, where comments,
verbatim blocks and math are blanked in place with offsets preserved, so a
source file written as one long unbroken line, as abstracts often are,
still counts correctly.

Which files are counted
-----------------------
By default only the files that actually carry prose: everything under the
configured ``chapters`` and ``content`` directories, plus any other file
that opens a ``\chapter`` or ``\section``.  Preamble and configuration
files are summarised in a single row instead of being listed, because the
package options in ``config/cjk.tex`` are not part of anybody's word count.
``all_files=True`` lists every file the document pulls in.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..config import ThesisConfig
from ..errors import ProjectLayoutError
from ..logging_utils import get_logger
from ..parsers.cjk import TextCounts, count_text
from ..parsers.tex_scanner import (
    FIGURE_ENVIRONMENTS,
    TABLE_ENVIRONMENTS,
    TexDocument,
    _argument_spans,
    _iter_commands,
    environment_counts,
    read_group,
    scan_document,
)
from ..report import CommandReport, Finding, ReportTable, Severity

__all__ = ["FileStatistics", "collect_statistics"]

_LOG = get_logger(__name__)

# Group 2 is the star: \chapter* headings (abstract, contents, vita) make a
# file count as prose but are not chapters.
_SECTION_RE: Final[re.Pattern[str]] = re.compile(
    r"\\(chapter|section|subsection)(?![A-Za-z])\s*(\*?)\s*[\[{]"
)
# The template macros that make a figure of their own.  The \mpfig panels
# sit inside a figure environment, which is counted already.
_FIGURE_MACROS: Final[tuple[str, ...]] = ("fig", "figt", "figtr", "hfig", "hfigt", "hfigtr")
# Environments whose first mandatory arguments are a column spec or a width.
_SPEC_ENVIRONMENTS: Final[dict[str, int]] = {
    "tabular": 1,
    "tabular*": 2,
    "tabularx": 2,
    "array": 1,
    "longtable": 1,
    "minipage": 1,
    "wrapfigure": 2,
    "multicols": 1,
}
_BEGIN_RE: Final[re.Pattern[str]] = re.compile(r"\\begin\s*\{([^{}]*)\}")
_END_RE: Final[re.Pattern[str]] = re.compile(r"\\end\s*\{[^{}]*\}")
_LINE_BREAK_RE: Final[re.Pattern[str]] = re.compile(r"\\\\\*?(?:\s*\[[^\]]*\])?")
_CONTROL_WORD_RE: Final[re.Pattern[str]] = re.compile(r"\\[A-Za-z@]+\*?")
_LENGTH = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:pt|bp|cm|mm|in|em|ex|sp|pc|dd|cc|mu)?"
# A group holding nothing but numbers or lengths: a width, a stretch factor,
# the trim box of \figtr.  Settings, not prose.
_NUMERIC_GROUP_RE: Final[re.Pattern[str]] = re.compile(
    rf"\{{(?:\s*{_LENGTH})+\s*\}}|\[(?:\s*{_LENGTH})+\s*\]"
)
_BRACKETS: Final[dict[int, str]] = {ord(char): " " for char in "{}[]"}
_STRIP_COMMANDS: Final[re.Pattern[str]] = re.compile(
    r"\\(?:label|ref|eqref|pageref|autoref|nameref|cref|Cref|cite[a-z]*|nocite|input|include"
    r"|includegraphics|bibliography|bibliographystyle|usepackage|documentclass|newcommand"
    r"|renewcommand|providecommand|newenvironment|renewenvironment|def|setlength|addtolength"
    r"|settowidth|newlength|hspace|vspace|index|verb|graphicspath|addcontentsline"
    r"|addtocontents|pagestyle|thispagestyle|pagenumbering|setcounter|addtocounter"
    r"|DeclareGraphicsExtensions|hypersetup|setCJKmainfont|geometry)"
    # The name must end here.  Without this guard the alternation above
    # matches a prefix of a longer command: \include would consume the
    # first eight characters of \includegraphics, leaving the word
    # "graphics" in the prose, and \ref would do the same to \reflectbox,
    # whose argument *is* prose and must stay in the count.
    r"(?![A-Za-z])"
    r"\s*\*?\s*(?:\[[^\]]*\])*\s*(?:\{[^{}]*\})*"
)


@dataclass(frozen=True, slots=True)
class FileStatistics:
    r"""Counts for one source file.

    Attributes
    ----------
    path : pathlib.Path
        The file.
    counts : TextCounts
        Character and word tallies.
    chapters, sections : int
        Structural divisions opened in this file.
    figures, tables : int
        Figure and table environments opened in this file, plus the figures
        made by ``\fig``, ``\figt``, ``\figtr`` and the ``\hfig`` family.
    equations : int
        Display-math blocks: ``equation``, ``align`` and the other math
        environments, starred or not, plus ``$$...$$`` and ``\[...\]``.  A
        multi-line ``align`` counts once.
    citations : int
        Number of ``\cite`` keys used.
    is_prose : bool
        Whether the file is part of the thesis text rather than preamble.
    """

    path: Path
    counts: TextCounts
    chapters: int = 0
    sections: int = 0
    figures: int = 0
    tables: int = 0
    equations: int = 0
    citations: int = 0
    is_prose: bool = True


def _strip_command_arguments(text: str) -> str:
    r"""Remove commands whose arguments are identifiers rather than prose.

    Parameters
    ----------
    text : str
        Prose view of a source file.

    Returns
    -------
    str
        Same text with label names, citation keys and file paths removed, so
        that ``\label{sec:radio-map}`` does not count as an English word.

    Examples
    --------
    >>> _strip_command_arguments(r"text \label{sec:a} more \cite{k1,k2} end").split()
    ['text', 'more', 'end']
    """
    return _STRIP_COMMANDS.sub(" ", text)


def _blank_spans(buffer: list[str], spans: list[tuple[int, int]]) -> None:
    for start, end in spans:
        buffer[start:end] = " " * (end - start)


def _blank_macro_settings(
    text: str, figure_macros: Mapping[str, int], label_macros: Mapping[str, int]
) -> str:
    r"""Blank the figure-macro arguments that are not prose.

    Everything up to and including the file name (the width, and the
    alignment of ``\mpfiga``) and the label go; the caption, and the extra
    text of the ``t`` variants, stay in the count.
    """
    buffer = list(text)
    for name, _, end_of_name in _iter_commands(text, set(figure_macros) | set(label_macros)):
        file_position = figure_macros.get(name, 0)
        label_position = label_macros.get(name, 0)
        located = _argument_spans(text, end_of_name, max(file_position, label_position))
        if located is None:
            continue
        spans, _ = located
        _blank_spans(
            buffer,
            [
                span
                for position, span in enumerate(spans, 1)
                if position <= file_position or position == label_position
            ],
        )
    return "".join(buffer)


def _blank_environment_markers(text: str) -> str:
    r"""Blank ``\begin``/``\end`` with their options, and tabular-like specs."""
    buffer = list(text)
    for match in _BEGIN_RE.finditer(text):
        remaining = _SPEC_ENVIRONMENTS.get(match.group(1).strip(), 0)
        cursor = match.end()
        while True:
            probe = cursor
            while probe < len(text) and text[probe] in " \t":
                probe += 1
            if probe < len(text) and text[probe] == "[":
                closing = text.find("]", probe)
                if closing == -1:
                    break
                cursor = closing + 1
            elif probe < len(text) and text[probe] == "{" and remaining:
                group = read_group(text, probe)
                if group is None:
                    break
                cursor = group[1]
                remaining -= 1
            else:
                break
        _blank_spans(buffer, [(match.start(), cursor)])
    return _END_RE.sub(lambda found: " " * len(found.group(0)), "".join(buffer))


def _prose_text(
    prose: str, figure_macros: Mapping[str, int], label_macros: Mapping[str, int]
) -> str:
    r"""Reduce a prose view to the words a reader sees.

    Parameters
    ----------
    prose : str
        :attr:`TexFile.prose`: comments, verbatim and math already blanked.
    figure_macros, label_macros : mapping of str to int
        Argument positions of the file name and the label, from
        ``thesis.toml``.

    Returns
    -------
    str
        The text with every control word, environment name, figure-macro
        setting, numeric argument and brace removed.  What is left is
        handed to :func:`~thesis_cli.parsers.cjk.count_text`.

    Examples
    --------
    >>> _prose_text(r"\chapter{緒論}\begin{itemize}\item 第一點\end{itemize}", {}, {}).split()
    ['緒論', '第一點']
    >>> _prose_text(r"\fig{0.6}{figures/plot}{The caption.}{fig:plot}", {"fig": 2}, {"fig": 4}).split()
    ['The', 'caption.']
    """
    text = _blank_macro_settings(prose, figure_macros, label_macros)
    text = _strip_command_arguments(text)
    text = _blank_environment_markers(text)
    text = _LINE_BREAK_RE.sub(" ", text)
    text = _CONTROL_WORD_RE.sub(" ", text)
    text = _NUMERIC_GROUP_RE.sub(" ", text)
    return text.translate(_BRACKETS)


def _count_file(
    path: Path,
    prose: str,
    code: str,
    display_math: int,
    citations: int,
    *,
    prose_roots: tuple[Path, ...],
    figure_macros: Mapping[str, int],
    label_macros: Mapping[str, int],
) -> FileStatistics:
    """Compute the statistics of a single file."""
    counts = count_text(_prose_text(prose, figure_macros, label_macros))
    environments = environment_counts(code)
    structure = {"chapter": 0, "section": 0, "subsection": 0}
    has_structure = False
    for match in _SECTION_RE.finditer(code):
        has_structure = has_structure or match.group(1) != "subsection"
        if not match.group(2):
            structure[match.group(1)] += 1
    figures = sum(environments.get(name, 0) for name in FIGURE_ENVIRONMENTS)
    figures += sum(1 for _ in _iter_commands(code, _FIGURE_MACROS))
    tables = sum(environments.get(name, 0) for name in TABLE_ENVIRONMENTS)
    in_prose_dir = any(path.is_relative_to(root) for root in prose_roots)
    return FileStatistics(
        path=path,
        counts=counts,
        chapters=structure["chapter"],
        sections=structure["section"] + structure["subsection"],
        figures=figures,
        tables=tables,
        equations=display_math,
        citations=citations,
        is_prose=in_prose_dir or has_structure,
    )


def collect_statistics(
    config: ThesisConfig,
    *,
    document: TexDocument | None = None,
    only_chapters: bool = False,
    all_files: bool = False,
) -> CommandReport:
    """Count the prose of every file in the document.

    Parameters
    ----------
    config : ThesisConfig
        Project configuration.
    document : TexDocument or None, optional
        Previously scanned document.
    only_chapters : bool, optional
        Restrict the report to files under ``stats.chapter_dir``.
    all_files : bool, optional
        List preamble and configuration files individually instead of
        collapsing them into one row.

    Returns
    -------
    CommandReport
        A per-file table with a totals row, plus a summary usable from
        scripts via ``--json``.

    Raises
    ------
    ProjectLayoutError
        When the main document does not exist.

    Notes
    -----
    A chapter file with no prose at all is reported rather than skipped.
    An empty chapter is almost always an ``\\input`` pointing at a file
    nobody filled in, and nothing else in the toolchain mentions it.
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

    citations_by_file: dict[Path, int] = {}
    for citation in document.citations:
        citations_by_file[citation.location.file] = (
            citations_by_file.get(citation.location.file, 0) + 1
        )

    chapter_dir = (config.root / config.stats.chapter_dir).resolve()
    content_dir = (config.root / config.stats.content_dir).resolve()
    prose_roots = (chapter_dir, content_dir)

    figure_macros = config.figure_macros
    label_macros = config.label_macros
    statistics: list[FileStatistics] = []
    for tex_file in document.files:
        if only_chapters and not tex_file.path.is_relative_to(chapter_dir):
            continue
        statistics.append(
            _count_file(
                tex_file.path,
                tex_file.prose,
                tex_file.code,
                tex_file.cleaned.display_math,
                citations_by_file.get(tex_file.path, 0),
                prose_roots=prose_roots,
                figure_macros=figure_macros,
                label_macros=label_macros,
            )
        )

    listed = [item for item in statistics if all_files or item.is_prose]
    collapsed = [item for item in statistics if not (all_files or item.is_prose)]

    def _shown(path: Path) -> str:
        try:
            return path.relative_to(config.root).as_posix()
        except ValueError:  # pragma: no cover - file outside the project
            return path.as_posix()

    rows: list[tuple[str, ...]] = []
    findings: list[Finding] = []
    total = TextCounts()
    totals = dict.fromkeys(
        ("chapters", "sections", "figures", "tables", "equations", "citations"), 0
    )

    for item in listed:
        rows.append(
            (
                _shown(item.path),
                str(item.counts.cjk_chars),
                str(item.counts.latin_words),
                str(item.chapters),
                str(item.sections),
                str(item.figures),
                str(item.tables),
                str(item.equations),
                str(item.citations),
            )
        )

    prose_total = TextCounts()
    for item in statistics:
        total = total + item.counts
        if item.path.is_relative_to(chapter_dir) and item.counts.total == 0:
            findings.append(
                Finding(
                    severity=Severity.WARNING,
                    kind="empty-chapter",
                    location=_shown(item.path),
                    message="Chapter file contains no prose.",
                )
            )
        if not item.is_prose:
            # A macro definition such as \newcommand{\hfig}{\begin{figurehere}...}
            # is not a figure; only prose files contribute structural counts.
            continue
        prose_total = prose_total + item.counts
        totals["chapters"] += item.chapters
        totals["sections"] += item.sections
        totals["figures"] += item.figures
        totals["tables"] += item.tables
        totals["equations"] += item.equations
        totals["citations"] += item.citations

    rows.append(
        (
            "TOTAL",
            str(prose_total.cjk_chars),
            str(prose_total.latin_words),
            str(totals["chapters"]),
            str(totals["sections"]),
            str(totals["figures"]),
            str(totals["tables"]),
            str(totals["equations"]),
            str(totals["citations"]),
        )
    )

    if collapsed:
        preamble = TextCounts()
        for item in collapsed:
            preamble = preamble + item.counts
        rows.append(
            (
                f"excluded: {len(collapsed)} preamble file(s)",
                str(preamble.cjk_chars),
                str(preamble.latin_words),
                "-",
                "-",
                "-",
                "-",
                "-",
                "-",
            )
        )

    table = ReportTable(
        title="Word count",
        columns=("file", "CJK", "words", "ch", "sec", "fig", "tab", "eq", "cite"),
        rows=tuple(rows),
        notes=(
            "CJK = Chinese characters, counted individually. words = Latin words.",
            "Comments, math, listings, command names and non-text arguments are excluded.",
            "TOTAL covers the prose files only; preamble files are listed below it.",
            "Pass --all-files to list every file the document pulls in.",
        ),
    )

    summary = {
        "files_scanned": len(statistics),
        "files_listed": len(listed),
        "cjk_chars": prose_total.cjk_chars,
        "latin_words": prose_total.latin_words,
        "cjk_punctuation": prose_total.cjk_punctuation,
        "combined": prose_total.total,
        "cjk_chars_all_files": total.cjk_chars,
        "latin_words_all_files": total.latin_words,
        **totals,
    }
    _LOG.info(
        "Counted %d prose file(s): %d CJK characters, %d Latin words",
        len(listed),
        prose_total.cjk_chars,
        prose_total.latin_words,
    )
    return CommandReport(
        command="stats",
        findings=tuple(findings),
        tables=(table,),
        summary=summary,
    )
