"""Classifier for the ``.log`` file a TeX run leaves behind.

A thesis log runs to thousands of lines and only a few of them matter.
The engine can exit zero and produce a PDF while the log still holds
overfull boxes, undefined references and a substituted font.  Those
messages are picked out here, classified and counted, so a build can fail
on them instead of passing with them left in the log.

Line wrapping
-------------
TeX breaks log lines at ``max_print_line`` characters (79 by default), in
the middle of file names and messages.  Lines of exactly that length are
joined back together before matching, otherwise any message that crosses a
break would be missed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from ..logging_utils import get_logger

__all__ = [
    "LogMessage",
    "LogReport",
    "MessageKind",
    "Severity",
    "parse_log",
    "parse_log_text",
    "parse_missfont",
    "summarise",
]

_LOG = get_logger(__name__)

_DEFAULT_PRINT_WIDTH: Final[int] = 79


class Severity(str, Enum):
    """How badly a log message should be taken."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class MessageKind(str, Enum):
    """Categories of log message this parser recognises."""

    ERROR = "error"
    UNDEFINED_REFERENCE = "undefined-reference"
    UNDEFINED_CITATION = "undefined-citation"
    MULTIPLY_DEFINED = "multiply-defined-label"
    OVERFULL_HBOX = "overfull-hbox"
    UNDERFULL_HBOX = "underfull-hbox"
    OVERFULL_VBOX = "overfull-vbox"
    MISSING_FONT = "missing-font"
    MISSING_CHARACTER = "missing-character"
    MISSING_FILE = "missing-file"
    RERUN_NEEDED = "rerun-needed"
    FONT_WARNING = "font-warning"
    PACKAGE_WARNING = "package-warning"
    LATEX_WARNING = "latex-warning"


@dataclass(frozen=True, slots=True)
class LogMessage:
    """One classified line (or group of lines) from a TeX log.

    Attributes
    ----------
    kind : MessageKind
        What sort of message it is.
    severity : Severity
        Whether it should fail a build.
    message : str
        Human-readable text, whitespace-normalised.
    file : str or None
        Source file the engine blamed, when it named one.
    line : int or None
        Source line, when known.
    amount : float or None
        How far an overfull box overflows, in points; for an underfull box,
        its badness.
    """

    kind: MessageKind
    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None
    amount: float | None = None

    def location(self) -> str:
        """Return ``file:line``, or ``"-"`` when the engine named neither.

        Returns
        -------
        str
            Location string suitable for a report table.
        """
        if self.file is None:
            return "-"
        return f"{self.file}:{self.line}" if self.line is not None else self.file


@dataclass(frozen=True, slots=True)
class LogReport:
    """What the log of one TeX run contained.

    Attributes
    ----------
    path : pathlib.Path or None
        The log file, when read from disk.
    messages : tuple of LogMessage
        Classified messages in file order.
    engine_banner : str
        First line of the log, which identifies the engine and format.
    missing_fonts : tuple of str
        Fonts the run had to generate with ``mktextfm``, in the order they
        first appeared.
    """

    path: Path | None = None
    messages: tuple[LogMessage, ...] = ()
    engine_banner: str = ""
    missing_fonts: tuple[str, ...] = ()

    def of_kind(self, kind: MessageKind) -> tuple[LogMessage, ...]:
        """Return every message of one kind.

        Parameters
        ----------
        kind : MessageKind
            Category to filter by.

        Returns
        -------
        tuple of LogMessage
            Matching messages, in file order.
        """
        return tuple(item for item in self.messages if item.kind is kind)

    @property
    def errors(self) -> tuple[LogMessage, ...]:
        """tuple of LogMessage: every message with error severity."""
        return tuple(item for item in self.messages if item.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[LogMessage, ...]:
        """tuple of LogMessage: every message with warning severity."""
        return tuple(item for item in self.messages if item.severity is Severity.WARNING)

    @property
    def has_errors(self) -> bool:
        """bool: whether the run produced at least one error."""
        return bool(self.errors)

    @property
    def rerun_needed(self) -> bool:
        """bool: whether LaTeX asked for another pass."""
        return bool(self.of_kind(MessageKind.RERUN_NEEDED))

    @property
    def worst_overfull(self) -> LogMessage | None:
        """LogMessage or None: the box that overflows by the most points."""
        boxes = [
            item
            for item in self.messages
            if item.kind in (MessageKind.OVERFULL_HBOX, MessageKind.OVERFULL_VBOX)
            and item.amount is not None
        ]
        if not boxes:
            return None
        return max(boxes, key=lambda item: item.amount or 0.0)

    def counts(self) -> dict[str, int]:
        """Tally the messages by kind.

        Returns
        -------
        dict of str to int
            Kind value to occurrence count, sorted by kind.
        """
        tally: dict[str, int] = {}
        for item in self.messages:
            tally[item.kind.value] = tally.get(item.kind.value, 0) + 1
        return dict(sorted(tally.items()))


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------
_FILE_LINE_ERROR: Final[re.Pattern[str]] = re.compile(
    r"^(?P<file>(?:[A-Za-z]:)?[^:\n]+?\.[A-Za-z0-9_+-]{1,12}):(?P<line>\d+):\s*(?P<message>.+)$"
)
"""The shape ``-file-line-error`` gives every error: ``path.ext:line: text``.

The path must carry an extension, which keeps prose such as
``Chapter 3: 15: ...`` out of the error list; every file TeX can blame has
one, because TeX appends ``.tex`` to an extensionless ``\\input``.
"""

_FILE_LINE_NOT_AN_ERROR: Final[re.Pattern[str]] = re.compile(
    r"^(?:(?:LaTeX|pdfTeX|XeTeX|LuaTeX|Font)(?:\s+Font)?|(?:Package|Class|Module)\s+\S+)"
    r"\s+(?:Warning|Info)\b"
)
"""Messages that carry a ``file:line:`` prefix but are not failures."""

_BANG_ERROR: Final[re.Pattern[str]] = re.compile(r"^!\s*(?P<message>.+)$")
_OVERFULL_HBOX: Final[re.Pattern[str]] = re.compile(
    r"^Overfull \\hbox \((?P<amount>[\d.]+)pt too wide\)(?P<where>.*)$"
)
_UNDERFULL_HBOX: Final[re.Pattern[str]] = re.compile(
    r"^Underfull \\hbox \(badness (?P<badness>\d+)\)(?P<where>.*)$"
)
_OVERFULL_VBOX: Final[re.Pattern[str]] = re.compile(
    r"^Overfull \\vbox \((?P<amount>[\d.]+)pt too high\)(?P<where>.*)$"
)
_AT_LINES: Final[re.Pattern[str]] = re.compile(r"at lines? (?P<first>\d+)(?:--(?P<last>\d+))?")
_UNDEFINED_REFERENCE: Final[re.Pattern[str]] = re.compile(
    r"^LaTeX Warning: Reference [`'\"](?P<name>[^'\"]+)['\"] on page \S+ undefined"
    r"(?: on input line (?P<line>\d+))?"
)
_UNDEFINED_CITATION: Final[re.Pattern[str]] = re.compile(
    r"^(?:LaTeX|Package \w+) Warning: Citation [`'\"](?P<name>[^'\"]+)['\"]"
    r".*?undefined(?: on input line (?P<line>\d+))?"
)
_MULTIPLY_DEFINED: Final[re.Pattern[str]] = re.compile(
    r"^LaTeX Warning: Label [`'\"](?P<name>[^'\"]+)['\"] multiply defined"
)
_RERUN: Final[re.Pattern[str]] = re.compile(
    r"^(?:LaTeX|Package \w+) Warning:.*[Rr]erun(?: to get| LaTeX| to)?"
)
_MISSING_CHARACTER: Final[re.Pattern[str]] = re.compile(r"^Missing character: (?P<message>.+)$")
_MKTEXTFM: Final[re.Pattern[str]] = re.compile(r"(?:kpathsea: )?Running mktextfm\s+(?P<font>\S+)")
_FONT_NOT_LOADABLE: Final[re.Pattern[str]] = re.compile(
    r"Font \\?\S+ (?:not loadable|not found)|Font shape [`'\"][^'\"]+['\"] undefined"
)
_MISSING_FILE: Final[re.Pattern[str]] = re.compile(
    r"^(?:No file (?P<nofile>\S+)\.|.*?File [`'\"](?P<name>[^'\"]+)['\"] not found)"
)
_PACKAGE_WARNING: Final[re.Pattern[str]] = re.compile(
    r"^Package (?P<package>\w+) Warning: (?P<message>.+)$"
)
_LATEX_WARNING: Final[re.Pattern[str]] = re.compile(r"^LaTeX Warning: (?P<message>.+)$")
_FONT_WARNING: Final[re.Pattern[str]] = re.compile(r"^LaTeX Font Warning: (?P<message>.+)$")


def _unwrap(lines: Sequence[str], width: int = _DEFAULT_PRINT_WIDTH) -> list[str]:
    """Re-join lines that TeX broke at *width* characters.

    Parameters
    ----------
    lines : sequence of str
        Raw log lines, already stripped of line endings.
    width : int, optional
        TeX's ``max_print_line``.

    Returns
    -------
    list of str
        Lines with hard wraps undone.
    """
    joined: list[str] = []
    buffer = ""
    for line in lines:
        buffer += line
        if len(line) == width:
            continue
        joined.append(buffer)
        buffer = ""
    if buffer:
        joined.append(buffer)
    return joined


def _normalise(text: str) -> str:
    """Collapse whitespace in a message."""
    return re.sub(r"\s+", " ", text).strip()


def _where_line(where: str) -> int | None:
    """Extract the first source line from an ``at lines 12--15`` tail."""
    match = _AT_LINES.search(where)
    if match is None:
        return None
    return int(match.group("first"))


def parse_log_text(
    text: str,
    *,
    path: Path | None = None,
    print_width: int = _DEFAULT_PRINT_WIDTH,
) -> LogReport:
    """Classify the contents of a TeX log.

    Parameters
    ----------
    text : str
        Raw log contents.
    path : pathlib.Path or None, optional
        Origin, recorded on the report.
    print_width : int, optional
        TeX's ``max_print_line``; pass ``0`` to disable unwrapping.

    Returns
    -------
    LogReport
        Classified messages plus the engine banner.

    Examples
    --------
    >>> report = parse_log_text(
    ...     "This is XeTeX\\n"
    ...     "Overfull \\\\hbox (30.14pt too wide) in paragraph at lines 365--367\\n"
    ...     "LaTeX Warning: Reference `fig:x' on page 5 undefined on input line 100.\\n"
    ...     "./body.tex:12: Undefined control sequence.\\n"
    ... )
    >>> report.has_errors, report.worst_overfull.amount
    (True, 30.14)
    >>> report.counts()["undefined-reference"]
    1
    """
    raw_lines = text.splitlines()
    lines = _unwrap(raw_lines, print_width) if print_width > 0 else list(raw_lines)
    banner = _normalise(raw_lines[0]) if raw_lines else ""

    messages: list[LogMessage] = []
    missing_fonts: list[str] = []

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            continue

        match = _OVERFULL_HBOX.match(stripped)
        if match is not None:
            messages.append(
                LogMessage(
                    kind=MessageKind.OVERFULL_HBOX,
                    severity=Severity.WARNING,
                    message=_normalise(stripped),
                    line=_where_line(match.group("where")),
                    amount=float(match.group("amount")),
                )
            )
            continue

        match = _OVERFULL_VBOX.match(stripped)
        if match is not None:
            messages.append(
                LogMessage(
                    kind=MessageKind.OVERFULL_VBOX,
                    severity=Severity.WARNING,
                    message=_normalise(stripped),
                    line=_where_line(match.group("where")),
                    amount=float(match.group("amount")),
                )
            )
            continue

        match = _UNDERFULL_HBOX.match(stripped)
        if match is not None:
            messages.append(
                LogMessage(
                    kind=MessageKind.UNDERFULL_HBOX,
                    severity=Severity.INFO,
                    message=_normalise(stripped),
                    line=_where_line(match.group("where")),
                    amount=float(match.group("badness")),
                )
            )
            continue

        match = _UNDEFINED_REFERENCE.match(stripped)
        if match is not None:
            raw_line = match.group("line")
            messages.append(
                LogMessage(
                    kind=MessageKind.UNDEFINED_REFERENCE,
                    severity=Severity.WARNING,
                    message=_normalise(stripped),
                    line=int(raw_line) if raw_line else None,
                )
            )
            continue

        match = _UNDEFINED_CITATION.match(stripped)
        if match is not None:
            raw_line = match.group("line")
            messages.append(
                LogMessage(
                    kind=MessageKind.UNDEFINED_CITATION,
                    severity=Severity.WARNING,
                    message=_normalise(stripped),
                    line=int(raw_line) if raw_line else None,
                )
            )
            continue

        match = _MULTIPLY_DEFINED.match(stripped)
        if match is not None:
            messages.append(
                LogMessage(
                    kind=MessageKind.MULTIPLY_DEFINED,
                    severity=Severity.WARNING,
                    message=_normalise(stripped),
                )
            )
            continue

        match = _MKTEXTFM.search(stripped)
        if match is not None:
            font = match.group("font")
            if font not in missing_fonts:
                missing_fonts.append(font)
            messages.append(
                LogMessage(
                    kind=MessageKind.MISSING_FONT,
                    severity=Severity.WARNING,
                    message=f"Font {font} is being generated on the fly (mktextfm); "
                    "the installed font set is incomplete.",
                )
            )
            continue

        if _FONT_NOT_LOADABLE.search(stripped):
            messages.append(
                LogMessage(
                    kind=MessageKind.MISSING_FONT,
                    severity=Severity.WARNING,
                    message=_normalise(stripped),
                )
            )
            continue

        match = _MISSING_CHARACTER.match(stripped)
        if match is not None:
            messages.append(
                LogMessage(
                    kind=MessageKind.MISSING_CHARACTER,
                    severity=Severity.WARNING,
                    message=_normalise(stripped),
                )
            )
            continue

        match = _FILE_LINE_ERROR.match(stripped)
        if match is not None and not stripped.startswith(("Package ", "LaTeX ", "Class ")):
            body = match.group("message")
            # Under -file-line-error everything the engine prints in
            # `file:line:` form is an error.  Matching on wordings such as
            # "Error" or "Undefined control sequence" would miss common
            # ones like "Missing $ inserted." and "Too many }'s.", and a
            # failed build would come out with no diagnosis at all.
            if not _FILE_LINE_NOT_AN_ERROR.match(body):
                messages.append(
                    LogMessage(
                        kind=MessageKind.ERROR,
                        severity=Severity.ERROR,
                        message=_normalise(body.lstrip("! ")),
                        file=match.group("file"),
                        line=int(match.group("line")),
                    )
                )
                continue

        match = _BANG_ERROR.match(stripped)
        if match is not None:
            messages.append(
                LogMessage(
                    kind=MessageKind.ERROR,
                    severity=Severity.ERROR,
                    message=_normalise(match.group("message")),
                )
            )
            continue

        match = _MISSING_FILE.match(stripped)
        if match is not None and (match.group("nofile") or match.group("name")):
            name = match.group("nofile") or match.group("name")
            messages.append(
                LogMessage(
                    kind=MessageKind.MISSING_FILE,
                    severity=Severity.WARNING,
                    message=f"File not found: {name}",
                )
            )
            continue

        if _RERUN.match(stripped):
            messages.append(
                LogMessage(
                    kind=MessageKind.RERUN_NEEDED,
                    severity=Severity.WARNING,
                    message=_normalise(stripped),
                )
            )
            continue

        match = _FONT_WARNING.match(stripped)
        if match is not None:
            messages.append(
                LogMessage(
                    kind=MessageKind.FONT_WARNING,
                    severity=Severity.WARNING,
                    message=_normalise(match.group("message")),
                )
            )
            continue

        match = _PACKAGE_WARNING.match(stripped)
        if match is not None:
            messages.append(
                LogMessage(
                    kind=MessageKind.PACKAGE_WARNING,
                    severity=Severity.WARNING,
                    message=_normalise(f"{match.group('package')}: {match.group('message')}"),
                )
            )
            continue

        match = _LATEX_WARNING.match(stripped)
        if match is not None:
            messages.append(
                LogMessage(
                    kind=MessageKind.LATEX_WARNING,
                    severity=Severity.WARNING,
                    message=_normalise(match.group("message")),
                )
            )
            continue

    return LogReport(
        path=path,
        messages=tuple(messages),
        engine_banner=banner,
        missing_fonts=tuple(missing_fonts),
    )


def _decode(raw: bytes) -> str:
    """Decode log bytes as UTF-8, then Big5 (cp950), then Latin-1."""
    for encoding in ("utf-8", "cp950", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")  # pragma: no cover - latin-1 never fails


def parse_log(path: Path, *, print_width: int = _DEFAULT_PRINT_WIDTH) -> LogReport:
    """Read and classify a ``.log`` file.

    Parameters
    ----------
    path : pathlib.Path
        The log file.
    print_width : int, optional
        TeX's ``max_print_line``.

    Returns
    -------
    LogReport
        Classified messages, or an empty report when the file is absent.
    """
    path = Path(path)
    if not path.is_file():
        _LOG.debug("No log file at %s", path)
        return LogReport(path=path)
    return parse_log_text(_decode(path.read_bytes()), path=path, print_width=print_width)


def parse_missfont(path: Path) -> tuple[str, ...]:
    """Read the ``missfont.log`` kpathsea leaves when it could not generate a font.

    Parameters
    ----------
    path : pathlib.Path
        Path to ``missfont.log``.

    Returns
    -------
    tuple of str
        Distinct font-building command lines, order preserved.  Empty when
        the file does not exist, which is the healthy case.

    Notes
    -----
    The engine can substitute a font and still exit zero, so this file may
    be the only sign that the PDF came out wrong.
    """
    path = Path(path)
    if not path.is_file():
        return ()
    seen: dict[str, None] = {}
    for line in _decode(path.read_bytes()).splitlines():
        entry = line.strip()
        if entry:
            seen.setdefault(entry, None)
    return tuple(seen)


def summarise(reports: Iterable[LogReport]) -> Mapping[str, int]:
    """Merge the tallies of several reports.

    Parameters
    ----------
    reports : iterable of LogReport
        Reports to combine.

    Returns
    -------
    mapping of str to int
        Kind value to total occurrence count.
    """
    total: dict[str, int] = {}
    for report in reports:
        for kind, count in report.counts().items():
            total[kind] = total.get(kind, 0) + count
    return dict(sorted(total.items()))
