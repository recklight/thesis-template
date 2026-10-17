"""Project configuration: the ``thesis.toml`` schema and its dataclasses.

Paths, extension lists, the engine and the other settings the commands
depend on are read from ``thesis.toml``.  Paths arrive as strings, are
resolved against the project root, and are rejected if they are absolute or
lead out of the root, so the package itself names no directory on any
machine.

Examples
--------
>>> from pathlib import Path
>>> cfg = ThesisConfig.default(Path("."))
>>> cfg.build.engine is Engine.XELATEX
True
>>> cfg.figures.extensions[0]
'.pdf'
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path, PureWindowsPath
from typing import Any, Final

from .errors import ConfigError
from .logging_utils import get_logger

__all__ = [
    "CONFIG_FILENAME",
    "DEFAULT_ARTIFACT_EXTENSIONS",
    "DEFAULT_FIGURE_MACROS",
    "DEFAULT_LABEL_MACROS",
    "DEFAULT_LATEX_PACKAGES",
    "BibConfig",
    "BuildConfig",
    "CleanConfig",
    "DepsConfig",
    "Engine",
    "FiguresConfig",
    "StatsConfig",
    "TemplateConfig",
    "ThesisConfig",
    "TocStyle",
    "WatermarkMode",
    "find_config_file",
    "load_config",
    "resolve_in_root",
]

_LOG = get_logger(__name__)

CONFIG_FILENAME: Final[str] = "thesis.toml"


class Engine(str, Enum):
    """Supported TeX toolchains.

    ``latex+dvipdfmx`` is supported because it is the fastest route for a
    long thesis: ``dvipdfmx`` embeds the fonts once, at the end, while
    ``pdflatex`` embeds them on every pass.  LuaLaTeX is not on the list:
    ``config/cjk.tex`` sets up Chinese with xeCJK, which runs under XeTeX
    only.
    """

    XELATEX = "xelatex"
    PDFLATEX = "pdflatex"
    LATEX_DVIPDFMX = "latex+dvipdfmx"

    @property
    def is_unicode_engine(self) -> bool:
        """bool: ``True`` for engines with native Unicode font handling."""
        return self is Engine.XELATEX

    @property
    def tex_program(self) -> str:
        """str: name of the executable that processes the ``.tex`` source."""
        return "latex" if self is Engine.LATEX_DVIPDFMX else self.value


class TocStyle(str, Enum):
    """Chapter numbering style used in the table of contents.

    Each member maps to one ``ThesisTocStyle*`` macro in
    ``template/config/chapter_numbering.tex``, selected from ``thesis.toml``,
    so switching styles never means editing the LaTeX source.
    """

    ARABIC = "arabic"
    CJK = "cjk"
    CJK_FULL = "cjk_full"

    @property
    def macro(self) -> str:
        """str: name of the LaTeX macro that installs this style."""
        return {
            TocStyle.ARABIC: "ThesisTocStyleArabic",
            TocStyle.CJK: "ThesisTocStyleCJK",
            TocStyle.CJK_FULL: "ThesisTocStyleCJKFull",
        }[self]


class WatermarkMode(str, Enum):
    """Where the watermark is stamped."""

    ALL = "all"
    SELECTED = "selected"
    OFF = "off"


DEFAULT_FIGURE_MACROS: Final[Mapping[str, int]] = {
    # macro name (without backslash) -> 1-based index of the mandatory
    # argument that holds the graphics file name.
    "includegraphics": 1,
    "fig": 2,
    "figt": 2,
    "figtr": 2,
    "hfig": 2,
    "hfigt": 2,
    "hfigtr": 2,
    "mpfig": 2,
    "mpfigt": 2,
    "mpfigtr": 2,
    "mpfigabc": 2,
    "mpfiga": 3,
    "mpfigtra": 3,
}
"""Mapping of figure macro name to the position of its file-name argument.

``includegraphics`` takes the file in its first mandatory argument; the
``fig`` family takes a width first, so the file is the second; the
alignment-aware ``mpfiga``/``mpfigtra`` push it to the third.
"""

DEFAULT_LABEL_MACROS: Final[Mapping[str, int]] = {
    # macro name -> 1-based index of the mandatory argument that becomes
    # a \label. Without this table a label created inside a figure macro
    # is invisible to the audit and every \ref to it looks undefined.
    "fig": 4,
    "figt": 4,
    "figtr": 4,
    "hfig": 4,
    "hfigt": 4,
    "hfigtr": 4,
    "mpfig": 4,
    "mpfigt": 4,
    "mpfigtr": 4,
    "mpfiga": 5,
    "mpfigtra": 5,
}
"""Mapping of figure macro name to the position of its label argument."""

DEFAULT_ARTIFACT_EXTENSIONS: Final[tuple[str, ...]] = (
    ".aux",
    ".bbl",
    ".bcf",
    ".blg",
    ".brf",
    ".dvi",
    ".fdb_latexmk",
    ".fls",
    ".idx",
    ".ilg",
    ".ind",
    ".loa",
    ".lof",
    ".log",
    ".lot",
    ".nav",
    ".out",
    ".ps",
    ".run.xml",
    ".snm",
    ".spl",
    ".synctex.gz",
    ".toc",
    ".vrb",
    ".xdv",
)
"""Extensions produced by a TeX run and never authored by a human."""

DEFAULT_LATEX_PACKAGES: Final[tuple[str, ...]] = (
    "iftex",
    "geometry",
    "graphicx",
    "fancyhdr",
    "cite",
    "amsmath",
    "amssymb",
    "mathrsfs",
    "listings",
    "url",
    "hyperref",
    "morefloats",
    "caption",
    "subcaption",
    "setspace",
    "epstopdf",
    "rotating",
    "xcolor",
)

UNICODE_ENGINE_PACKAGES: Final[tuple[str, ...]] = ("xeCJK",)
LEGACY_CJK_PACKAGES: Final[tuple[str, ...]] = ("CJKutf8", "CJKnumb")


def resolve_in_root(root: Path, raw: str | os.PathLike[str], *, what: str) -> Path:
    """Resolve *raw* against *root*, refusing absolute or escaping paths.

    Parameters
    ----------
    root : pathlib.Path
        Project root; assumed already absolute.
    raw : str or os.PathLike
        The path as written in ``thesis.toml``.  Must be relative.
    what : str
        Name of the setting, used in error messages.

    Returns
    -------
    pathlib.Path
        Absolute path inside *root*.

    Raises
    ------
    ConfigError
        If *raw* is absolute, empty, or resolves outside *root*.

    Notes
    -----
    ``clean`` depends on this: with every configured path inside the project
    root, a deletion candidate can never name a file outside the project, so
    the command is safe to run unattended.
    """
    text = str(raw).strip()
    if not text:
        raise ConfigError(f"{what} must not be empty.")
    candidate = Path(text)
    if _looks_absolute(text):
        raise ConfigError(
            f"{what} must be a path relative to the project root, got {text!r}.",
            hint="Absolute paths make a project impossible to move or share; "
            "write e.g. 'figures' instead of a drive-qualified path.",
        )
    resolved = (root / candidate).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        raise ConfigError(
            f"{what} points outside the project root: {text!r}.",
            hint=f"Keep every configured path under {root_resolved}.",
        )
    return resolved


def _looks_absolute(text: str) -> bool:
    """Return ``True`` for a path that is absolute on *any* platform.

    Parameters
    ----------
    text : str
        A path as written in ``thesis.toml``.

    Returns
    -------
    bool
        Whether the path is rooted, drive-qualified, or a UNC share.

    Notes
    -----
    ``Path("/etc").is_absolute()`` is ``False`` on Windows, because the path
    carries no drive letter.  Validating with the host platform's own rules
    would accept a configuration on one machine and reject it on another,
    which is the very portability problem this check is for.

    Examples
    --------
    >>> [_looks_absolute(p) for p in ("/etc", "C:/x", "figures", "a/b")]
    [True, True, False, False]
    """
    if text.startswith(("/", "\\")):
        return True
    if len(text) >= 2 and text[1] == ":" and text[0].isalpha():
        return True
    return PureWindowsPath(text).is_absolute()


def _as_mapping(value: Any, *, what: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"[{what}] must be a table, got {type(value).__name__}.")
    return dict(value)


def _check_keys(section: str, table: Mapping[str, Any], allowed: Sequence[str]) -> None:
    unknown = sorted(set(table) - set(allowed))
    if unknown:
        raise ConfigError(
            f"Unknown key(s) in [{section}]: {', '.join(unknown)}.",
            hint=f"Recognised keys are: {', '.join(sorted(allowed))}.",
        )


def _get_str(table: Mapping[str, Any], key: str, default: str, *, what: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ConfigError(f"{what}.{key} must be a string, got {type(value).__name__}.")
    return value


def _get_bool(table: Mapping[str, Any], key: str, default: bool, *, what: str) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{what}.{key} must be true or false.")
    return value


def _get_int(
    table: Mapping[str, Any], key: str, default: int, *, what: str, minimum: int = 1
) -> int:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{what}.{key} must be an integer.")
    if value < minimum:
        raise ConfigError(f"{what}.{key} must be >= {minimum}, got {value}.")
    return value


def _get_str_list(
    table: Mapping[str, Any], key: str, default: Sequence[str], *, what: str
) -> tuple[str, ...]:
    value = table.get(key, list(default))
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise ConfigError(f"{what}.{key} must be a list of strings.")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ConfigError(f"{what}.{key} must contain only strings.")
        out.append(item)
    return tuple(out)


def _get_enum(table: Mapping[str, Any], key: str, enum_cls: Any, default: Any, *, what: str) -> Any:
    raw = table.get(key, default.value)
    if not isinstance(raw, str):
        raise ConfigError(f"{what}.{key} must be a string.")
    try:
        return enum_cls(raw)
    except ValueError:
        options = ", ".join(member.value for member in enum_cls)
        raise ConfigError(
            f"{what}.{key} = {raw!r} is not recognised.",
            hint=f"Valid values: {options}.",
        ) from None


def _normalise_extension(ext: str, *, what: str) -> str:
    text = ext.strip()
    if not text:
        raise ConfigError(f"{what} contains an empty extension.")
    if not text.startswith("."):
        text = "." + text
    return text.lower()


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildConfig:
    """How the document is compiled.

    Attributes
    ----------
    engine : Engine
        Toolchain used for the main document.
    use_latexmk : bool
        Delegate pass counting to ``latexmk``.  When ``False`` the manual
        sequence is used instead: one engine pass, ``bibtex``, then the
        remaining engine passes.
    max_passes : int
        Number of engine invocations in the manual sequence; all of them
        run, ``bibtex`` not included.  The default 4 gives engine, bibtex
        and three more engine passes.
    interaction : str
        Value for the engine's ``-interaction`` flag.
    halt_on_error : bool
        Stop at the first error instead of trying to continue.
    synctex : bool
        Write SyncTeX data, so an editor can jump between source and PDF.
    output_dir : str
        Directory (relative to the root) that receives build artefacts.
    spine_source : str
        Stand-alone document that produces the printed spine.
    """

    engine: Engine = Engine.XELATEX
    use_latexmk: bool = True
    max_passes: int = 4
    interaction: str = "nonstopmode"
    halt_on_error: bool = True
    synctex: bool = False
    output_dir: str = "build"
    spine_source: str = "structure/coverbridge.tex"


@dataclass(frozen=True, slots=True, kw_only=True)
class BibConfig:
    """Bibliography database locations and style."""

    files: tuple[str, ...] = ("bib/references.bib",)
    style: str = "ieeetr"


@dataclass(frozen=True, slots=True, kw_only=True)
class FiguresConfig:
    """Graphics search rules.

    Attributes
    ----------
    dirs : tuple of str
        Directories searched for graphics, in order.
    extensions : tuple of str
        Extension probe order for references written without a suffix,
        mirroring LaTeX's ``DeclareGraphicsExtensions``.
    derived_suffixes : tuple of str
        File-name suffixes that mark a *generated* graphic (``epstopdf``
        output).  Such files are build artefacts: never reported as unused,
        always offered to ``clean``.
    macros : dict of str to int
        Extra or overriding entries for :data:`DEFAULT_FIGURE_MACROS`.
    label_macros : dict of str to int
        Extra or overriding entries for :data:`DEFAULT_LABEL_MACROS`.
    """

    dirs: tuple[str, ...] = ("figures",)
    extensions: tuple[str, ...] = (".pdf", ".png", ".jpg", ".jpeg", ".eps")
    derived_suffixes: tuple[str, ...] = ("-eps-converted-to.pdf",)
    macros: Mapping[str, int] = field(default_factory=dict)
    label_macros: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class CleanConfig:
    """Which files ``clean`` may delete."""

    extensions: tuple[str, ...] = DEFAULT_ARTIFACT_EXTENSIONS
    extra_patterns: tuple[str, ...] = ("*.bak", "*.asv", "Thumbs.db", ".DS_Store")
    remove_outputs: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class TemplateConfig:
    """Template switches that ``build`` writes to ``config/generated.tex``."""

    toc_style: TocStyle = TocStyle.CJK_FULL
    watermark: WatermarkMode = WatermarkMode.ALL
    watermark_image: str = "figures/watermark_placeholder.png"
    watermark_width: str = "5.1cm"
    watermark_x: str = "-1"
    watermark_y: str = "-6.2"
    float_spec: str = "htbp"
    generated_file: str = "config/generated.tex"


@dataclass(frozen=True, slots=True, kw_only=True)
class DepsConfig:
    """What ``deps`` verifies before a build is attempted."""

    packages: tuple[str, ...] = DEFAULT_LATEX_PACKAGES
    cjk_font: str = "Noto Serif CJK TC"
    legacy_font_files: tuple[str, ...] = ("bkai00mp.tfm", "bsmi00lp.tfm")


@dataclass(frozen=True, slots=True, kw_only=True)
class StatsConfig:
    """Word-count behaviour."""

    chapter_dir: str = "chapters"
    content_dir: str = "content"


@dataclass(frozen=True, slots=True, kw_only=True)
class ThesisConfig:
    """Fully resolved project configuration.

    Attributes
    ----------
    root : pathlib.Path
        Absolute project root; every relative path resolves against it.
    main : str
        Main document, relative to *root*.
    source_file : pathlib.Path or None
        The ``thesis.toml`` this object came from, or ``None`` for defaults.
    """

    root: Path
    main: str = "thesis.tex"
    build: BuildConfig = field(default_factory=BuildConfig)
    bib: BibConfig = field(default_factory=BibConfig)
    figures: FiguresConfig = field(default_factory=FiguresConfig)
    clean: CleanConfig = field(default_factory=CleanConfig)
    template: TemplateConfig = field(default_factory=TemplateConfig)
    deps: DepsConfig = field(default_factory=DepsConfig)
    stats: StatsConfig = field(default_factory=StatsConfig)
    source_file: Path | None = None

    # -- derived paths ---------------------------------------------------
    @property
    def main_path(self) -> Path:
        """pathlib.Path: absolute path of the main document."""
        return resolve_in_root(self.root, self.main, what="main")

    @property
    def output_dir(self) -> Path:
        """pathlib.Path: absolute path of the build output directory."""
        return resolve_in_root(self.root, self.build.output_dir, what="build.output_dir")

    @property
    def spine_path(self) -> Path:
        """pathlib.Path: absolute path of the spine document."""
        return resolve_in_root(self.root, self.build.spine_source, what="build.spine_source")

    @property
    def bib_paths(self) -> tuple[Path, ...]:
        """tuple of pathlib.Path: absolute paths of the bibliography files."""
        return tuple(resolve_in_root(self.root, item, what="bib.files") for item in self.bib.files)

    @property
    def figure_dirs(self) -> tuple[Path, ...]:
        """tuple of pathlib.Path: absolute paths of the graphics directories."""
        return tuple(
            resolve_in_root(self.root, item, what="figures.dirs") for item in self.figures.dirs
        )

    @property
    def watermark_path(self) -> Path:
        """pathlib.Path: absolute path of the watermark image."""
        return resolve_in_root(
            self.root, self.template.watermark_image, what="template.watermark_image"
        )

    @property
    def generated_path(self) -> Path:
        """pathlib.Path: absolute path of the CLI-generated LaTeX settings file."""
        return resolve_in_root(
            self.root, self.template.generated_file, what="template.generated_file"
        )

    @property
    def figure_macros(self) -> dict[str, int]:
        """dict: merged built-in and user-supplied figure macro table."""
        merged = dict(DEFAULT_FIGURE_MACROS)
        merged.update(self.figures.macros)
        return merged

    @property
    def label_macros(self) -> dict[str, int]:
        """dict: merged built-in and user-supplied label macro table."""
        merged = dict(DEFAULT_LABEL_MACROS)
        merged.update(self.figures.label_macros)
        return merged

    @property
    def required_packages(self) -> tuple[str, ...]:
        """tuple of str: LaTeX packages the configured engine needs."""
        extra = (
            UNICODE_ENGINE_PACKAGES if self.build.engine.is_unicode_engine else LEGACY_CJK_PACKAGES
        )
        return tuple(dict.fromkeys(self.deps.packages + extra))

    # -- constructors ----------------------------------------------------
    @classmethod
    def default(cls, root: Path) -> ThesisConfig:
        """Build a configuration with every default in place.

        Parameters
        ----------
        root : pathlib.Path
            Project root.

        Returns
        -------
        ThesisConfig
            Defaults anchored at *root*.
        """
        return cls(root=Path(root).resolve())

    def with_overrides(self, **kwargs: Any) -> ThesisConfig:
        """Return a copy with top-level fields replaced.

        Parameters
        ----------
        **kwargs
            Field names of :class:`ThesisConfig`.

        Returns
        -------
        ThesisConfig
            A new, independent configuration object.
        """
        return replace(self, **kwargs)


def find_config_file(start: Path) -> Path | None:
    """Search *start* and its parents for ``thesis.toml``.

    Parameters
    ----------
    start : pathlib.Path
        Directory (or a file inside the directory) to start from.

    Returns
    -------
    pathlib.Path or None
        The first configuration file found, or ``None``.
    """
    here = Path(start).resolve()
    if here.is_file():
        here = here.parent
    for candidate in (here, *here.parents):
        probe = candidate / CONFIG_FILENAME
        if probe.is_file():
            return probe
    return None


def _parse_toml(path: Path) -> dict[str, Any]:
    try:
        import tomli
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ConfigError(
            "The 'tomli' package is required to read thesis.toml.",
            hint="Install it with: pip install tomli",
        ) from exc
    try:
        with path.open("rb") as handle:
            return tomli.load(handle)
    except OSError as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc
    except tomli.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc


def load_config(start: Path | None = None, *, required: bool = False) -> ThesisConfig:
    """Load ``thesis.toml``, falling back to defaults when absent.

    Parameters
    ----------
    start : pathlib.Path or None, optional
        Where to begin searching.  Defaults to the current directory.
    required : bool, optional
        Raise instead of falling back to defaults when no file is found.

    Returns
    -------
    ThesisConfig
        Validated configuration with the project root already resolved.

    Raises
    ------
    ConfigError
        The file is unreadable, malformed, or holds an unknown or
        ill-typed key.
    """
    origin = Path(start) if start is not None else Path.cwd()
    config_path = find_config_file(origin)
    if config_path is None:
        if required:
            raise ConfigError(
                f"No {CONFIG_FILENAME} found in {origin.resolve()} or any parent directory.",
                hint="Run 'thesis-cli init <dir>' to create a new project.",
            )
        root = origin if origin.is_dir() else origin.parent
        _LOG.debug("No %s found; using built-in defaults at %s", CONFIG_FILENAME, root)
        return ThesisConfig.default(root)

    root = config_path.parent.resolve()
    data = _parse_toml(config_path)
    _check_keys(
        CONFIG_FILENAME,
        data,
        ["project", "build", "bib", "figures", "clean", "template", "deps", "stats"],
    )

    project = _as_mapping(data.get("project"), what="project")
    _check_keys("project", project, ["main"])
    main = _get_str(project, "main", "thesis.tex", what="project")

    build_tbl = _as_mapping(data.get("build"), what="build")
    _check_keys(
        "build",
        build_tbl,
        [
            "engine",
            "use_latexmk",
            "max_passes",
            "interaction",
            "halt_on_error",
            "synctex",
            "output_dir",
            "spine_source",
        ],
    )
    build_cfg = BuildConfig(
        engine=_get_enum(build_tbl, "engine", Engine, Engine.XELATEX, what="build"),
        use_latexmk=_get_bool(build_tbl, "use_latexmk", True, what="build"),
        max_passes=_get_int(build_tbl, "max_passes", 4, what="build", minimum=1),
        interaction=_get_str(build_tbl, "interaction", "nonstopmode", what="build"),
        halt_on_error=_get_bool(build_tbl, "halt_on_error", True, what="build"),
        synctex=_get_bool(build_tbl, "synctex", False, what="build"),
        output_dir=_get_str(build_tbl, "output_dir", "build", what="build"),
        spine_source=_get_str(build_tbl, "spine_source", "structure/coverbridge.tex", what="build"),
    )

    bib_tbl = _as_mapping(data.get("bib"), what="bib")
    _check_keys("bib", bib_tbl, ["files", "style"])
    bib_cfg = BibConfig(
        files=_get_str_list(bib_tbl, "files", ("bib/references.bib",), what="bib"),
        style=_get_str(bib_tbl, "style", "ieeetr", what="bib"),
    )

    fig_tbl = _as_mapping(data.get("figures"), what="figures")
    _check_keys(
        "figures",
        fig_tbl,
        ["dirs", "extensions", "derived_suffixes", "macros", "label_macros"],
    )

    def _macro_table(table_name: str) -> dict[str, int]:
        raw = _as_mapping(fig_tbl.get(table_name), what=f"figures.{table_name}")
        parsed: dict[str, int] = {}
        for name, position in raw.items():
            if isinstance(position, bool) or not isinstance(position, int) or position < 1:
                raise ConfigError(
                    f"figures.{table_name}.{name} must be a 1-based argument index (>= 1)."
                )
            parsed[name.lstrip("\\")] = position
        return parsed

    macros = _macro_table("macros")
    label_macros = _macro_table("label_macros")
    fig_cfg = FiguresConfig(
        dirs=_get_str_list(fig_tbl, "dirs", ("figures",), what="figures"),
        extensions=tuple(
            _normalise_extension(item, what="figures.extensions")
            for item in _get_str_list(
                fig_tbl,
                "extensions",
                (".pdf", ".png", ".jpg", ".jpeg", ".eps"),
                what="figures",
            )
        ),
        derived_suffixes=_get_str_list(
            fig_tbl, "derived_suffixes", ("-eps-converted-to.pdf",), what="figures"
        ),
        macros=macros,
        label_macros=label_macros,
    )

    clean_tbl = _as_mapping(data.get("clean"), what="clean")
    _check_keys("clean", clean_tbl, ["extensions", "extra_patterns", "remove_outputs"])
    clean_cfg = CleanConfig(
        extensions=tuple(
            _normalise_extension(item, what="clean.extensions")
            for item in _get_str_list(
                clean_tbl, "extensions", DEFAULT_ARTIFACT_EXTENSIONS, what="clean"
            )
        ),
        extra_patterns=_get_str_list(
            clean_tbl,
            "extra_patterns",
            ("*.bak", "*.asv", "Thumbs.db", ".DS_Store"),
            what="clean",
        ),
        remove_outputs=_get_bool(clean_tbl, "remove_outputs", False, what="clean"),
    )

    tpl_tbl = _as_mapping(data.get("template"), what="template")
    _check_keys(
        "template",
        tpl_tbl,
        [
            "toc_style",
            "watermark",
            "watermark_image",
            "watermark_width",
            "watermark_x",
            "watermark_y",
            "float_spec",
            "generated_file",
        ],
    )
    tpl_cfg = TemplateConfig(
        toc_style=_get_enum(tpl_tbl, "toc_style", TocStyle, TocStyle.CJK_FULL, what="template"),
        watermark=_get_enum(
            tpl_tbl, "watermark", WatermarkMode, WatermarkMode.ALL, what="template"
        ),
        watermark_image=_get_str(
            tpl_tbl, "watermark_image", "figures/watermark_placeholder.png", what="template"
        ),
        watermark_width=_get_str(tpl_tbl, "watermark_width", "5.1cm", what="template"),
        watermark_x=_get_str(tpl_tbl, "watermark_x", "-1", what="template"),
        watermark_y=_get_str(tpl_tbl, "watermark_y", "-6.2", what="template"),
        float_spec=_get_str(tpl_tbl, "float_spec", "htbp", what="template"),
        generated_file=_get_str(tpl_tbl, "generated_file", "config/generated.tex", what="template"),
    )

    deps_tbl = _as_mapping(data.get("deps"), what="deps")
    _check_keys("deps", deps_tbl, ["packages", "cjk_font", "legacy_font_files"])
    deps_cfg = DepsConfig(
        packages=_get_str_list(deps_tbl, "packages", DEFAULT_LATEX_PACKAGES, what="deps"),
        cjk_font=_get_str(deps_tbl, "cjk_font", "Noto Serif CJK TC", what="deps"),
        legacy_font_files=_get_str_list(
            deps_tbl, "legacy_font_files", ("bkai00mp.tfm", "bsmi00lp.tfm"), what="deps"
        ),
    )

    stats_tbl = _as_mapping(data.get("stats"), what="stats")
    _check_keys("stats", stats_tbl, ["chapter_dir", "content_dir"])
    stats_cfg = StatsConfig(
        chapter_dir=_get_str(stats_tbl, "chapter_dir", "chapters", what="stats"),
        content_dir=_get_str(stats_tbl, "content_dir", "content", what="stats"),
    )

    config = ThesisConfig(
        root=root,
        main=main,
        build=build_cfg,
        bib=bib_cfg,
        figures=fig_cfg,
        clean=clean_cfg,
        template=tpl_cfg,
        deps=deps_cfg,
        stats=stats_cfg,
        source_file=config_path,
    )
    # Touch every derived path once so that an absolute or escaping path
    # fails here, while the message can still name the setting, rather than
    # halfway through a build that has already started writing files.
    _ = (
        config.main_path,
        config.output_dir,
        config.bib_paths,
        config.figure_dirs,
        config.spine_path,
        config.generated_path,
    )
    # The watermark image is only resolved when a watermark is drawn, so
    # checking it unconditionally would reject a 'watermark = "off"' project
    # that left the path blank.
    if config.template.watermark is not WatermarkMode.OFF:
        _ = config.watermark_path
    _LOG.debug("Loaded configuration from %s", config_path)
    return config
