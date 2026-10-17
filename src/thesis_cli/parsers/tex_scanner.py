"""A small LaTeX source scanner.

It expands no macros.  It reads the commands it knows and skips any it
cannot parse, rather than guessing.

Comments, ``verbatim``/``lstlisting`` blocks and, on request, math are
blanked out without changing the length of the text: every removed
character becomes a space and every newline stays, so an offset in the
cleaned text falls on the same line as in the raw file.  That is how
each finding gets an exact ``file:line``.

``\\input`` and ``\\include`` are followed recursively.  A file is read
only once, which also stops include cycles, and a target that cannot be
found is recorded instead of raising.  ``\\cite``, ``\\ref``, ``\\label``
and the graphics references, including those inside the figure macros,
are read with a balanced-brace reader.

Why not a regular expression
----------------------------
LaTeX arguments nest: ``\\fig{0.8}{a}{caption with \\textbf{bold}}{fig:a}``
defeats ``\\{([^}]*)\\}`` on its third argument.  Every argument here is read
by counting braces, honouring backslash escapes, which is why nested markup
in a caption does not shift the file name out of position.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..logging_utils import get_logger

__all__ = [
    "CITATION_COMMANDS",
    "REFERENCE_COMMANDS",
    "Citation",
    "CleanedSource",
    "GraphicRef",
    "LabelDef",
    "LineIndex",
    "MissingInput",
    "RefUse",
    "SourceLocation",
    "TexDocument",
    "TexFile",
    "clean_source",
    "environment_counts",
    "expand_document",
    "read_group",
    "scan_document",
    "strip_comments",
]

_LOG = get_logger(__name__)

VERBATIM_ENVIRONMENTS: Final[frozenset[str]] = frozenset(
    {"verbatim", "verbatim*", "lstlisting", "Verbatim", "minted", "alltt", "filecontents"}
)
MATH_ENVIRONMENTS: Final[frozenset[str]] = frozenset(
    {
        "align",
        "align*",
        "alignat",
        "alignat*",
        "displaymath",
        "eqnarray",
        "eqnarray*",
        "equation",
        "equation*",
        "flalign",
        "flalign*",
        "gather",
        "gather*",
        "math",
        "multline",
        "multline*",
    }
)
FIGURE_ENVIRONMENTS: Final[frozenset[str]] = frozenset({"figure", "figure*", "figurehere"})
TABLE_ENVIRONMENTS: Final[frozenset[str]] = frozenset({"table", "table*", "tablehere"})

CITATION_COMMANDS: Final[frozenset[str]] = frozenset(
    {"cite", "Cite", "citep", "citet", "citeauthor", "citeyear", "citealp", "nocite"}
)
REFERENCE_COMMANDS: Final[frozenset[str]] = frozenset(
    {"ref", "eqref", "pageref", "autoref", "nameref", "cref", "Cref", "vref", "fref"}
)

_ENV_NAME_RE: Final[re.Pattern[str]] = re.compile(r"\\begin\s*\{([^}]*)\}")
_CONTROL_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z]+")


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SourceLocation:
    """A position in a LaTeX source file.

    Attributes
    ----------
    file : pathlib.Path
        Absolute path of the file.
    line : int
        1-based line number.
    """

    file: Path
    line: int

    def render(self, root: Path | None = None) -> str:
        """Format as ``path:line``, relative to *root* when possible.

        Parameters
        ----------
        root : pathlib.Path or None, optional
            Base directory used to shorten the path.

        Returns
        -------
        str
            ``"chapters/ch1.tex:42"`` style location string.
        """
        path = self.file
        if root is not None:
            try:
                path = self.file.relative_to(root)
            except ValueError:
                path = self.file
        return f"{path.as_posix()}:{self.line}"


class LineIndex:
    """Maps character offsets to 1-based line numbers in O(log n)."""

    __slots__ = ("_starts",)

    def __init__(self, text: str) -> None:
        starts = [0]
        for match in re.finditer("\n", text):
            starts.append(match.end())
        self._starts = starts

    def line_of(self, offset: int) -> int:
        """Return the 1-based line number containing *offset*."""
        return bisect_right(self._starts, offset)


# ---------------------------------------------------------------------------
# Extracted items
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Citation:
    """One bibliography key used by one citation command."""

    key: str
    command: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class LabelDef:
    """One ``\\label`` definition."""

    name: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class RefUse:
    """One cross-reference use (``\\ref``, ``\\eqref``, ...)."""

    name: str
    command: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class GraphicRef:
    """One graphics reference, as written in the source.

    Attributes
    ----------
    raw : str
        Exactly the text inside the braces, e.g. ``"./figures/plot"``.
        Not normalised: the figure audit compares what was written with
        what is on disk, byte for byte.
    macro : str
        Name of the command that produced it (``includegraphics``, ``fig``...).
    location : SourceLocation
        Where it appeared.
    """

    raw: str
    macro: str
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class MissingInput:
    """An ``\\input``/``\\include`` target that could not be resolved."""

    target: str
    command: str
    location: SourceLocation


# ---------------------------------------------------------------------------
# Brace / bracket readers
# ---------------------------------------------------------------------------
def read_group(text: str, index: int) -> tuple[str, int] | None:
    """Read a balanced ``{...}`` group starting at *index*.

    Parameters
    ----------
    text : str
        Source text.
    index : int
        Offset of the opening brace.

    Returns
    -------
    tuple of (str, int) or None
        The group's contents and the offset just past the closing brace, or
        ``None`` when *index* does not hold an opening brace or the group is
        unterminated.

    Examples
    --------
    >>> read_group("{a{b}c}rest", 0)
    ('a{b}c', 7)
    >>> read_group("{a\\\\}b}", 0)
    ('a\\\\}b', 6)
    """
    if index >= len(text) or text[index] != "{":
        return None
    depth = 0
    cursor = index
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[index + 1 : cursor], cursor + 1
        cursor += 1
    return None


def _read_bracket(text: str, index: int) -> tuple[str, int] | None:
    """Read a ``[...]`` optional argument starting at *index*."""
    if index >= len(text) or text[index] != "[":
        return None
    depth = 0
    cursor = index
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[index + 1 : cursor], cursor + 1
        cursor += 1
    return None


def _skip_blank(text: str, index: int) -> int:
    """Skip spaces, tabs and at most one newline (LaTeX argument separator)."""
    cursor = index
    newlines = 0
    while cursor < len(text):
        char = text[cursor]
        if char == "\n":
            newlines += 1
            if newlines > 1:
                break
            cursor += 1
        elif char in " \t\r":
            cursor += 1
        else:
            break
    return cursor


def _argument_spans(text: str, index: int, count: int) -> tuple[list[tuple[int, int]], int] | None:
    """Locate *count* mandatory arguments, skipping ``*`` and ``[...]`` along the way.

    Parameters
    ----------
    text : str
        Source text.
    index : int
        Offset just past the command name.
    count : int
        How many mandatory ``{...}`` groups to locate.

    Returns
    -------
    tuple of (list of tuple of int, int) or None
        ``(start, end)`` offsets of each argument's contents, braces
        excluded, and the offset past the last one; ``None`` if the command
        is truncated.
    """
    cursor = _skip_blank(text, index)
    if cursor < len(text) and text[cursor] == "*":
        cursor = _skip_blank(text, cursor + 1)
    spans: list[tuple[int, int]] = []
    while len(spans) < count:
        cursor = _skip_blank(text, cursor)
        if cursor >= len(text):
            return None
        if text[cursor] == "[":
            bracket = _read_bracket(text, cursor)
            if bracket is None:
                return None
            cursor = bracket[1]
            continue
        group = read_group(text, cursor)
        if group is None:
            return None
        spans.append((cursor + 1, group[1] - 1))
        cursor = group[1]
    return spans, cursor


def _read_arguments(text: str, index: int, count: int) -> tuple[list[str], int] | None:
    """Read *count* mandatory arguments, skipping ``*`` and ``[...]`` along the way.

    Parameters
    ----------
    text : str
        Source text.
    index : int
        Offset just past the command name.
    count : int
        How many mandatory ``{...}`` groups to collect.

    Returns
    -------
    tuple of (list of str, int) or None
        The argument contents and the offset past the last one, or ``None``
        if the command is truncated.
    """
    located = _argument_spans(text, index, count)
    if located is None:
        return None
    spans, cursor = located
    return [text[start:end] for start, end in spans], cursor


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CleanedSource:
    """Two length-preserving views of one LaTeX file, plus content counters.

    Attributes
    ----------
    code : str
        Comments, ``\\verb`` and verbatim-like environments blanked out.
        Math is kept, because ``\\label`` and ``\\cite`` legitimately
        appear inside ``equation`` and ``align`` bodies.
    prose : str
        As *code*, with math additionally blanked out.  This is what the
        word counter reads.
    inline_math : int
        Number of ``$...$`` / ``\\(...\\)`` groups.
    display_math : int
        Number of display-math constructs: ``equation``-like
        environments, starred or not, plus ``$$...$$`` and ``\\[...\\]``.
    verbatim_blocks : int
        Number of verbatim-like blocks removed.
    """

    code: str
    prose: str
    inline_math: int = 0
    display_math: int = 0
    verbatim_blocks: int = 0


def _blank_span(buffer: list[str], start: int, end: int) -> None:
    """Replace ``buffer[start:end]`` with spaces, keeping newlines intact."""
    for position in range(start, min(end, len(buffer))):
        if buffer[position] != "\n":
            buffer[position] = " "


def _find_environment_end(text: str, name: str, start: int) -> int:
    """Return the offset just past ``\\end{name}``, or ``len(text)``."""
    needle = re.compile(r"\\end\s*\{" + re.escape(name) + r"\}")
    match = needle.search(text, start)
    return match.end() if match else len(text)


def clean_source(text: str) -> CleanedSource:
    """Blank out comments, verbatim blocks and math in a single pass.

    Parameters
    ----------
    text : str
        Raw contents of a ``.tex`` file.

    Returns
    -------
    CleanedSource
        Both cleaned views and the math/verbatim counters.

    Notes
    -----
    The scan is a hand-written state machine rather than a chain of regular
    substitutions, because the three constructs interact: a ``%`` inside
    ``\\begin{verbatim}`` is literal text, a ``$`` inside a comment starts
    nothing, and ``\\%`` is neither.  A single left-to-right pass sees each
    character in its context; separate substitutions would each have to
    know about the other two.

    Examples
    --------
    >>> clean_source("a % comment\\nb").code
    'a          \\nb'
    >>> clean_source("50\\\\% done").code
    '50\\\\% done'
    >>> clean_source("x $y+1$ z").prose.split()
    ['x', 'z']
    """
    code = list(text)
    math_spans: list[tuple[int, int]] = []
    inline_math = 0
    display_math = 0
    verbatim_blocks = 0

    length = len(text)
    cursor = 0
    while cursor < length:
        char = text[cursor]

        if char == "%":
            end = text.find("\n", cursor)
            end = length if end == -1 else end
            _blank_span(code, cursor, end)
            cursor = end
            continue

        if char == "$":
            is_display = cursor + 1 < length and text[cursor + 1] == "$"
            delimiter = "$$" if is_display else "$"
            probe = cursor + len(delimiter)
            while probe < length:
                if text[probe] == "\\":
                    probe += 2
                    continue
                if text.startswith(delimiter, probe):
                    break
                probe += 1
            end = min(probe + len(delimiter), length)
            math_spans.append((cursor, end))
            if is_display:
                display_math += 1
            else:
                inline_math += 1
            cursor = end
            continue

        if char != "\\":
            cursor += 1
            continue

        # -- a control sequence ------------------------------------------
        if cursor + 1 >= length:
            break
        nxt = text[cursor + 1]

        if nxt == "[":
            end = text.find("\\]", cursor + 2)
            end = length if end == -1 else end + 2
            math_spans.append((cursor, end))
            display_math += 1
            cursor = end
            continue
        if nxt == "(":
            end = text.find("\\)", cursor + 2)
            end = length if end == -1 else end + 2
            math_spans.append((cursor, end))
            inline_math += 1
            cursor = end
            continue
        if not nxt.isalpha():
            # An escaped literal such as \%, \$, \{, \_ or the \\ line break.
            cursor += 2
            continue

        word_match = _CONTROL_WORD_RE.match(text, cursor + 1)
        assert word_match is not None  # guaranteed by nxt.isalpha()
        name = word_match.group(0)
        after = word_match.end()

        if name == "verb":
            probe = after
            if probe < length and text[probe] == "*":
                probe += 1
            if probe < length:
                delimiter = text[probe]
                end = text.find(delimiter, probe + 1)
                end = length if end == -1 else end + 1
                _blank_span(code, cursor, end)
                verbatim_blocks += 1
                cursor = end
                continue
            cursor = after
            continue

        if name == "begin":
            env_match = _ENV_NAME_RE.match(text, cursor)
            if env_match is None:
                cursor = after
                continue
            env_name = env_match.group(1).strip()
            if env_name in VERBATIM_ENVIRONMENTS:
                end = _find_environment_end(text, env_name, env_match.end())
                _blank_span(code, cursor, end)
                verbatim_blocks += 1
                cursor = end
                continue
            if env_name in MATH_ENVIRONMENTS:
                end = _find_environment_end(text, env_name, env_match.end())
                math_spans.append((cursor, end))
                display_math += 1
                cursor = end
                continue
            cursor = env_match.end()
            continue

        cursor = after

    code_text = "".join(code)
    prose = list(code_text)
    for start, end in math_spans:
        _blank_span(prose, start, end)

    return CleanedSource(
        code=code_text,
        prose="".join(prose),
        inline_math=inline_math,
        display_math=display_math,
        verbatim_blocks=verbatim_blocks,
    )


def strip_comments(text: str) -> str:
    """Return *text* with LaTeX comments blanked out.

    Parameters
    ----------
    text : str
        Raw LaTeX source.

    Returns
    -------
    str
        Same length as the input; comment characters become spaces.

    Examples
    --------
    >>> strip_comments("keep % drop").rstrip()
    'keep'
    >>> strip_comments("100\\\\% sure")
    '100\\\\% sure'
    """
    return clean_source(text).code


# ---------------------------------------------------------------------------
# Files and documents
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TexFile:
    """One source file together with its cleaned views."""

    path: Path
    text: str
    cleaned: CleanedSource

    @property
    def code(self) -> str:
        """str: comment-free, verbatim-free source."""
        return self.cleaned.code

    @property
    def prose(self) -> str:
        """str: as :attr:`code` but with math removed as well."""
        return self.cleaned.prose


@dataclass(frozen=True, slots=True)
class TexDocument:
    """A main file and everything it pulls in, already scanned.

    Attributes
    ----------
    root : pathlib.Path
        Project root, used only to shorten paths in reports.
    main : pathlib.Path
        The document that was expanded.
    files : tuple of TexFile
        Every file reached, in depth-first inclusion order, main file first.
    citations, labels, references, graphics : tuple
        What :func:`scan_document` extracted, in source order.
    missing_inputs : tuple of MissingInput
        ``\\input``/``\\include`` targets not found on disk.
    bib_commands : tuple of str
        Databases named by ``\\bibliography``/``\\addbibresource``, each
        once, in order of first appearance.
    cite_all : bool
        Whether the source contains ``\\nocite{*}`` (or ``*`` in any other
        citation command).
    """

    root: Path
    main: Path
    files: tuple[TexFile, ...] = ()
    citations: tuple[Citation, ...] = ()
    labels: tuple[LabelDef, ...] = ()
    references: tuple[RefUse, ...] = ()
    graphics: tuple[GraphicRef, ...] = ()
    missing_inputs: tuple[MissingInput, ...] = ()
    bib_commands: tuple[str, ...] = ()
    cite_all: bool = False

    @property
    def paths(self) -> tuple[Path, ...]:
        """tuple of pathlib.Path: every file reached by the expansion."""
        return tuple(item.path for item in self.files)

    def cited_keys(self) -> set[str]:
        """Return the set of distinct bibliography keys used in the source."""
        return {item.key for item in self.citations}

    def label_names(self) -> set[str]:
        """Return the set of distinct label names defined in the source."""
        return {item.name for item in self.labels}

    def duplicate_labels(self) -> dict[str, list[LabelDef]]:
        """Group label definitions that share a name.

        Returns
        -------
        dict
            Name to the two or more definitions that declare it.  Empty when
            every label is unique.
        """
        grouped: dict[str, list[LabelDef]] = {}
        for item in self.labels:
            grouped.setdefault(item.name, []).append(item)
        return {name: items for name, items in grouped.items() if len(items) > 1}


def _resolve_input(target: str, current: Path, root: Path) -> Path | None:
    """Resolve an ``\\input`` target the way a TeX run would."""
    cleaned = target.strip().strip('"')
    if not cleaned:
        return None
    cleaned = cleaned.replace("\\space", " ").strip()
    candidates: list[Path] = []
    bases = (root, current.parent)
    suffixes = ("", ".tex") if not Path(cleaned).suffix else ("",)
    for base in bases:
        for suffix in suffixes:
            candidates.append(base / (cleaned + suffix))
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.resolve()
        except OSError:  # pragma: no cover - exotic path characters
            continue
    return None


def _iter_commands(text: str, names: Iterable[str]) -> Iterator[tuple[str, int, int]]:
    """Yield ``(name, start, end_of_name)`` for each listed command in *text*."""
    sorted_names = sorted(set(names), key=len, reverse=True)
    if not sorted_names:
        return
    pattern = re.compile(
        r"\\(" + "|".join(re.escape(item) for item in sorted_names) + r")(?![A-Za-z])"
    )
    for match in pattern.finditer(text):
        yield match.group(1), match.start(), match.end()


def _read_file(path: Path) -> str:
    """Read a ``.tex`` file that may be UTF-8, Big5 (cp950) or Latin-1."""
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp950", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")  # pragma: no cover - unreachable


def expand_document(
    main: Path,
    root: Path | None = None,
    *,
    max_depth: int = 16,
) -> tuple[list[TexFile], list[MissingInput]]:
    """Follow ``\\input``/``\\include`` from *main* and read every file.

    Parameters
    ----------
    main : pathlib.Path
        The document to start from.
    root : pathlib.Path or None, optional
        Directory the TeX engine would run in.  Defaults to *main*'s parent.
    max_depth : int, optional
        Recursion guard for pathological nesting.

    Returns
    -------
    files : list of TexFile
        Files reached, in depth-first order, main file first.  Each file
        appears once even if it is included several times.
    missing : list of MissingInput
        Targets that could not be resolved on disk.

    Raises
    ------
    FileNotFoundError
        If *main* itself does not exist.
    """
    main = Path(main).resolve()
    if not main.is_file():
        raise FileNotFoundError(f"Main document not found: {main}")
    base = Path(root).resolve() if root is not None else main.parent

    files: list[TexFile] = []
    missing: list[MissingInput] = []
    seen: set[Path] = set()

    def walk(path: Path, depth: int) -> None:
        resolved = path.resolve()
        if resolved in seen:
            _LOG.debug("Skipping already-expanded file %s", resolved)
            return
        if depth > max_depth:
            _LOG.warning("Maximum include depth %d reached at %s", max_depth, resolved)
            return
        seen.add(resolved)
        try:
            text = _read_file(resolved)
        except OSError as exc:  # pragma: no cover - permissions / races
            _LOG.warning("Cannot read %s: %s", resolved, exc)
            return
        cleaned = clean_source(text)
        tex_file = TexFile(path=resolved, text=text, cleaned=cleaned)
        files.append(tex_file)

        index = LineIndex(cleaned.code)
        for name, start, end_of_name in _iter_commands(cleaned.code, ("input", "include")):
            parsed = _read_arguments(cleaned.code, end_of_name, 1)
            if parsed is None:
                # TeX also accepts the space-delimited primitive form:
                # \input chapters/ch1
                tail = cleaned.code[end_of_name:]
                bare = re.match(r"[ \t]*([^\s{}\\%]+)", tail)
                if bare is None:
                    continue
                target = bare.group(1)
            else:
                target = parsed[0][0]
            child = _resolve_input(target, resolved, base)
            location = SourceLocation(file=resolved, line=index.line_of(start))
            if child is None:
                missing.append(MissingInput(target=target, command=name, location=location))
                _LOG.debug("Unresolved %s{%s} at %s", name, target, location.render(base))
                continue
            walk(child, depth + 1)

    walk(main, 0)
    return files, missing


def environment_counts(text: str) -> dict[str, int]:
    r"""Count ``\begin{...}`` occurrences per environment name.

    Parameters
    ----------
    text : str
        Cleaned LaTeX source (comments and verbatim already removed).

    Returns
    -------
    dict of str to int
        Environment name to the number of times it is opened.

    Examples
    --------
    >>> environment_counts(r"\begin{figure}\end{figure}\begin{table}\end{table}")
    {'figure': 1, 'table': 1}
    """
    counts: dict[str, int] = {}
    for match in _ENV_NAME_RE.finditer(text):
        name = match.group(1).strip()
        counts[name] = counts.get(name, 0) + 1
    return counts


def _is_macro_parameter(name: str) -> bool:
    r"""Return ``True`` for an argument reference such as ``#4``.

    Parameters
    ----------
    name : str
        A label or reference name as read from the source.

    Returns
    -------
    bool
        Whether the name is a macro parameter rather than a real label.

    Notes
    -----
    ``\newcommand{\fig}[4]{...\label{#4}}`` contains a ``\label`` whose
    argument is the placeholder ``#4``.  Counting those would report the
    whole figure macro family as nine duplicate definitions of a label
    called "#4".
    """
    return name.startswith("#")


def _scan_file(
    tex_file: TexFile,
    macros: Mapping[str, int],
    label_macros: Mapping[str, int],
) -> tuple[list[Citation], list[LabelDef], list[RefUse], list[GraphicRef], list[str], bool]:
    """Extract every tracked construct from one already-cleaned file."""
    text = tex_file.code
    index = LineIndex(text)
    citations: list[Citation] = []
    labels: list[LabelDef] = []
    references: list[RefUse] = []
    graphics: list[GraphicRef] = []
    bib_commands: list[str] = []
    cite_all = False

    for name, start, end_of_name in _iter_commands(text, CITATION_COMMANDS):
        parsed = _read_arguments(text, end_of_name, 1)
        if parsed is None:
            continue
        location = SourceLocation(file=tex_file.path, line=index.line_of(start))
        for raw_key in parsed[0][0].split(","):
            key = raw_key.strip()
            if not key:
                continue
            if key == "*":
                cite_all = True
                continue
            citations.append(Citation(key=key, command=name, location=location))

    for _, start, end_of_name in _iter_commands(text, ("label",)):
        parsed = _read_arguments(text, end_of_name, 1)
        if parsed is None:
            continue
        name = parsed[0][0].strip()
        if not name or _is_macro_parameter(name):
            continue
        labels.append(
            LabelDef(
                name=name,
                location=SourceLocation(file=tex_file.path, line=index.line_of(start)),
            )
        )

    # Labels created by a figure macro, e.g. \fig{w}{file}{caption}{fig:x}.
    for name, start, end_of_name in _iter_commands(text, label_macros.keys()):
        position = label_macros[name]
        parsed = _read_arguments(text, end_of_name, position)
        if parsed is None:
            continue
        label_name = parsed[0][position - 1].strip()
        if not label_name or _is_macro_parameter(label_name):
            continue
        labels.append(
            LabelDef(
                name=label_name,
                location=SourceLocation(file=tex_file.path, line=index.line_of(start)),
            )
        )

    for name, start, end_of_name in _iter_commands(text, REFERENCE_COMMANDS):
        parsed = _read_arguments(text, end_of_name, 1)
        if parsed is None:
            continue
        location = SourceLocation(file=tex_file.path, line=index.line_of(start))
        for raw_name in parsed[0][0].split(","):
            target = raw_name.strip()
            if target and not _is_macro_parameter(target):
                references.append(RefUse(name=target, command=name, location=location))

    for name, start, end_of_name in _iter_commands(text, macros.keys()):
        position = macros[name]
        parsed = _read_arguments(text, end_of_name, position)
        if parsed is None:
            _LOG.debug(
                "Could not read %d argument(s) of \\%s at %s:%d",
                position,
                name,
                tex_file.path,
                index.line_of(start),
            )
            continue
        raw = parsed[0][position - 1].strip()
        if not raw or _is_macro_parameter(raw):
            continue
        graphics.append(
            GraphicRef(
                raw=raw,
                macro=name,
                location=SourceLocation(file=tex_file.path, line=index.line_of(start)),
            )
        )

    # The command name is irrelevant here: \bibliography and
    # \addbibresource both just name a database.
    for _, _, end_of_name in _iter_commands(text, ("bibliography", "addbibresource")):
        parsed = _read_arguments(text, end_of_name, 1)
        if parsed is None:
            continue
        for item in parsed[0][0].split(","):
            entry = item.strip()
            if entry:
                bib_commands.append(entry)

    return citations, labels, references, graphics, bib_commands, cite_all


def scan_document(
    main: Path,
    root: Path | None = None,
    *,
    macros: Mapping[str, int] | None = None,
    label_macros: Mapping[str, int] | None = None,
    max_depth: int = 16,
) -> TexDocument:
    """Expand and scan a LaTeX document.

    Parameters
    ----------
    main : pathlib.Path
        Main ``.tex`` file.
    root : pathlib.Path or None, optional
        Directory used to resolve ``\\input`` targets; defaults to *main*'s
        parent.
    macros : mapping of str to int or None, optional
        Figure macro table mapping a command name to the 1-based index of
        its file-name argument.  Defaults to
        :data:`thesis_cli.config.DEFAULT_FIGURE_MACROS`.
    label_macros : mapping of str to int or None, optional
        Macros that create a ``\\label``, mapping the command name to the
        1-based index of the label argument.  Defaults to
        :data:`thesis_cli.config.DEFAULT_LABEL_MACROS`.
    max_depth : int, optional
        Include-recursion guard.

    Returns
    -------
    TexDocument
        Everything the audit commands need, with exact source locations.

    Examples
    --------
    >>> import tempfile, pathlib
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     p = pathlib.Path(tmp, "m.tex")
    ...     _ = p.write_text(r"\\label{a}\\cite{k}\\includegraphics{f.png}")
    ...     doc = scan_document(p)
    ...     sorted(doc.cited_keys()), doc.graphics[0].raw
    (['k'], 'f.png')
    """
    from ..config import DEFAULT_FIGURE_MACROS, DEFAULT_LABEL_MACROS

    macro_table = dict(DEFAULT_FIGURE_MACROS if macros is None else macros)
    label_table = dict(DEFAULT_LABEL_MACROS if label_macros is None else label_macros)
    main = Path(main).resolve()
    base = Path(root).resolve() if root is not None else main.parent
    files, missing = expand_document(main, base, max_depth=max_depth)

    citations: list[Citation] = []
    labels: list[LabelDef] = []
    references: list[RefUse] = []
    graphics: list[GraphicRef] = []
    bib_commands: list[str] = []
    cite_all = False

    for tex_file in files:
        file_cites, file_labels, file_refs, file_graphics, file_bibs, file_all = _scan_file(
            tex_file, macro_table, label_table
        )
        citations.extend(file_cites)
        labels.extend(file_labels)
        references.extend(file_refs)
        graphics.extend(file_graphics)
        bib_commands.extend(file_bibs)
        cite_all = cite_all or file_all

    _LOG.info(
        "Scanned %d file(s): %d citation(s), %d label(s), %d reference(s), %d graphic(s)",
        len(files),
        len(citations),
        len(labels),
        len(references),
        len(graphics),
    )
    return TexDocument(
        root=base,
        main=main,
        files=tuple(files),
        citations=tuple(citations),
        labels=tuple(labels),
        references=tuple(references),
        graphics=tuple(graphics),
        missing_inputs=tuple(missing),
        bib_commands=tuple(dict.fromkeys(bib_commands)),
        cite_all=cite_all,
    )
