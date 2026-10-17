"""BibTeX database reader with a quality audit.

BibTeX writes its complaints to the ``.blg``, and a build that succeeds
gives no reason to open it, so a broken entry usually turns up only as a
wrong line in the reference list.  Besides reading the entries, this parser reports the defects that
get through a build that otherwise looks fine:

* the same key defined twice, in one file or across several files (and
  whether the two definitions actually disagree);
* required fields missing for the entry type;
* fields present but empty;
* a name list ending in a stray comma, which BibTeX reports only in the
  ``.blg`` ("has a comma at the end") and counts as an error;
* a ``year`` field that does not contain a year, for example because a
  whole book citation was pasted into it.

It reads the part of the grammar real ``.bib`` files use:
``@type{key, f = {v}, ...}`` with ``{...}``, ``"..."`` and bare values,
``#`` concatenation and ``@string`` macros.  ``@comment`` and
``@preamble`` blocks are skipped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Final

from ..logging_utils import get_logger
from .tex_scanner import LineIndex

__all__ = [
    "REQUIRED_FIELDS",
    "BibDatabase",
    "BibEntry",
    "BibProblem",
    "BibProblemKind",
    "detect_duplicate_keys",
    "parse_bib",
    "parse_bib_files",
    "parse_bib_text",
]

_LOG = get_logger(__name__)

REQUIRED_FIELDS: Final[Mapping[str, tuple[tuple[str, ...], ...]]] = {
    # entry type -> tuple of requirement groups; each group is satisfied when
    # at least one of its field names is present and non-empty.
    "article": (("author",), ("title",), ("journal",), ("year",)),
    "book": (("author", "editor"), ("title",), ("publisher",), ("year",)),
    "booklet": (("title",),),
    "inbook": (("author", "editor"), ("title",), ("chapter", "pages"), ("publisher",), ("year",)),
    "incollection": (("author",), ("title",), ("booktitle",), ("publisher",), ("year",)),
    "inproceedings": (("author",), ("title",), ("booktitle",), ("year",)),
    "conference": (("author",), ("title",), ("booktitle",), ("year",)),
    "manual": (("title",),),
    "mastersthesis": (("author",), ("title",), ("school",), ("year",)),
    "phdthesis": (("author",), ("title",), ("school",), ("year",)),
    "misc": (),
    "proceedings": (("title",), ("year",)),
    "techreport": (("author",), ("title",), ("institution",), ("year",)),
    "unpublished": (("author",), ("title",), ("note",)),
    "online": (("title",),),
    "electronic": (("title",),),
}

_NAME_FIELDS: Final[frozenset[str]] = frozenset({"author", "editor"})
_YEAR_RE: Final[re.Pattern[str]] = re.compile(r"\b(1[5-9]\d{2}|20\d{2})\b")
_IGNORED_TYPES: Final[frozenset[str]] = frozenset({"comment", "preamble"})


class BibProblemKind(str, Enum):
    """Categories of defect this parser reports."""

    DUPLICATE_KEY = "duplicate-key"
    CONFLICTING_DUPLICATE = "conflicting-duplicate"
    MISSING_FIELD = "missing-field"
    EMPTY_FIELD = "empty-field"
    TRAILING_COMMA_IN_NAMES = "trailing-comma-in-names"
    MALFORMED_YEAR = "malformed-year"
    UNKNOWN_ENTRY_TYPE = "unknown-entry-type"
    SYNTAX = "syntax"


@dataclass(frozen=True, slots=True)
class BibEntry:
    """One bibliography entry.

    Attributes
    ----------
    key : str
        Citation key as used by ``\\cite``.
    entry_type : str
        Lower-cased entry type, e.g. ``"inproceedings"``.
    fields : mapping of str to str
        Lower-cased field name to its value, outer delimiters removed and
        internal whitespace collapsed.
    path : pathlib.Path or None
        File the entry came from.
    line : int
        1-based line of the ``@`` that opens the entry.
    """

    key: str
    entry_type: str
    fields: Mapping[str, str] = field(default_factory=dict)
    path: Path | None = None
    line: int = 0

    def get(self, name: str, default: str = "") -> str:
        """Return a field value, case-insensitively.

        Parameters
        ----------
        name : str
            Field name.
        default : str, optional
            Value returned when the field is absent.

        Returns
        -------
        str
            The field value or *default*.
        """
        return self.fields.get(name.lower(), default)

    def signature(self) -> tuple[tuple[str, str], ...]:
        """Return a hashable, order-independent view of the entry's content.

        Returns
        -------
        tuple
            Sorted ``(field, value)`` pairs plus the entry type.  Two entries
            with the same key and the same signature are true duplicates;
            differing signatures mean the databases disagree.
        """
        pairs = sorted((name, value) for name, value in self.fields.items())
        return (("@type", self.entry_type), *pairs)


@dataclass(frozen=True, slots=True)
class BibProblem:
    """One defect found in a bibliography database.

    Attributes
    ----------
    kind : BibProblemKind
        Category of the defect.
    key : str
        Citation key of the entry; empty for a ``syntax`` problem, where
        there is no key to name.
    message : str
        One-sentence description.
    path : pathlib.Path or None
        File the entry came from.
    line : int
        1-based line of the entry, ``0`` when unknown.
    severity : str
        ``"error"`` or ``"warning"``.
    """

    kind: BibProblemKind
    key: str
    message: str
    path: Path | None = None
    line: int = 0
    severity: str = "error"

    def render_location(self, root: Path | None = None) -> str:
        """Format the problem's origin as ``path:line``.

        Parameters
        ----------
        root : pathlib.Path or None, optional
            Base directory used to shorten the path.

        Returns
        -------
        str
            Location string, or ``"-"`` when unknown.
        """
        if self.path is None:
            return "-"
        shown = self.path
        if root is not None:
            try:
                shown = self.path.relative_to(root)
            except ValueError:
                shown = self.path
        return f"{shown.as_posix()}:{self.line}"


@dataclass(frozen=True, slots=True)
class BibDatabase:
    """A parsed collection of ``.bib`` files."""

    entries: tuple[BibEntry, ...] = ()
    problems: tuple[BibProblem, ...] = ()
    strings: Mapping[str, str] = field(default_factory=dict)
    paths: tuple[Path, ...] = ()

    @property
    def keys(self) -> set[str]:
        """set of str: every citation key defined."""
        return {entry.key for entry in self.entries}

    def by_key(self) -> dict[str, list[BibEntry]]:
        """Group entries by key.

        Returns
        -------
        dict
            Key to the list of entries declaring it; lists longer than one
            mean the key is defined more than once.
        """
        grouped: dict[str, list[BibEntry]] = {}
        for entry in self.entries:
            grouped.setdefault(entry.key, []).append(entry)
        return grouped

    def errors(self) -> tuple[BibProblem, ...]:
        """tuple of BibProblem: only the problems whose severity is ``error``."""
        return tuple(item for item in self.problems if item.severity == "error")


def _skip_space(text: str, index: int) -> int:
    while index < len(text) and text[index] in " \t\r\n":
        index += 1
    return index


def _read_delimited(text: str, index: int, opening: str, closing: str) -> tuple[str, int] | None:
    """Read a balanced ``{...}`` or ``(...)`` block."""
    if index >= len(text) or text[index] != opening:
        return None
    depth = 0
    cursor = index
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return text[index + 1 : cursor], cursor + 1
        cursor += 1
    return None


def _read_quoted(text: str, index: int) -> tuple[str, int] | None:
    """Read a ``"..."`` value, allowing braced groups to hide quotes."""
    if index >= len(text) or text[index] != '"':
        return None
    depth = 0
    cursor = index + 1
    while cursor < len(text):
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(depth - 1, 0)
        elif char == '"' and depth == 0:
            return text[index + 1 : cursor], cursor + 1
        cursor += 1
    return None


def _read_value(text: str, index: int, strings: Mapping[str, str]) -> tuple[str, int]:
    """Read one field value, resolving ``#`` concatenation and ``@string`` macros."""
    parts: list[str] = []
    cursor = _skip_space(text, index)
    while cursor < len(text):
        char = text[cursor]
        if char == "{":
            block = _read_delimited(text, cursor, "{", "}")
            if block is None:
                break
            parts.append(block[0])
            cursor = block[1]
        elif char == '"':
            quoted = _read_quoted(text, cursor)
            if quoted is None:
                break
            parts.append(quoted[0])
            cursor = quoted[1]
        else:
            token = re.match(r"[^,\s#}\)]+", text[cursor:])
            if token is None:
                break
            raw = token.group(0)
            parts.append(strings.get(raw.lower(), raw))
            cursor += len(raw)
        cursor = _skip_space(text, cursor)
        if cursor < len(text) and text[cursor] == "#":
            cursor = _skip_space(text, cursor + 1)
            continue
        break
    return "".join(parts), cursor


def _normalise(value: str) -> str:
    """Collapse whitespace and strip outer padding."""
    return re.sub(r"\s+", " ", value).strip()


def _split_fields(body: str, strings: Mapping[str, str]) -> tuple[str, list[tuple[str, str, int]]]:
    """Split an entry body into its key and ``(name, raw_value, offset)`` triples.

    Parameters
    ----------
    body : str
        Everything between the entry's outer delimiters.
    strings : mapping of str to str
        ``@string`` macros defined so far, so that ``publisher = acme``
        resolves instead of being stored as the literal word.

    Returns
    -------
    key : str
        The citation key.
    fields : list of tuple
        ``(name, value, offset)`` for each field, in file order.
    """
    cursor = _skip_space(body, 0)
    key_match = re.match(r"[^,\s]+", body[cursor:])
    key = key_match.group(0).strip() if key_match else ""
    cursor += len(key)
    cursor = _skip_space(body, cursor)

    fields: list[tuple[str, str, int]] = []
    while cursor < len(body):
        if body[cursor] == ",":
            cursor = _skip_space(body, cursor + 1)
            continue
        name_match = re.match(r"[A-Za-z][A-Za-z0-9_:.\-+/]*", body[cursor:])
        if name_match is None:
            break
        name = name_match.group(0).lower()
        offset = cursor
        cursor = _skip_space(body, cursor + len(name))
        if cursor >= len(body) or body[cursor] != "=":
            break
        cursor = _skip_space(body, cursor + 1)
        value, cursor = _read_value(body, cursor, strings)
        fields.append((name, value, offset))
        cursor = _skip_space(body, cursor)
    return key, fields


def _audit_entry(entry: BibEntry) -> list[BibProblem]:
    """Check one entry against the quality rules."""
    problems: list[BibProblem] = []
    entry_type = entry.entry_type

    if entry_type not in REQUIRED_FIELDS:
        problems.append(
            BibProblem(
                kind=BibProblemKind.UNKNOWN_ENTRY_TYPE,
                key=entry.key,
                message=f"Unknown entry type @{entry_type}.",
                path=entry.path,
                line=entry.line,
                severity="warning",
            )
        )

    for group in REQUIRED_FIELDS.get(entry_type, ()):
        if not any(entry.get(name).strip() for name in group):
            alternatives = " or ".join(group)
            problems.append(
                BibProblem(
                    kind=BibProblemKind.MISSING_FIELD,
                    key=entry.key,
                    message=f"@{entry_type} requires a non-empty '{alternatives}' field.",
                    path=entry.path,
                    line=entry.line,
                    severity="error",
                )
            )

    for name, value in entry.fields.items():
        if not value.strip():
            problems.append(
                BibProblem(
                    kind=BibProblemKind.EMPTY_FIELD,
                    key=entry.key,
                    message=f"Field '{name}' is present but empty.",
                    path=entry.path,
                    line=entry.line,
                    severity="warning",
                )
            )
            continue
        if name in _NAME_FIELDS:
            stripped = value.strip()
            if stripped.endswith(","):
                problems.append(
                    BibProblem(
                        kind=BibProblemKind.TRAILING_COMMA_IN_NAMES,
                        key=entry.key,
                        message=(
                            f"Field '{name}' ends with a comma; BibTeX reports 'has a comma "
                            "at the end' in the .blg, counts an error and drops the comma."
                        ),
                        path=entry.path,
                        line=entry.line,
                        severity="error",
                    )
                )
            elif re.search(r"\band\s*$", stripped):
                problems.append(
                    BibProblem(
                        kind=BibProblemKind.TRAILING_COMMA_IN_NAMES,
                        key=entry.key,
                        message=f"Field '{name}' ends with a dangling 'and'.",
                        path=entry.path,
                        line=entry.line,
                        severity="error",
                    )
                )

    year = entry.get("year").strip()
    if year:
        if not _YEAR_RE.search(year):
            problems.append(
                BibProblem(
                    kind=BibProblemKind.MALFORMED_YEAR,
                    key=entry.key,
                    message=f"Field 'year' contains no four-digit year: {year!r}.",
                    path=entry.path,
                    line=entry.line,
                    severity="error",
                )
            )
        elif len(year) > 16:
            problems.append(
                BibProblem(
                    kind=BibProblemKind.MALFORMED_YEAR,
                    key=entry.key,
                    message=(
                        "Field 'year' is long enough to be a whole citation "
                        f"({len(year)} characters); split it into proper fields."
                    ),
                    path=entry.path,
                    line=entry.line,
                    severity="error",
                )
            )
    return problems


def parse_bib_text(
    text: str,
    *,
    path: Path | None = None,
    strings: Mapping[str, str] | None = None,
) -> BibDatabase:
    """Parse BibTeX source held in a string.

    Parameters
    ----------
    text : str
        Database contents.
    path : pathlib.Path or None, optional
        Origin, recorded on entries and problems.
    strings : mapping of str to str or None, optional
        ``@string`` macros already in scope, i.e. the ones defined by the
        databases BibTeX read before this one.  The returned database
        carries these plus the macros this text defines.

    Returns
    -------
    BibDatabase
        Entries plus every defect found.  Only the outermost delimiters
        are removed from a value: inner braces are BibTeX's
        capitalisation protection and belong to the data.

    Notes
    -----
    Per-entry defects only.  Duplicate keys are a property of a whole
    database rather than of one entry, so they are reported by
    :func:`parse_bib` and :func:`parse_bib_files`; call
    :func:`detect_duplicate_keys` on the result to get them here too.

    Examples
    --------
    >>> db = parse_bib_text('@ARTICLE{a, author={X,}, title={T}, journal={J}, year={1999}}')
    >>> db.entries[0].key, db.entries[0].get("year")
    ('a', '1999')
    >>> [p.kind.value for p in db.problems]
    ['trailing-comma-in-names']

    A macro defined by an earlier database resolves here too:

    >>> db = parse_bib_text("@misc{m, publisher = acme}", strings={"acme": "Acme Ltd"})
    >>> db.entries[0].get("publisher")
    'Acme Ltd'
    """
    index = LineIndex(text)
    entries: list[BibEntry] = []
    problems: list[BibProblem] = []
    # Macros in scope: the ones inherited from earlier databases, plus the
    # ones this text defines as the scan reaches them.
    scope: dict[str, str] = dict(strings) if strings else {}

    cursor = 0
    length = len(text)
    while cursor < length:
        at_sign = text.find("@", cursor)
        if at_sign == -1:
            break
        type_match = re.match(r"[A-Za-z]+", text[at_sign + 1 :])
        if type_match is None:
            cursor = at_sign + 1
            continue
        entry_type = type_match.group(0).lower()
        after_type = _skip_space(text, at_sign + 1 + len(type_match.group(0)))
        if after_type >= length or text[after_type] not in "{(":
            cursor = at_sign + 1
            continue
        opening = text[after_type]
        closing = "}" if opening == "{" else ")"
        block = _read_delimited(text, after_type, opening, closing)
        line = index.line_of(at_sign)
        if block is None:
            problems.append(
                BibProblem(
                    kind=BibProblemKind.SYNTAX,
                    key="",
                    message=f"Unterminated @{entry_type} block.",
                    path=path,
                    line=line,
                    severity="error",
                )
            )
            break
        body, cursor = block

        if entry_type in _IGNORED_TYPES:
            continue
        if entry_type == "string":
            name, value = "", ""
            macro = re.match(r"\s*([A-Za-z][\w\-]*)\s*=", body)
            if macro is not None:
                name = macro.group(1).lower()
                value, _ = _read_value(body, macro.end(), scope)
            if name:
                scope[name] = _normalise(value)
            continue

        key, raw_fields = _split_fields(body, scope)
        if not key:
            problems.append(
                BibProblem(
                    kind=BibProblemKind.SYNTAX,
                    key="",
                    message=f"@{entry_type} entry has no citation key.",
                    path=path,
                    line=line,
                    severity="error",
                )
            )
            continue
        fields = {name: _normalise(value) for name, value, _ in raw_fields}
        entry = BibEntry(key=key, entry_type=entry_type, fields=fields, path=path, line=line)
        entries.append(entry)
        problems.extend(_audit_entry(entry))

    return BibDatabase(
        entries=tuple(entries),
        problems=tuple(problems),
        strings=scope,
        paths=(path,) if path is not None else (),
    )


def detect_duplicate_keys(entries: Sequence[BibEntry]) -> list[BibProblem]:
    """Report keys that are defined more than once.

    Parameters
    ----------
    entries : sequence of BibEntry
        Entries from one or several databases.

    Returns
    -------
    list of BibProblem
        One problem per repeated key.  The severity is ``warning`` when every
        definition carries identical content and ``error`` when they differ:
        BibTeX keeps the first definition it reads and mentions the others
        only as "Repeated entry" in the ``.blg``, so which text ends up in
        the reference list depends on the file order.

    Examples
    --------
    >>> source = "@misc{k, title={A}} @misc{k, title={B}}"
    >>> [p.kind.value for p in detect_duplicate_keys(parse_bib_text(source).entries)]
    ['conflicting-duplicate']
    """
    grouped: dict[str, list[BibEntry]] = {}
    for entry in entries:
        grouped.setdefault(entry.key, []).append(entry)

    problems: list[BibProblem] = []
    for key, duplicates in grouped.items():
        if len(duplicates) < 2:
            continue
        signatures = {entry.signature() for entry in duplicates}
        origins = ", ".join(
            f"{(entry.path.name if entry.path else '<string>')}:{entry.line}"
            for entry in duplicates
        )
        if len(signatures) == 1:
            problems.append(
                BibProblem(
                    kind=BibProblemKind.DUPLICATE_KEY,
                    key=key,
                    message=(
                        f"Key defined {len(duplicates)} times with identical content ({origins})."
                    ),
                    path=duplicates[-1].path,
                    line=duplicates[-1].line,
                    severity="warning",
                )
            )
        else:
            problems.append(
                BibProblem(
                    kind=BibProblemKind.CONFLICTING_DUPLICATE,
                    key=key,
                    message=(
                        f"Key defined {len(duplicates)} times with DIFFERENT content "
                        f"({origins}); BibTeX keeps the first and only notes 'Repeated "
                        "entry' in the .blg."
                    ),
                    path=duplicates[-1].path,
                    line=duplicates[-1].line,
                    severity="error",
                )
            )
    return problems


def _read_single(path: Path, *, strings: Mapping[str, str] | None = None) -> BibDatabase:
    """Read one ``.bib`` file without cross-entry duplicate detection."""
    if not path.is_file():
        return BibDatabase(
            problems=(
                BibProblem(
                    kind=BibProblemKind.SYNTAX,
                    key="",
                    message=f"Bibliography file not found: {path}",
                    path=path,
                    line=0,
                    severity="error",
                ),
            ),
            paths=(path,),
        )
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp950", "latin-1"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover - latin-1 never fails
        text = raw.decode("utf-8", errors="replace")
    return parse_bib_text(text, path=path, strings=strings)


def parse_bib(path: Path) -> BibDatabase:
    """Read and parse a single ``.bib`` file.

    Parameters
    ----------
    path : pathlib.Path
        The database file.

    Returns
    -------
    BibDatabase
        Parsed entries.  A missing file yields an empty database carrying a
        single ``syntax`` problem, so callers can report it uniformly
        instead of handling :class:`OSError`.
    """
    database = _read_single(Path(path))
    extra = detect_duplicate_keys(database.entries)
    if not extra:
        return database
    return BibDatabase(
        entries=database.entries,
        problems=database.problems + tuple(extra),
        strings=database.strings,
        paths=database.paths,
    )


def parse_bib_files(paths: Iterable[Path] | Sequence[Path]) -> BibDatabase:
    """Parse several databases and cross-check their keys.

    Parameters
    ----------
    paths : iterable of pathlib.Path
        Database files, in the order BibTeX would read them.

    Returns
    -------
    BibDatabase
        The merged database.  Keys defined more than once are reported as
        :attr:`BibProblemKind.DUPLICATE_KEY` when the definitions agree and
        :attr:`BibProblemKind.CONFLICTING_DUPLICATE`, an error, when they do
        not; a plain build leaves that to one line in the ``.blg``.

    Notes
    -----
    The order matters: ``@string`` macros accumulate as the files are
    read, so a macro defined in the first database resolves in the second,
    as it does in BibTeX.  Parsing each file on its own would leave
    ``publisher = acme`` in the second file stored as the literal word
    ``acme``.
    """
    entries: list[BibEntry] = []
    problems: list[BibProblem] = []
    strings: dict[str, str] = {}
    used: list[Path] = []

    for path in paths:
        database = _read_single(Path(path), strings=strings)
        entries.extend(database.entries)
        problems.extend(database.problems)
        strings.update(database.strings)
        used.extend(database.paths)

    problems.extend(detect_duplicate_keys(entries))

    _LOG.info("Parsed %d bibliography entries from %d file(s)", len(entries), len(used))
    return BibDatabase(
        entries=tuple(entries),
        problems=tuple(problems),
        strings=strings,
        paths=tuple(used),
    )
