r"""The ``init`` command: create a new thesis from the template.

``init`` copies the neutral template, writes a ``thesis.toml`` next to it,
and fills the front-matter placeholders from values given on the command
line or asked for interactively.  The template holds placeholders only, so
a new project starts with nobody's name, advisor or acknowledgements in it.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..config import CONFIG_FILENAME, ThesisConfig
from ..errors import ProjectLayoutError, ValidationError
from ..logging_utils import get_logger
from ..report import CommandReport, Finding, ReportTable, Severity

__all__ = [
    "NAME_FIELDS",
    "NameField",
    "apply_name_values",
    "escape_latex",
    "init_project",
    "locate_template_dir",
    "render_config_toml",
    "template_candidates",
]

_LOG = get_logger(__name__)

_RENEW_RE: Final[re.Pattern[str]] = re.compile(
    r"(\\renewcommand\s*\{\s*\\(?P<name>[A-Za-z]+)\s*\}\s*\{)"
    # A value may hold escaped characters (\%, \{) and one level of braces
    # (\textbackslash{}), which is what escape_latex() writes.
    r"(?P<value>(?:[^{}\\]|\\.|\{[^{}]*\})*)(\})"
)
_LATEX_SPECIALS: Final[Mapping[str, str]] = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "%": r"\%",
    "&": r"\&",
    "#": r"\#",
    "$": r"\$",
    "_": r"\_",
    "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
}
_TEMPLATE_ENV_VAR: Final[str] = "THESIS_TEMPLATE_DIR"
_SKIP_NAMES: Final[frozenset[str]] = frozenset({"__pycache__", ".git", ".DS_Store", "Thumbs.db"})


@dataclass(frozen=True, slots=True)
class NameField:
    """One front-matter placeholder that ``init`` can fill.

    Attributes
    ----------
    macro : str
        LaTeX macro name, without the backslash.
    prompt : str
        Question shown in interactive mode (Traditional Chinese).
    example : str
        Placeholder value shipped in the template.
    """

    macro: str
    prompt: str
    example: str


NAME_FIELDS: Final[tuple[NameField, ...]] = (
    NameField("cTitle", "論文中文題目", "論文中文題目"),
    NameField("eTitle", "論文英文題目", "THESIS TITLE IN ENGLISH"),
    NameField("myCname", "研究生中文姓名", "研究生姓名"),
    NameField("myEname", "研究生英文姓名", "Student Name"),
    NameField("advisorCnameA", "指導教授中文姓名", "指導教授姓名"),
    NameField("advisorEnameA", "指導教授英文姓名", "Advisor Name"),
    NameField("univCname", "學校中文名稱", "○○大學"),
    NameField("univEname", "學校英文名稱", "UNIVERSITY NAME"),
    NameField("deptCname", "系所中文名稱", "○○學系"),
    NameField("deptEname", "系所英文名稱", "Department Name"),
    NameField("cYear", "口試中華民國年", "一一四"),
    NameField("cMonth", "口試月份（中文）", "六"),
    NameField("eYear", "口試西元年", "2025"),
    NameField("eMonth", "口試月份（英文）", "June"),
)
"""Placeholders offered by interactive ``init``; every other macro in
``config/names.tex`` can still be edited by hand afterwards."""


def template_candidates(explicit: Path | None = None) -> list[Path]:
    """List the directories that may hold the LaTeX template, in priority order.

    Parameters
    ----------
    explicit : pathlib.Path or None, optional
        A directory given on the command line, which always comes first.

    Returns
    -------
    list of pathlib.Path
        Candidates, whether or not they exist.

    Notes
    -----
    The order is: the explicit argument, the ``THESIS_TEMPLATE_DIR``
    environment variable, the copy installed beside the package (a wheel
    carries the template as ``thesis_cli/_template``), the ``template/``
    directory of a source checkout, and finally one under the current
    directory.
    """
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    env_value = os.environ.get(_TEMPLATE_ENV_VAR)
    if env_value:
        candidates.append(Path(env_value))
    package_root = Path(__file__).resolve().parent.parent
    candidates.append(package_root / "_template")
    candidates.append(package_root.parent.parent / "template")
    candidates.append(Path.cwd() / "template")
    return candidates


def locate_template_dir(explicit: Path | None = None) -> Path:
    """Find the LaTeX template directory.

    Parameters
    ----------
    explicit : pathlib.Path or None, optional
        A directory given on the command line, which always wins.

    Returns
    -------
    pathlib.Path
        Directory containing ``thesis.tex`` and its supporting files.

    Raises
    ------
    ProjectLayoutError
        When no candidate from :func:`template_candidates` holds a
        ``thesis.tex``.
    """
    for candidate in template_candidates(explicit):
        if (candidate / "thesis.tex").is_file():
            _LOG.debug("Using template directory %s", candidate)
            return candidate.resolve()

    raise ProjectLayoutError(
        "Could not find the LaTeX template directory.",
        hint=(
            "Pass --template <dir>, or set "
            f"{_TEMPLATE_ENV_VAR} to the directory that contains thesis.tex."
        ),
    )


def escape_latex(value: str) -> str:
    r"""Escape the characters LaTeX treats as markup.

    Parameters
    ----------
    value : str
        Plain text, as typed after ``--set`` or at the interactive prompt.

    Returns
    -------
    str
        The same text with ``\ { } % & # $ _ ^ ~`` written so that LaTeX
        prints them.  An unescaped ``%`` would comment out the closing brace
        of the ``\renewcommand`` and ``&`` would stop the build.

    Examples
    --------
    >>> print(escape_latex("50% of R&D {draft}"))
    50\% of R\&D \{draft\}
    """
    return "".join(_LATEX_SPECIALS.get(char, char) for char in value)


def apply_name_values(text: str, values: Mapping[str, str]) -> str:
    r"""Substitute front-matter values into ``config/names.tex``.

    Parameters
    ----------
    text : str
        Contents of the template's ``names.tex``.
    values : mapping of str to str
        Macro name (without backslash) to its new value, as plain text;
        LaTeX special characters are escaped with :func:`escape_latex`.

    Returns
    -------
    str
        The file with matching ``\renewcommand`` values replaced and every
        comment left untouched.

    Examples
    --------
    >>> src = "\\renewcommand{\\cTitle}{論文中文題目}\n"
    >>> apply_name_values(src, {"cTitle": "室內定位研究"}).strip()
    '\\renewcommand{\\cTitle}{室內定位研究}'
    """

    def _replace(match: re.Match[str]) -> str:
        name = match.group("name")
        if name not in values:
            return match.group(0)
        return f"{match.group(1)}{escape_latex(values[name])}{match.group(4)}"

    return _RENEW_RE.sub(_replace, text)


def parse_assignments(assignments: Sequence[str]) -> dict[str, str]:
    """Turn ``--set name=value`` arguments into a mapping.

    Parameters
    ----------
    assignments : sequence of str
        Strings of the form ``macro=value``.

    Returns
    -------
    dict of str to str
        Parsed values.

    Raises
    ------
    ValidationError
        When an argument has no ``=``, or names an unknown macro.
    """
    known = {field.macro for field in NAME_FIELDS}
    values: dict[str, str] = {}
    for item in assignments:
        if "=" not in item:
            raise ValidationError(
                f"--set expects name=value, got {item!r}.",
                hint="For example: --set cTitle=室內定位研究",
            )
        name, _, value = item.partition("=")
        name = name.strip().lstrip("\\")
        if name not in known:
            raise ValidationError(
                f"Unknown front-matter field {name!r}.",
                hint="Known fields: " + ", ".join(sorted(known)),
            )
        values[name] = value.strip()
    return values


def render_config_toml(config: ThesisConfig | None = None) -> str:
    """Render a commented ``thesis.toml`` for a new project.

    Parameters
    ----------
    config : ThesisConfig or None, optional
        Values to write; defaults are used when omitted.

    Returns
    -------
    str
        File contents, with a short comment above every key.
    """
    cfg = config if config is not None else ThesisConfig.default(Path())
    extensions = ", ".join(f'"{item}"' for item in cfg.figures.extensions)
    bib_files = ", ".join(f'"{item}"' for item in cfg.bib.files)
    figure_dirs = ", ".join(f'"{item}"' for item in cfg.figures.dirs)
    patterns = ", ".join(f'"{item}"' for item in cfg.clean.extra_patterns)
    return f"""# thesis.toml -- project settings read by thesis-cli.
# Every path is relative to this file's directory.

[project]
# Main document. 'thesis-cli build' refuses any file without a preamble.
main = "{cfg.main}"

[build]
# One of: xelatex, pdflatex, latex+dvipdfmx
engine = "{cfg.build.engine.value}"
# latexmk decides how many passes the cross-references need.
use_latexmk = {str(cfg.build.use_latexmk).lower()}
# Engine passes in the manual sequence used when latexmk is absent.
max_passes = {cfg.build.max_passes}
interaction = "{cfg.build.interaction}"
halt_on_error = {str(cfg.build.halt_on_error).lower()}
synctex = {str(cfg.build.synctex).lower()}
# Build artefacts are kept out of the source tree.
output_dir = "{cfg.build.output_dir}"
# Stand-alone landscape document for the printed spine.
spine_source = "{cfg.build.spine_source}"

[bib]
files = [{bib_files}]
style = "{cfg.bib.style}"

[figures]
dirs = [{figure_dirs}]
# Probe order for references written without an extension.
extensions = [{extensions}]
# Suffixes that mark generated graphics (epstopdf output).
derived_suffixes = ["-eps-converted-to.pdf"]

# Extra figure macros: macro name = 1-based index of the file-name argument.
[figures.macros]

[template]
# Table of contents chapter numbering: arabic | cjk | cjk_full
toc_style = "{cfg.template.toc_style.value}"
# Watermark placement: all | selected | off
watermark = "{cfg.template.watermark.value}"
watermark_image = "{cfg.template.watermark_image}"
watermark_width = "{cfg.template.watermark_width}"
watermark_x = "{cfg.template.watermark_x}"
watermark_y = "{cfg.template.watermark_y}"
# Default float placement for the figure macros.
float_spec = "{cfg.template.float_spec}"
# Written by 'thesis-cli build'; do not edit it by hand.
generated_file = "{cfg.template.generated_file}"

[clean]
extra_patterns = [{patterns}]
# The finished PDF is only removed when this is true or --outputs is given.
remove_outputs = {str(cfg.clean.remove_outputs).lower()}

[deps]
# Family name for XeLaTeX; checked with fc-list.
cjk_font = "{cfg.deps.cjk_font}"
# Metric files checked on the CJK package route (pdflatex, latex+dvipdfmx).
legacy_font_files = ["bkai00mp.tfm", "bsmi00lp.tfm"]

[stats]
# Files under these directories carry prose, and so does any file that opens
# a \\chapter or \\section; everything else is preamble.
chapter_dir = "{cfg.stats.chapter_dir}"
content_dir = "{cfg.stats.content_dir}"
"""


def _copy_template(
    source: Path, destination: Path, *, force: bool
) -> tuple[list[Path], list[Path]]:
    """Copy the template tree, reporting what was written and what was kept.

    The template's own ``thesis.toml`` is skipped: the new project gets the
    commented file from :func:`render_config_toml`.  A copied one would
    already exist by the time ``init`` writes that file, so the generated
    file would never be written and ``init`` would list it as a file it
    kept.
    """
    written: list[Path] = []
    skipped: list[Path] = []
    for item in sorted(source.rglob("*")):
        if any(part in _SKIP_NAMES for part in item.parts):
            continue
        relative = item.relative_to(source)
        if relative == Path(CONFIG_FILENAME):
            continue
        target = destination / relative
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if target.exists() and not force:
            skipped.append(target)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        written.append(target)
    return written, skipped


def init_project(
    destination: Path,
    *,
    template_dir: Path | None = None,
    values: Mapping[str, str] | None = None,
    force: bool = False,
) -> CommandReport:
    """Create a new thesis project.

    Parameters
    ----------
    destination : pathlib.Path
        Directory to create or populate.
    template_dir : pathlib.Path or None, optional
        Template to copy; located automatically when omitted.
    values : mapping of str to str or None, optional
        Front-matter values substituted into ``config/names.tex``.
    force : bool, optional
        Overwrite files that already exist in *destination*.

    Returns
    -------
    CommandReport
        What was created, what was kept, and which placeholders remain.

    Raises
    ------
    ProjectLayoutError
        When the template cannot be found, or *destination* is a file.
    """
    destination = Path(destination).resolve()
    if destination.exists() and not destination.is_dir():
        raise ProjectLayoutError(
            f"{destination} exists and is not a directory.",
            hint="Choose an empty directory for the new project.",
        )
    source = locate_template_dir(template_dir)
    destination.mkdir(parents=True, exist_ok=True)

    written, skipped = _copy_template(source, destination, force=force)

    config_path = destination / CONFIG_FILENAME
    if not config_path.exists() or force:
        config_path.write_text(render_config_toml(), encoding="utf-8")
        written.append(config_path)
    else:
        skipped.append(config_path)

    names_path = destination / "config" / "names.tex"
    applied: dict[str, str] = {}
    if values and names_path.is_file():
        original = names_path.read_text(encoding="utf-8")
        updated = apply_name_values(original, values)
        if updated != original:
            names_path.write_text(updated, encoding="utf-8")
            applied = dict(values)

    findings: list[Finding] = []
    if skipped:
        findings.append(
            Finding(
                severity=Severity.WARNING,
                kind="file-kept",
                location=str(destination),
                message=(
                    f"{len(skipped)} existing file(s) were left untouched; "
                    "pass --force to overwrite them."
                ),
                detail=", ".join(path.name for path in skipped[:10]),
            )
        )

    remaining = _remaining_placeholders(names_path)
    if remaining:
        findings.append(
            Finding(
                severity=Severity.INFO,
                kind="placeholder",
                location=str(names_path),
                # The names go in the message: the detail only shows with -v,
                # and which fields are left is the useful part.
                message=(
                    f"{len(remaining)} front-matter placeholder(s) still hold their default "
                    f"value ({', '.join(remaining)}); edit config/names.tex before submitting."
                ),
            )
        )

    summary = {
        "destination": str(destination),
        "template": str(source),
        "files_written": len(written),
        "files_kept": len(skipped),
        "values_applied": sorted(applied),
        "next_steps": "thesis-cli deps && thesis-cli build",
    }
    return CommandReport(
        command="init",
        findings=tuple(findings),
        tables=(
            ReportTable(
                title="Created",
                columns=("file",),
                rows=tuple(
                    (path.relative_to(destination).as_posix(),)
                    for path in written
                    if path.is_relative_to(destination)
                ),
            ),
        ),
        summary=summary,
    )


def _remaining_placeholders(names_path: Path) -> list[str]:
    """List front-matter macros that still hold the template's default value."""
    if not names_path.is_file():
        return []
    text = names_path.read_text(encoding="utf-8")
    defaults = {field.macro: field.example for field in NAME_FIELDS}
    remaining: list[str] = []
    for match in _RENEW_RE.finditer(text):
        name = match.group("name")
        if name in defaults and match.group("value").strip() == defaults[name]:
            remaining.append(name)
    return remaining


def interactive_values(fields: Iterable[NameField] = NAME_FIELDS) -> dict[str, str]:
    """Ask for front-matter values on the terminal.

    Parameters
    ----------
    fields : iterable of NameField, optional
        Fields to ask about.

    Returns
    -------
    dict of str to str
        Only the fields that got an answer; an empty answer keeps
        the template's placeholder.
    """
    import typer

    # Questions go to stderr, so 'init --interactive --json' still writes
    # nothing but JSON to stdout.
    values: dict[str, str] = {}
    typer.echo("Press Enter to keep the placeholder shown in brackets.", err=True)
    for field in fields:
        answer = typer.prompt(
            f"{field.prompt} [{field.example}]", default="", show_default=False, err=True
        )
        if answer.strip():
            values[field.macro] = answer.strip()
    return values
