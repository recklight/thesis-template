"""Identification and safe removal of LaTeX build artefacts.

Deleting files automatically is the one operation in this toolkit that can
destroy work, so the rules are narrow and written out here rather than
implied by a glob:

1. Containment: a candidate must lie inside the project root.  Paths are
   resolved before the check, so a symbolic link pointing outside is
   rejected rather than followed.
2. Provenance: an artefact is only removable while the source file that
   produced it is present.  ``thesis.aux`` may go because ``thesis.tex``
   exists; an orphan ``notes.aux`` with no ``notes.tex`` is left alone,
   because nothing shows a TeX run created it.  In the build output
   directory the source is not a sibling: ``build/thesis.aux`` is matched
   to ``thesis.tex`` in the project root, ``build/chapters/ch1.aux`` to
   ``chapters/ch1.tex``, and ``build/coverbridge.aux`` to the configured
   spine source.
3. Sources are never candidates.  Authored formats (``.tex``, ``.cls``,
   ``.sty``, ``.bib``, ``.bst`` and every image format) are protected
   whatever the configuration says.
4. Outputs only on request: the finished PDF is removed only when asked
   for.

The plan is always computed first and can be inspected without touching the
filesystem, which is what ``--dry-run`` shows.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from ..config import ThesisConfig
from ..errors import ConfigError
from ..logging_utils import get_logger

__all__ = [
    "PROTECTED_EXTENSIONS",
    "ArtifactReason",
    "Candidate",
    "CleanPlan",
    "execute_clean",
    "iter_candidates",
    "plan_clean",
]

_LOG = get_logger(__name__)

PROTECTED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".tex",
        ".ltx",
        ".cls",
        ".sty",
        ".bst",
        ".bib",
        ".clo",
        ".def",
        ".cfg",
        ".png",
        ".jpg",
        ".jpeg",
        ".eps",
        ".svg",
        ".tif",
        ".tiff",
        ".gif",
        ".toml",
        ".md",
        ".py",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
    }
)
"""Extensions that are never deletion candidates, whatever the configuration says."""

_SOURCE_EXTENSIONS: Final[tuple[str, ...]] = (".tex", ".ltx", ".dtx", ".ins")


class ArtifactReason(str, Enum):
    """Why a file was selected for removal."""

    ENGINE_ARTIFACT = "engine-artifact"
    DERIVED_GRAPHIC = "derived-graphic"
    EDITOR_BACKUP = "editor-backup"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One file proposed for deletion.

    Attributes
    ----------
    path : pathlib.Path
        Absolute path of the file.
    reason : ArtifactReason
        Rule that selected it.
    source : pathlib.Path or None
        The source file that proves the candidate is generated, when the
        rule requires one.
    size : int
        Size in bytes at planning time.
    """

    path: Path
    reason: ArtifactReason
    source: Path | None = None
    size: int = 0

    def relative_to(self, root: Path) -> str:
        """Render the path relative to *root* for display.

        Parameters
        ----------
        root : pathlib.Path
            Project root.

        Returns
        -------
        str
            POSIX-style relative path, or the absolute path when the file
            lies outside *root*.
        """
        try:
            return self.path.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover - containment is checked earlier
            return self.path.as_posix()


@dataclass(frozen=True, slots=True)
class CleanPlan:
    """The set of files ``clean`` proposes to remove."""

    root: Path
    candidates: tuple[Candidate, ...] = ()
    skipped: tuple[tuple[Path, str], ...] = ()

    @property
    def total_bytes(self) -> int:
        """int: combined size of every candidate."""
        return sum(item.size for item in self.candidates)

    def by_reason(self) -> dict[str, int]:
        """Count candidates per rule.

        Returns
        -------
        dict of str to int
            Reason value to number of files.
        """
        tally: dict[str, int] = {}
        for item in self.candidates:
            tally[item.reason.value] = tally.get(item.reason.value, 0) + 1
        return dict(sorted(tally.items()))


def _is_contained(path: Path, root: Path) -> bool:
    """Return ``True`` when *path* resolves inside *root*."""
    try:
        resolved = path.resolve()
    except OSError:  # pragma: no cover - broken link
        return False
    return resolved == root or resolved.is_relative_to(root)


def _matching_extension(name: str, extensions: Sequence[str]) -> str | None:
    """Return the configured extension *name* ends with, longest match first.

    Compound suffixes such as ``.synctex.gz`` and ``.run.xml`` must beat the
    plain ``.gz``/``.xml`` they contain, hence the length ordering.
    """
    lowered = name.lower()
    best: str | None = None
    for extension in extensions:
        if lowered.endswith(extension) and (best is None or len(extension) > len(best)):
            best = extension
    return best


def _source_for(
    path: Path,
    extension: str,
    *,
    root: Path | None = None,
    output_dirs: Sequence[Path] = (),
    build_sources: Mapping[str, Path] | None = None,
) -> Path | None:
    """Find the source file that would produce *path*.

    Parameters
    ----------
    path : pathlib.Path
        The artefact.
    extension : str
        The artefact extension that matched.
    root : pathlib.Path or None, optional
        Project root, needed to look past an output directory.
    output_dirs : sequence of pathlib.Path, optional
        Build output directories inside *root*.
    build_sources : mapping of str to pathlib.Path or None, optional
        Job name (the stem) to the document ``build`` compiles under that
        name: the main document and the spine.

    Returns
    -------
    pathlib.Path or None
        The sibling ``.tex``-like file with the same stem; for a file in an
        output directory, the source at the same relative position under
        *root*, or the build source whose job name it carries.  ``None``
        when there is no such file.
    """
    stem = path.name[: -len(extension)]
    if not stem:
        return None
    for source_extension in _SOURCE_EXTENSIONS:
        candidate = path.with_name(stem + source_extension)
        if candidate.is_file():
            return candidate
    if root is None:
        return None
    # -output-directory sends every artefact into the output directory, so
    # the source sits elsewhere: \include'd chapters keep their relative
    # path, and the job itself is named after the main or spine source.
    for output_dir in output_dirs:
        if not path.parent.is_relative_to(output_dir):
            continue
        relative_parent = path.parent.relative_to(output_dir)
        for source_extension in _SOURCE_EXTENSIONS:
            candidate = root / relative_parent / (stem + source_extension)
            if candidate.is_file():
                return candidate
        if relative_parent == Path() and build_sources and stem in build_sources:
            return build_sources[stem]
    return None


def _build_sources(config: ThesisConfig) -> dict[str, Path]:
    """Map each job name ``build`` uses to the document compiled under it."""
    sources: dict[str, Path] = {}
    for attribute in ("main_path", "spine_path"):
        try:
            source = getattr(config, attribute)
        except ConfigError:  # pragma: no cover - load_config rejects these first
            continue
        if source.is_file():
            sources.setdefault(source.stem, source)
    return sources


def iter_candidates(
    config: ThesisConfig,
    *,
    include_outputs: bool | None = None,
    extra_roots: Iterable[Path] = (),
) -> Iterator[tuple[Candidate | None, tuple[Path, str] | None]]:
    """Walk the project and yield deletion candidates and skip reasons.

    Parameters
    ----------
    config : ThesisConfig
        Project configuration supplying the extension and pattern lists.
    include_outputs : bool or None, optional
        Whether the finished PDF counts as removable.  Defaults to
        ``config.clean.remove_outputs``.
    extra_roots : iterable of pathlib.Path, optional
        Additional directories to scan, used for out-of-tree build
        directories.

    Yields
    ------
    tuple
        ``(candidate, skipped)`` where exactly one element is non-``None``.
    """
    root = config.root.resolve()
    extensions = tuple(config.clean.extensions)
    derived = tuple(config.figures.derived_suffixes)
    patterns = tuple(config.clean.extra_patterns)
    remove_outputs = config.clean.remove_outputs if include_outputs is None else include_outputs
    main_stem = Path(config.main).stem

    scan_roots: list[Path] = [root]
    for extra in extra_roots:
        resolved = Path(extra).resolve()
        if resolved != root and _is_contained(resolved, root) and resolved.is_dir():
            scan_roots.append(resolved)
    output_dirs = tuple(scan_roots[1:])
    build_sources = _build_sources(config)

    def source_of(path: Path, extension: str) -> Path | None:
        return _source_for(
            path, extension, root=root, output_dirs=output_dirs, build_sources=build_sources
        )

    # Where a PDF may be the compiled thesis rather than a figure.  Without
    # this, a graphic that shares the main document's stem (say
    # 'figures/thesis.pdf' in a project whose main file is 'thesis.tex')
    # would be taken for the build output and deleted by 'clean --outputs'.
    output_locations = {scan_root.resolve() for scan_root in scan_roots}

    visited: set[Path] = set()
    for scan_root in scan_roots:
        for dirpath, dirnames, filenames in os.walk(scan_root):
            directory = Path(dirpath)
            dirnames[:] = [
                name for name in dirnames if name not in {".git", ".venv", "__pycache__"}
            ]
            for filename in filenames:
                path = directory / filename
                if path in visited:
                    continue
                visited.add(path)

                if not _is_contained(path, root):
                    yield None, (path, "outside the project root")
                    continue
                try:
                    size = path.stat().st_size
                except OSError:  # pragma: no cover - races
                    continue

                derived_suffix = next(
                    (suffix for suffix in derived if filename.lower().endswith(suffix.lower())),
                    None,
                )
                if derived_suffix is not None:
                    stem = filename[: -len(derived_suffix)]
                    origin = path.with_name(stem + ".eps")
                    yield (
                        Candidate(
                            path=path,
                            reason=ArtifactReason.DERIVED_GRAPHIC,
                            source=origin if origin.is_file() else None,
                            size=size,
                        ),
                        None,
                    )
                    continue

                suffix = path.suffix.lower()
                if suffix in PROTECTED_EXTENSIONS:
                    # Rule 3: authored formats are never candidates, and no
                    # configured pattern can promote them into candidates.
                    continue

                if suffix == ".pdf":
                    if not remove_outputs:
                        continue
                    is_main_output = (
                        path.stem == main_stem and directory.resolve() in output_locations
                    )
                    if not is_main_output and source_of(path, ".pdf") is None:
                        yield None, (path, "not a build output of the configured main document")
                        continue
                    yield (
                        Candidate(path=path, reason=ArtifactReason.OUTPUT, size=size),
                        None,
                    )
                    continue

                extension = _matching_extension(filename, extensions)
                if extension is not None:
                    source = source_of(path, extension)
                    if source is None:
                        yield None, (path, f"no source file matching {path.stem}.tex")
                        continue
                    yield (
                        Candidate(
                            path=path,
                            reason=ArtifactReason.ENGINE_ARTIFACT,
                            source=source,
                            size=size,
                        ),
                        None,
                    )
                    continue

                if any(fnmatch.fnmatch(filename, pattern) for pattern in patterns):
                    yield (
                        Candidate(path=path, reason=ArtifactReason.EDITOR_BACKUP, size=size),
                        None,
                    )
                    continue


def plan_clean(
    config: ThesisConfig,
    *,
    include_outputs: bool | None = None,
) -> CleanPlan:
    """Compute, without deleting anything, which files ``clean`` would remove.

    Parameters
    ----------
    config : ThesisConfig
        Project configuration.
    include_outputs : bool or None, optional
        Override for ``config.clean.remove_outputs``.

    Returns
    -------
    CleanPlan
        Candidates and the files that were deliberately spared, with the
        reason each was spared.

    Examples
    --------
    >>> import tempfile, pathlib
    >>> from thesis_cli.config import ThesisConfig
    >>> with tempfile.TemporaryDirectory() as tmp:
    ...     root = pathlib.Path(tmp)
    ...     _ = (root / "thesis.tex").write_text("x")
    ...     _ = (root / "thesis.aux").write_text("x")
    ...     _ = (root / "orphan.aux").write_text("x")
    ...     plan = plan_clean(ThesisConfig.default(root))
    ...     sorted(c.path.name for c in plan.candidates)
    ['thesis.aux']
    """
    root = config.root.resolve()
    candidates: list[Candidate] = []
    skipped: list[tuple[Path, str]] = []
    extra_roots: list[Path] = []
    try:
        extra_roots.append(config.output_dir)
    except ConfigError:  # pragma: no cover - misconfigured output directory
        _LOG.debug("build.output_dir is not usable; scanning the project root only")

    for candidate, skip in iter_candidates(
        config, include_outputs=include_outputs, extra_roots=extra_roots
    ):
        if candidate is not None:
            candidates.append(candidate)
        elif skip is not None:
            skipped.append(skip)

    candidates.sort(key=lambda item: item.path.as_posix())
    skipped.sort(key=lambda item: item[0].as_posix())
    _LOG.info(
        "Clean plan: %d file(s), %.1f KiB", len(candidates), sum(c.size for c in candidates) / 1024
    )
    return CleanPlan(root=root, candidates=tuple(candidates), skipped=tuple(skipped))


def execute_clean(
    plan: CleanPlan, *, dry_run: bool = False
) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Delete the files in *plan*.

    Parameters
    ----------
    plan : CleanPlan
        A plan produced by :func:`plan_clean`.
    dry_run : bool, optional
        Report what would happen without touching the filesystem.

    Returns
    -------
    removed : list of pathlib.Path
        Files deleted (or that would have been, under *dry_run*).
    failures : list of tuple
        ``(path, message)`` for files that could not be deleted.

    Notes
    -----
    Containment is re-checked immediately before each deletion rather than
    trusted from planning time, so a path that changed underneath the tool
    between the two steps cannot escape the project root.
    """
    removed: list[Path] = []
    failures: list[tuple[Path, str]] = []
    root = plan.root.resolve()

    for candidate in plan.candidates:
        if not _is_contained(candidate.path, root):  # pragma: no cover - defensive
            failures.append((candidate.path, "refusing to delete outside the project root"))
            continue
        if dry_run:
            removed.append(candidate.path)
            continue
        try:
            candidate.path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            failures.append((candidate.path, str(exc)))
            continue
        _LOG.debug("Removed %s", candidate.path)
        removed.append(candidate.path)

    if not dry_run:
        _LOG.info("Removed %d file(s)", len(removed))
    return removed, failures
