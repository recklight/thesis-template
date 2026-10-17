"""Command line interface for thesis-cli.

One subcommand per job, from creating the project to cleaning up after it:

======================  ====================================================
Command                 Purpose
======================  ====================================================
``init``                Create a new project from the neutral template.
``deps``                Verify the TeX installation before compiling.
``doctor``              Diagnose environment, project and toolchain.
``build``               Compile the thesis or the printed spine.
``check-refs``          Audit citations, references, labels and the ``.bib``.
``figures``             Resolve every graphic; catch case and path errors.
``stats``               Count CJK characters and Latin words per chapter.
``clean``               Remove build artefacts, and only those.
======================  ====================================================

The audit commands accept ``--json`` for machine-readable output on
*stdout* and ``--strict`` to turn warnings into a non-zero exit status, so
the same commands work at a terminal and in a CI job.
"""

from __future__ import annotations

from pathlib import Path

import typer

from . import __version__
from .commands.build import BuildTarget, attached_report, build_document
from .commands.check_refs import check_references
from .commands.clean import clean_project
from .commands.deps import check_dependencies
from .commands.doctor import run_doctor
from .commands.figures import audit_figures
from .commands.init_project import (
    init_project,
    interactive_values,
    parse_assignments,
)
from .commands.stats import collect_statistics
from .config import Engine, ThesisConfig, load_config
from .errors import ThesisCliError
from .logging_utils import configure_logging
from .report import CommandReport, render_report

__all__ = ["app", "main"]

app = typer.Typer(
    name="thesis-cli",
    help="Build and audit a LaTeX thesis: compile, check references, resolve figures, "
    "count words, and clean up.",
    no_args_is_help=True,
    add_completion=False,
)

_STATE: dict[str, object] = {"project": None, "verbosity": 0, "quiet": False}


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"thesis-cli {__version__}")
        raise typer.Exit(0)


@app.callback()
def main(
    project: Path | None = typer.Option(
        None,
        "--project",
        "-C",
        help="Project directory containing thesis.toml (default: search upwards from cwd).",
    ),
    verbose: int = typer.Option(
        0, "--verbose", "-v", count=True, help="Increase logging detail; repeat for debug."
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Only report errors."),
    version: bool = typer.Option(  # noqa: ARG001 - handled by its eager callback
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Shared options for every subcommand."""
    configure_logging(verbose, quiet=quiet)
    _STATE["project"] = project
    _STATE["verbosity"] = verbose
    _STATE["quiet"] = quiet


def _load(required: bool = True) -> ThesisConfig:
    """Load the project configuration, honouring ``--project``.

    Every command that works on a thesis needs its ``thesis.toml``: without
    one the current directory would pass for the project root, and ``clean``
    would sweep a home folder.  ``deps`` and ``doctor`` also run outside a
    project, where they check only the machine, so they fall back to the
    defaults.
    """
    start = _STATE.get("project")
    return load_config(Path(start) if start is not None else None, required=required)


def _finish(report: CommandReport, *, as_json: bool, strict: bool) -> None:
    """Render *report* and exit with the status it implies."""
    render_report(report, as_json=as_json, verbose=bool(_STATE.get("verbosity")))
    code = report.exit_code(strict=strict)
    if code:
        raise typer.Exit(code)


def _fail(error: ThesisCliError) -> None:
    """Print a deliberate failure and exit with its code."""
    typer.secho(f"error: {error.message}", fg=typer.colors.RED, err=True)
    if error.hint:
        typer.secho(f"hint:  {error.hint}", fg=typer.colors.YELLOW, err=True)
    raise typer.Exit(error.exit_code)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------
@app.command("init")
def init_command(
    destination: Path = typer.Argument(..., help="Directory for the new thesis project."),
    template: Path | None = typer.Option(
        None, "--template", help="Template directory to copy (default: the bundled one)."
    ),
    set_value: list[str] | None = typer.Option(
        None,
        "--set",
        help="Front-matter value, e.g. --set cTitle=室內定位研究. Repeatable.",
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Ask for the front-matter values."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite files that already exist."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Create a new thesis project from the neutral template."""
    try:
        values = parse_assignments(set_value or [])
        if interactive:
            values.update(interactive_values())
        report = init_project(destination, template_dir=template, values=values, force=force)
    except ThesisCliError as error:
        _fail(error)
        return
    _finish(report, as_json=as_json, strict=False)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
@app.command("build")
def build_command(
    target: BuildTarget = typer.Option(
        BuildTarget.THESIS, "--target", help="What to compile: the thesis or the printed spine."
    ),
    engine: Engine | None = typer.Option(
        None, "--engine", help="Override the engine configured in thesis.toml."
    ),
    latexmk: bool = typer.Option(
        True, "--latexmk/--no-latexmk", help="Let latexmk decide the number of passes."
    ),
    preflight: bool = typer.Option(
        True, "--preflight/--no-preflight", help="Check graphics and inputs before compiling."
    ),
    strict: bool = typer.Option(False, "--strict", help="Treat LaTeX warnings as failures."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Compile the thesis, running as many passes as the references need."""
    try:
        config = _load()
        if engine is not None:
            config = config.with_overrides(
                build=type(config.build)(
                    engine=engine,
                    use_latexmk=config.build.use_latexmk,
                    max_passes=config.build.max_passes,
                    interaction=config.build.interaction,
                    halt_on_error=config.build.halt_on_error,
                    synctex=config.build.synctex,
                    output_dir=config.build.output_dir,
                    spine_source=config.build.spine_source,
                )
            )
        report = build_document(config, target=target, use_latexmk=latexmk, preflight=preflight)
    except ThesisCliError as error:
        partial = attached_report(error)
        if partial is not None:
            render_report(partial, as_json=as_json, verbose=bool(_STATE.get("verbosity")))
        _fail(error)
        return
    _finish(report, as_json=as_json, strict=strict)


# ---------------------------------------------------------------------------
# check-refs
# ---------------------------------------------------------------------------
@app.command("check-refs")
def check_refs_command(
    use_aux: bool = typer.Option(
        True, "--aux/--no-aux", help="Also read the .aux file from the last build."
    ),
    strict: bool = typer.Option(False, "--strict", help="Fail on warnings as well as errors."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Audit citations, bibliography entries, cross-references and labels."""
    try:
        report = check_references(_load(), use_aux=use_aux)
    except ThesisCliError as error:
        _fail(error)
        return
    _finish(report, as_json=as_json, strict=strict)


# ---------------------------------------------------------------------------
# figures
# ---------------------------------------------------------------------------
@app.command("figures")
def figures_command(
    unused: bool = typer.Option(
        True, "--unused/--no-unused", help="Also list graphics nothing references."
    ),
    strict: bool = typer.Option(False, "--strict", help="Fail on warnings as well as errors."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Resolve every graphics reference, catching case and path mistakes."""
    try:
        report = audit_figures(_load(), report_unused=unused)
    except ThesisCliError as error:
        _fail(error)
        return
    _finish(report, as_json=as_json, strict=strict)


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
@app.command("stats")
def stats_command(
    only_chapters: bool = typer.Option(
        False, "--only-chapters", help="Restrict the count to the chapters directory."
    ),
    all_files: bool = typer.Option(
        False, "--all-files", help="Include preamble and configuration files too."
    ),
    strict: bool = typer.Option(False, "--strict", help="Fail when a chapter file is empty."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Count CJK characters and Latin words per file."""
    try:
        report = collect_statistics(_load(), only_chapters=only_chapters, all_files=all_files)
    except ThesisCliError as error:
        _fail(error)
        return
    _finish(report, as_json=as_json, strict=strict)


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------
@app.command("clean")
def clean_command(
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be removed without deleting."
    ),
    outputs: bool = typer.Option(False, "--outputs", help="Also remove the finished PDF."),
    show_skipped: bool = typer.Option(
        False, "--show-skipped", help="List files that were deliberately spared."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Remove build artefacts; sources and unrelated files are never touched."""
    try:
        report = clean_project(
            _load(),
            dry_run=dry_run,
            include_outputs=True if outputs else None,
            show_skipped=show_skipped,
        )
    except ThesisCliError as error:
        _fail(error)
        return
    _finish(report, as_json=as_json, strict=False)


# ---------------------------------------------------------------------------
# deps
# ---------------------------------------------------------------------------
@app.command("deps")
def deps_command(
    strict: bool = typer.Option(False, "--strict", help="Fail on warnings as well as errors."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Check that every LaTeX package, program and font the build needs is present."""
    try:
        report = check_dependencies(_load(required=False))
    except ThesisCliError as error:
        _fail(error)
        return
    _finish(report, as_json=as_json, strict=strict)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
@app.command("doctor")
def doctor_command(
    smoke: bool = typer.Option(
        True, "--smoke/--no-smoke", help="Compile a minimal CJK document as a final check."
    ),
    strict: bool = typer.Option(False, "--strict", help="Fail on warnings as well as errors."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON on stdout."),
) -> None:
    """Diagnose the Python environment, the project layout and the TeX toolchain."""
    try:
        report = run_doctor(_load(required=False), smoke=smoke)
    except ThesisCliError as error:
        _fail(error)
        return
    _finish(report, as_json=as_json, strict=strict)


if __name__ == "__main__":  # pragma: no cover
    app()
