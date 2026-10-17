"""Reader for the ``.aux`` file LaTeX writes on every pass.

LaTeX carries cross-reference and citation data from one pass to the next
in the ``.aux``.  Reading it tells the audit commands what the source alone
cannot: which numbers the last run assigned, which cited keys that run
never saw (a stale ``.aux``), and which ``.bib`` database and style it
really used, so they can be compared with ``thesis.toml``.

``\\newlabel`` comes in two shapes and both are accepted:

.. code-block:: latex

    \\newlabel{sec:intro}{{1}{1}}                                  % plain
    \\newlabel{eq:nn}{{2.1}{6}{Chapter title\\relax }{equation.2.1.1}{}}  % hyperref

So the number of fields is never assumed: every balanced group present is
collected, and the ones with a known meaning get a name.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from ..logging_utils import get_logger
from .tex_scanner import read_group

__all__ = [
    "AuxData",
    "AuxLabel",
    "parse_aux",
    "parse_aux_text",
]

_LOG = get_logger(__name__)

_AUX_COMMANDS: Final[tuple[str, ...]] = (
    "newlabel",
    "citation",
    "bibcite",
    "bibdata",
    "bibstyle",
    "@writefile",
    "@input",
)


@dataclass(frozen=True, slots=True)
class AuxLabel:
    """One ``\\newlabel`` entry.

    Attributes
    ----------
    name : str
        The label name, i.e. the argument of ``\\label`` in the source.
    fields : tuple of str
        Every balanced group of the second argument, in order.
    number : str
        Printed number (``"2.1"``).  Empty when the entry is malformed.
    page : str
        Printed page number.  Empty when absent.
    anchor : str or None
        ``hyperref`` anchor name, present only in the five-field form.
    """

    name: str
    fields: tuple[str, ...] = ()

    @property
    def number(self) -> str:
        """str: the printed reference number."""
        return self.fields[0] if self.fields else ""

    @property
    def page(self) -> str:
        """str: the printed page number."""
        return self.fields[1] if len(self.fields) > 1 else ""

    @property
    def anchor(self) -> str | None:
        """str or None: the hyperref anchor, when the entry carries one."""
        return self.fields[3] if len(self.fields) > 3 else None

    @property
    def has_hyperref_fields(self) -> bool:
        """bool: ``True`` for the five-field hyperref shape."""
        return len(self.fields) >= 4


@dataclass(frozen=True, slots=True)
class AuxData:
    """Everything extracted from one ``.aux`` file (and its ``\\@input`` children).

    Attributes
    ----------
    path : pathlib.Path or None
        Source file, or ``None`` when parsed from a string.
    labels : tuple of AuxLabel
        Label definitions, in file order.
    citations : tuple of str
        Citation keys in the order LaTeX encountered them, duplicates kept.
    bibcite : mapping of str to str
        Key to the number BibTeX assigned, i.e. the keys that really made it
        into the bibliography.
    bibdata : tuple of str
        Database names from ``\\bibdata``, i.e. the ``.bib`` files BibTeX
        actually reads for this document.
    bibstyle : str or None
        Style name from ``\\bibstyle``.
    writefiles : tuple of tuple of (str, str)
        ``(destination, payload)`` pairs from ``\\@writefile``.
    included : tuple of pathlib.Path
        Child ``.aux`` files pulled in via ``\\@input``.
    """

    path: Path | None = None
    labels: tuple[AuxLabel, ...] = ()
    citations: tuple[str, ...] = ()
    bibcite: Mapping[str, str] = field(default_factory=dict)
    bibdata: tuple[str, ...] = ()
    bibstyle: str | None = None
    writefiles: tuple[tuple[str, str], ...] = ()
    included: tuple[Path, ...] = ()

    @property
    def label_names(self) -> set[str]:
        """set of str: distinct label names."""
        return {item.name for item in self.labels}

    @property
    def citation_keys(self) -> set[str]:
        """set of str: distinct citation keys."""
        return set(self.citations)

    @property
    def toc_entries(self) -> int:
        """int: number of ``\\@writefile`` payloads aimed at ``toc``."""
        return sum(1 for destination, _ in self.writefiles if destination == "toc")


def _split_groups(text: str, index: int) -> tuple[list[str], int]:
    """Read every consecutive balanced group starting at *index*."""
    groups: list[str] = []
    cursor = index
    while cursor < len(text) and text[cursor] == "{":
        parsed = read_group(text, cursor)
        if parsed is None:
            break
        groups.append(parsed[0])
        cursor = parsed[1]
    return groups, cursor


def parse_aux_text(text: str, *, path: Path | None = None) -> AuxData:
    """Parse the contents of an ``.aux`` file.

    Parameters
    ----------
    text : str
        Raw file contents.
    path : pathlib.Path or None, optional
        Origin, recorded on the result for reporting.

    Returns
    -------
    AuxData
        Parsed bookkeeping data.  Malformed lines are skipped with a debug
        message rather than raising: a half-written ``.aux`` from an aborted
        run must not crash an audit.

    Examples
    --------
    >>> aux = parse_aux_text(
    ...     r"\\newlabel{eq:a}{{2.1}{6}{Title}{equation.2.1.1}{}}"
    ...     "\\n" r"\\citation{k1,k2}" "\\n" r"\\bibstyle{ieeetr}"
    ... )
    >>> aux.labels[0].number, aux.labels[0].anchor
    ('2.1', 'equation.2.1.1')
    >>> sorted(aux.citation_keys), aux.bibstyle
    (['k1', 'k2'], 'ieeetr')
    """
    labels: list[AuxLabel] = []
    citations: list[str] = []
    bibcite: dict[str, str] = {}
    bibdata: list[str] = []
    bibstyle: str | None = None
    writefiles: list[tuple[str, str]] = []
    included: list[Path] = []

    cursor = 0
    length = len(text)
    while cursor < length:
        backslash = text.find("\\", cursor)
        if backslash == -1:
            break
        matched: str | None = None
        for command in _AUX_COMMANDS:
            if text.startswith(command, backslash + 1):
                after = backslash + 1 + len(command)
                # Guard against \bibcitex or \citationfoo.
                if after < length and text[after].isalpha():
                    continue
                matched = command
                break
        if matched is None:
            cursor = backslash + 1
            continue

        start = backslash + 1 + len(matched)
        groups, end = _split_groups(text, start)
        cursor = max(end, backslash + 1)

        if matched == "newlabel":
            if not groups:
                _LOG.debug("Skipping malformed \\newlabel at offset %d", backslash)
                continue
            name = groups[0]
            fields: tuple[str, ...] = ()
            if len(groups) > 1:
                inner, _ = _split_groups(groups[1], 0)
                fields = tuple(inner)
            labels.append(AuxLabel(name=name, fields=fields))
        elif matched == "citation":
            if groups:
                for raw in groups[0].split(","):
                    key = raw.strip()
                    if key:
                        citations.append(key)
        elif matched == "bibcite":
            if len(groups) >= 2:
                bibcite[groups[0].strip()] = groups[1].strip()
            elif groups:
                bibcite[groups[0].strip()] = ""
        elif matched == "bibdata":
            if groups:
                for raw in groups[0].split(","):
                    name = raw.strip()
                    if name:
                        bibdata.append(name)
        elif matched == "bibstyle":
            if groups:
                bibstyle = groups[0].strip()
        elif matched == "@writefile":
            if len(groups) >= 2:
                writefiles.append((groups[0].strip(), groups[1]))
        elif matched == "@input":
            if groups:
                included.append(Path(groups[0].strip()))

    return AuxData(
        path=path,
        labels=tuple(labels),
        citations=tuple(citations),
        bibcite=bibcite,
        bibdata=tuple(bibdata),
        bibstyle=bibstyle,
        writefiles=tuple(writefiles),
        included=tuple(included),
    )


def parse_aux(path: Path, *, follow_includes: bool = True, _depth: int = 0) -> AuxData:
    """Read and parse an ``.aux`` file from disk.

    Parameters
    ----------
    path : pathlib.Path
        The ``.aux`` file.
    follow_includes : bool, optional
        Also read the child ``.aux`` files named by ``\\@input``, merging
        their contents.  A document split with ``\\include`` writes one such
        file per included part.
    _depth : int, optional
        Internal recursion guard.

    Returns
    -------
    AuxData
        Merged bookkeeping data.  An empty :class:`AuxData` is returned when
        the file does not exist, since a missing ``.aux`` only means the
        document has not been compiled yet.
    """
    path = Path(path)
    if not path.is_file():
        _LOG.debug("No .aux file at %s (document not compiled yet?)", path)
        return AuxData(path=path)

    raw = path.read_bytes()
    for encoding in ("utf-8", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 never fails
        text = raw.decode("utf-8", errors="replace")

    data = parse_aux_text(text, path=path)
    if not follow_includes or _depth > 8 or not data.included:
        return data

    labels = list(data.labels)
    citations = list(data.citations)
    bibcite = dict(data.bibcite)
    bibdata = list(data.bibdata)
    writefiles = list(data.writefiles)
    bibstyle = data.bibstyle

    for child_name in data.included:
        child_path = (path.parent / child_name).resolve()
        if child_path == path.resolve():
            continue
        child = parse_aux(child_path, follow_includes=True, _depth=_depth + 1)
        labels.extend(child.labels)
        citations.extend(child.citations)
        bibcite.update(child.bibcite)
        bibdata.extend(item for item in child.bibdata if item not in bibdata)
        writefiles.extend(child.writefiles)
        bibstyle = bibstyle or child.bibstyle

    return AuxData(
        path=path,
        labels=tuple(labels),
        citations=tuple(citations),
        bibcite=bibcite,
        bibdata=tuple(bibdata),
        bibstyle=bibstyle,
        writefiles=tuple(writefiles),
        included=data.included,
    )
