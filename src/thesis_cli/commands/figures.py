r"""The ``figures`` command: resolve every graphic the way LaTeX would.

It reports three kinds of problem:

1. Case mismatches.  ``\includegraphics{figures/case_1_test.eps}`` against
   ``Case_1_test.eps`` on disk compiles on Windows (NTFS), then fails as
   soon as the project reaches a Linux CI runner or a case-sensitive macOS
   volume.  So resolution never asks the filesystem whether a path exists.
   It lists the directory with :meth:`pathlib.Path.iterdir` and compares
   the requested name with the listing as a string, which gives the same
   answer on every platform.
2. Missing files, including references written without an extension, which
   LaTeX resolves by probing a list of extensions in order.  The probe
   order here is the one in ``thesis.toml``.
3. Unused graphics: files in the figure directories that no source
   references.  Files written by ``epstopdf`` are build output rather than
   assets, so they are left out of that list.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

from ..config import ThesisConfig, WatermarkMode
from ..errors import ProjectLayoutError
from ..logging_utils import get_logger
from ..parsers.tex_scanner import GraphicRef, TexDocument, scan_document
from ..report import CommandReport, Finding, ReportTable, Severity

__all__ = [
    "DirectoryIndex",
    "FigureResolution",
    "FigureStatus",
    "audit_figures",
    "resolve_reference",
]

_LOG = get_logger(__name__)

_GRAPHICS_EXTENSIONS_ALWAYS: tuple[str, ...] = (".pdf", ".png", ".jpg", ".jpeg", ".eps", ".tif")
# epstopdf converts plot.eps into plot-eps-converted-to.pdf.
_EPSTOPDF_SUFFIX = "-eps-converted-to.pdf"


class FigureStatus(str, Enum):
    """Outcome of resolving one graphics reference.

    ``MACRO`` means the file name is produced by a LaTeX macro rather
    than written literally, as the watermark is.  Such a reference
    cannot be resolved without running TeX, so it is reported as
    information and checked by ``build`` instead.
    """

    OK = "ok"
    CASE_MISMATCH = "case-mismatch"
    MISSING = "missing"
    MACRO = "macro"


@dataclass(frozen=True, slots=True)
class FigureResolution:
    """The result of resolving one reference.

    Attributes
    ----------
    reference : GraphicRef
        The reference as written in the source.
    status : FigureStatus
        Whether it resolves, resolves only case-insensitively, or not at all.
    resolved : pathlib.Path or None
        The file that would be used, when one was found.
    actual_name : str or None
        For a case mismatch, the requested path as it is spelled on disk,
        directories included.
    searched : tuple of str
        Paths that were probed, for the diagnostic message.
    """

    reference: GraphicRef
    status: FigureStatus
    resolved: Path | None = None
    actual_name: str | None = None
    searched: tuple[str, ...] = ()


class DirectoryIndex:
    """Caches exact directory listings for case-sensitive comparison.

    ``Path.exists()`` asks the filesystem, and on Windows and default macOS
    volumes that comparison ignores case.  Listing the directory once and
    comparing strings gives the same answer everywhere, which is what the
    whole audit relies on.

    A lowercase key can map to *several* real names at once: a
    case-sensitive filesystem can hold ``Plot.eps`` and ``plot.eps`` side by
    side.  Every spelling is kept, so that an exact match is never hidden by
    a sibling that differs only in case.
    """

    __slots__ = ("_cache",)

    def __init__(self) -> None:
        self._cache: dict[Path, dict[str, tuple[str, ...]]] = {}

    def entries(self, directory: Path) -> dict[str, tuple[str, ...]]:
        """Return ``{lowercased name: every real spelling}`` for *directory*.

        Parameters
        ----------
        directory : pathlib.Path
            Directory to list.

        Returns
        -------
        dict of str to tuple of str
            Empty when the directory does not exist.  Each value holds the
            on-disk spellings sharing that lowercased name, sorted, so the
            result does not depend on directory iteration order.
        """
        cached = self._cache.get(directory)
        if cached is not None:
            return cached
        try:
            names = [item.name for item in directory.iterdir()]
        except OSError:
            names = []
        listing: dict[str, list[str]] = {}
        for name in names:
            listing.setdefault(name.lower(), []).append(name)
        frozen = {key: tuple(sorted(values)) for key, values in listing.items()}
        self._cache[directory] = frozen
        return frozen

    def contains_exact(self, directory: Path, name: str) -> bool:
        """Return ``True`` when *name* appears in *directory* byte for byte."""
        return name in self.entries(directory).get(name.lower(), ())

    def actual_case(self, directory: Path, name: str) -> str | None:
        """Return the on-disk spelling of *name*, or ``None`` when absent.

        When several spellings differ only in case, the first in sorted
        order is reported; the exact match is handled by
        :meth:`contains_exact` before this is ever consulted.
        """
        matches = self.entries(directory).get(name.lower(), ())
        return matches[0] if matches else None


def _normalise_reference(raw: str) -> PurePosixPath:
    """Turn a LaTeX graphics argument into a relative POSIX path."""
    text = raw.strip().strip('"').replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return PurePosixPath(text)


def _walk_parents(
    base: Path, parts: tuple[str, ...], listings: DirectoryIndex
) -> tuple[Path, tuple[str, ...], bool] | None:
    """Follow the directory part of a reference one component at a time.

    Parameters
    ----------
    base : pathlib.Path
        Directory the reference is resolved against.
    parts : tuple of str
        Directory components of the reference, as written.
    listings : DirectoryIndex
        Listing cache used for the comparison.

    Returns
    -------
    tuple or None
        The directory reached, its components as spelled on disk, and
        whether every component matched exactly.  ``None`` when some
        component is absent under any spelling.
    """
    directory = base
    spelled: list[str] = []
    exact = True
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            directory = directory.parent
            spelled.append(part)
            continue
        name = part if listings.contains_exact(directory, part) else None
        if name is None:
            name = listings.actual_case(directory, part)
            if name is None:
                return None
            exact = False
        directory = directory / name
        spelled.append(name)
    return directory, tuple(spelled), exact


def resolve_reference(
    reference: GraphicRef,
    *,
    root: Path,
    search_dirs: tuple[Path, ...],
    extensions: tuple[str, ...],
    index: DirectoryIndex | None = None,
) -> FigureResolution:
    r"""Resolve one graphics reference against the filesystem.

    Parameters
    ----------
    reference : GraphicRef
        Reference as written in the source.
    root : pathlib.Path
        Directory the TeX engine runs in; searched first, exactly as LaTeX
        resolves a relative path.
    search_dirs : tuple of pathlib.Path
        Additional directories, standing in for ``\graphicspath``.
    extensions : tuple of str
        Probe order for references written without a suffix.
    index : DirectoryIndex or None, optional
        Shared listing cache; one is created when omitted.

    Returns
    -------
    FigureResolution
        Where the file was found, or why it was not.

    Notes
    -----
    A reference that names a graphics extension resolves only to that exact
    file: graphicx does not swap ``plot.png`` for ``plot.pdf``.  The one
    substitute is the ``plot-eps-converted-to.pdf`` that ``epstopdf`` writes
    for ``plot.eps``, which pdfLaTeX uses when it is present.

    Every directory in the path is compared as well as the file name, so
    ``Figures/plot.png`` against a ``figures/`` folder is a case mismatch.
    """
    listings = index if index is not None else DirectoryIndex()
    if "\\" in reference.raw:
        return FigureResolution(reference=reference, status=FigureStatus.MACRO)
    relative = _normalise_reference(reference.raw)
    stem_with_suffix = relative.name
    suffix = relative.suffix.lower()
    has_known_suffix = suffix in set(extensions) | set(_GRAPHICS_EXTENSIONS_ALWAYS)

    if has_known_suffix:
        names = [stem_with_suffix]
        if suffix == ".eps":
            names.append(relative.stem + _EPSTOPDF_SUFFIX)
    else:
        names = [stem_with_suffix + extension for extension in extensions]
        names.append(stem_with_suffix)

    bases = [root, *search_dirs]
    searched: list[str] = []
    case_hit: tuple[Path, str] | None = None

    for base in bases:
        walked = _walk_parents(base, relative.parent.parts, listings)
        if walked is None:
            searched.extend((base / relative.parent / name).as_posix() for name in names)
            continue
        directory, spelled_parent, parent_exact = walked
        for name in names:
            candidate = directory / name
            searched.append(candidate.as_posix())
            name_exact = listings.contains_exact(directory, name)
            if parent_exact and name_exact:
                return FigureResolution(
                    reference=reference,
                    status=FigureStatus.OK,
                    resolved=candidate,
                    searched=tuple(searched),
                )
            if case_hit is None:
                actual = name if name_exact else listings.actual_case(directory, name)
                if actual is not None:
                    shown = PurePosixPath(*spelled_parent, actual).as_posix()
                    case_hit = (directory / actual, shown)

    if case_hit is not None:
        return FigureResolution(
            reference=reference,
            status=FigureStatus.CASE_MISMATCH,
            resolved=case_hit[0],
            actual_name=case_hit[1],
            searched=tuple(searched),
        )

    return FigureResolution(
        reference=reference,
        status=FigureStatus.MISSING,
        searched=tuple(searched),
    )


def _is_derived(name: str, derived_suffixes: tuple[str, ...]) -> bool:
    """Return ``True`` when *name* looks like generated graphics output."""
    lowered = name.lower()
    return any(lowered.endswith(suffix.lower()) for suffix in derived_suffixes)


def audit_figures(
    config: ThesisConfig,
    *,
    document: TexDocument | None = None,
    report_unused: bool = True,
) -> CommandReport:
    """Check that every referenced graphic exists and that none are orphaned.

    Parameters
    ----------
    config : ThesisConfig
        Project configuration.
    document : TexDocument or None, optional
        Previously scanned document.
    report_unused : bool, optional
        Also list files in the figure directories that nothing references.

    Returns
    -------
    CommandReport
        One finding per unresolved or mis-cased reference, plus a table of
        every reference and its status.

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

    index = DirectoryIndex()
    search_dirs = config.figure_dirs
    extensions = tuple(config.figures.extensions)
    derived = tuple(config.figures.derived_suffixes)

    resolutions: list[FigureResolution] = []
    used: set[Path] = set()
    for reference in document.graphics:
        resolution = resolve_reference(
            reference,
            root=config.root,
            search_dirs=search_dirs,
            extensions=extensions,
            index=index,
        )
        resolutions.append(resolution)
        if resolution.resolved is not None:
            used.add(resolution.resolved.resolve())

    findings: list[Finding] = []
    for resolution in resolutions:
        location = resolution.reference.location.render(config.root)
        if resolution.status is FigureStatus.CASE_MISMATCH:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    kind="figure-case-mismatch",
                    location=location,
                    message=(
                        f"\\{resolution.reference.macro} requests "
                        f"'{resolution.reference.raw}' but the path on disk is "
                        f"'{resolution.actual_name}'."
                    ),
                    detail=(
                        "This compiles on Windows and on a default macOS volume and fails on "
                        "any case-sensitive filesystem, including most Linux CI runners."
                    ),
                )
            )
        elif resolution.status is FigureStatus.MACRO:
            findings.append(
                Finding(
                    severity=Severity.INFO,
                    kind="figure-macro-path",
                    location=location,
                    message=(
                        f"\\{resolution.reference.macro} builds its file name from a "
                        f"macro ('{resolution.reference.raw}'); it cannot be resolved "
                        "without running TeX."
                    ),
                )
            )
        elif resolution.status is FigureStatus.MISSING:
            findings.append(
                Finding(
                    severity=Severity.ERROR,
                    kind="figure-missing",
                    location=location,
                    message=(
                        f"\\{resolution.reference.macro} requests "
                        f"'{resolution.reference.raw}', which was not found."
                    ),
                    detail="Searched: " + ", ".join(resolution.searched[:8]),
                )
            )

    # The watermark is referenced through a macro, so the scanner cannot
    # see it; take it from the configuration instead, or it would be
    # reported as an unused file on every run.
    if config.template.watermark is not WatermarkMode.OFF:
        watermark = config.watermark_path
        if watermark.is_file():
            used.add(watermark.resolve())
        else:
            for sibling in watermark.parent.glob(watermark.stem + ".*"):
                if sibling.is_file():
                    used.add(sibling.resolve())

    unused_rows: list[tuple[str, ...]] = []
    if report_unused:
        # figure_dirs are resolved; a root given as a relative path would
        # make relative_to() below raise.
        root = config.root.resolve()
        for directory in search_dirs:
            if not directory.is_dir():
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        kind="figure-dir-missing",
                        location=str(directory),
                        message="Configured figure directory does not exist.",
                    )
                )
                continue
            for candidate in sorted(directory.iterdir(), key=lambda item: item.name):
                if not candidate.is_file():
                    continue
                if _is_derived(candidate.name, derived):
                    continue
                if candidate.suffix.lower() not in set(extensions) | set(
                    _GRAPHICS_EXTENSIONS_ALWAYS
                ):
                    continue
                if candidate.resolve() in used:
                    continue
                size_kb = candidate.stat().st_size / 1024
                shown = candidate.relative_to(root).as_posix()
                unused_rows.append((shown, f"{size_kb:.1f}"))
                findings.append(
                    Finding(
                        severity=Severity.WARNING,
                        kind="figure-unused",
                        location=shown,
                        message="Graphics file is never referenced by the document.",
                    )
                )

    reference_rows = tuple(
        (
            resolution.reference.location.render(config.root),
            f"\\{resolution.reference.macro}",
            resolution.reference.raw,
            resolution.status.value,
            (
                resolution.resolved.relative_to(config.root).as_posix()
                if resolution.resolved is not None
                and resolution.resolved.is_relative_to(config.root)
                else (str(resolution.resolved) if resolution.resolved else "-")
            ),
        )
        for resolution in resolutions
    )

    tables: list[ReportTable] = [
        ReportTable(
            title="Graphics references",
            columns=("source", "macro", "requested", "status", "resolved"),
            rows=reference_rows,
        )
    ]
    if unused_rows:
        tables.append(
            ReportTable(
                title="Unreferenced graphics",
                columns=("file", "KiB"),
                rows=tuple(unused_rows),
                notes=(
                    "Files produced by epstopdf are excluded; 'thesis-cli clean' removes those.",
                ),
            )
        )

    status_counts: dict[str, int] = {}
    for resolution in resolutions:
        status_counts[resolution.status.value] = status_counts.get(resolution.status.value, 0) + 1

    summary = {
        "references": len(resolutions),
        "ok": status_counts.get(FigureStatus.OK.value, 0),
        "case_mismatch": status_counts.get(FigureStatus.CASE_MISMATCH.value, 0),
        "missing": status_counts.get(FigureStatus.MISSING.value, 0),
        "macro_paths": status_counts.get(FigureStatus.MACRO.value, 0),
        "unused": len(unused_rows),
        "search_path": [str(config.root), *(str(item) for item in search_dirs)],
    }
    _LOG.info(
        "Resolved %d graphics reference(s): %s",
        len(resolutions),
        ", ".join(f"{key}={value}" for key, value in sorted(status_counts.items())),
    )
    return CommandReport(
        command="figures",
        findings=tuple(findings),
        tables=tuple(tables),
        summary=summary,
    )
