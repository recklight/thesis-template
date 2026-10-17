"""Subprocess wrappers for the TeX toolchain.

Run by hand, the engine, BibTeX and the DVI converter have to be typed in
the right order with the right flags, and a program that is not installed
only shows up as a failure somewhere in the middle.  Here the engine is
chosen once in ``thesis.toml`` and the flags follow from it, missing
executables are reported before anything runs, and each call is logged
with its exit status and duration.

Commands are built as argument lists and run with ``shell=False``, never
as a string, so a path with a space, an ampersand or Chinese characters in
it cannot change what gets executed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from ..config import Engine, ThesisConfig
from ..errors import ToolNotFoundError
from ..logging_utils import get_logger

__all__ = [
    "BIBTEX_PROGRAM",
    "DVIPDFMX_PROGRAM",
    "LATEXMK_PROGRAM",
    "CommandResult",
    "EngineSpec",
    "build_environment",
    "dvipdfmx_command",
    "engine_spec",
    "find_executable",
    "kpsewhich",
    "latexmk_command",
    "manual_sequence",
    "require_executable",
    "run_command",
]

_LOG = get_logger(__name__)

LATEXMK_PROGRAM: Final[str] = "latexmk"
BIBTEX_PROGRAM: Final[str] = "bibtex"
DVIPDFMX_PROGRAM: Final[str] = "dvipdfmx"
KPSEWHICH_PROGRAM: Final[str] = "kpsewhich"

_DEFAULT_TIMEOUT: Final[float] = 900.0


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Outcome of one external command.

    Attributes
    ----------
    command : tuple of str
        Argument vector exactly as executed.
    returncode : int
        Process exit status; ``-1`` marks a timeout.
    stdout, stderr : str
        Captured output, decoded leniently.
    duration : float
        Wall-clock seconds.
    cwd : pathlib.Path
        Working directory used.
    timed_out : bool
        Whether the command was killed after exceeding its timeout.
    """

    command: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    cwd: Path = field(default_factory=Path)
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        """bool: ``True`` when the command exited with status zero."""
        return self.returncode == 0 and not self.timed_out

    @property
    def display(self) -> str:
        """str: the command rendered for logs and error messages."""
        return " ".join(self.command)


@dataclass(frozen=True, slots=True)
class EngineSpec:
    """How one engine is driven.

    Attributes
    ----------
    engine : Engine
        The toolchain this spec describes.
    program : str
        Executable that processes the ``.tex`` source.
    latexmk_flags : tuple of str
        Flags that select this engine when ``latexmk`` drives the build.
    intermediate_suffix : str
        Extension of the file the engine produces (``.pdf`` or ``.dvi``).
    needs_dvipdfmx : bool
        Whether a separate ``dvipdfmx`` step is required to reach a PDF.
    """

    engine: Engine
    program: str
    latexmk_flags: tuple[str, ...]
    intermediate_suffix: str
    needs_dvipdfmx: bool

    @property
    def helper_programs(self) -> tuple[str, ...]:
        """tuple of str: executables besides *program* that a build needs."""
        return (DVIPDFMX_PROGRAM,) if self.needs_dvipdfmx else ()


_SPECS: Final[Mapping[Engine, EngineSpec]] = {
    Engine.XELATEX: EngineSpec(
        engine=Engine.XELATEX,
        program="xelatex",
        latexmk_flags=("-xelatex",),
        intermediate_suffix=".pdf",
        needs_dvipdfmx=False,
    ),
    Engine.PDFLATEX: EngineSpec(
        engine=Engine.PDFLATEX,
        program="pdflatex",
        latexmk_flags=("-pdf",),
        intermediate_suffix=".pdf",
        needs_dvipdfmx=False,
    ),
    Engine.LATEX_DVIPDFMX: EngineSpec(
        engine=Engine.LATEX_DVIPDFMX,
        program="latex",
        # latexmk stops at the DVI; the PDF is produced by an explicit
        # dvipdfmx call so that the landscape flag needed by the spine can be
        # added without fighting latexmk's own dvipdf plumbing.
        latexmk_flags=("-dvi", "-pdf-"),
        intermediate_suffix=".dvi",
        needs_dvipdfmx=True,
    ),
}


def engine_spec(engine: Engine) -> EngineSpec:
    """Return the :class:`EngineSpec` for *engine*.

    Parameters
    ----------
    engine : Engine
        Configured toolchain.

    Returns
    -------
    EngineSpec
        Flags and helper programs for that toolchain.
    """
    return _SPECS[engine]


def find_executable(name: str) -> Path | None:
    """Locate *name* on ``PATH``.

    Parameters
    ----------
    name : str
        Program name, without extension.

    Returns
    -------
    pathlib.Path or None
        Absolute path, or ``None`` when not installed.
    """
    found = shutil.which(name)
    return Path(found) if found else None


def require_executable(name: str, *, purpose: str) -> Path:
    """Locate *name*, or raise with an install hint when it is missing.

    Parameters
    ----------
    name : str
        Program name.
    purpose : str
        What it is needed for, included in the message.

    Returns
    -------
    pathlib.Path
        Absolute path to the program.

    Raises
    ------
    ToolNotFoundError
        When the program is not on ``PATH``.
    """
    found = find_executable(name)
    if found is None:
        raise ToolNotFoundError(
            f"Required program '{name}' was not found on PATH ({purpose}).",
            hint=(
                "Install a TeX distribution (TeX Live, MiKTeX or MacTeX) and make sure "
                "its bin directory is on PATH, then re-run 'thesis-cli doctor'."
            ),
        )
    return found


def build_environment(
    config: ThesisConfig, extra: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Return the environment for a TeX run, with input paths widened.

    Parameters
    ----------
    config : ThesisConfig
        Project configuration, used for the root directory.
    extra : mapping of str to str or None, optional
        Additional variables to set.

    Returns
    -------
    dict of str to str
        A copy of ``os.environ`` in which ``TEXINPUTS``, ``BIBINPUTS`` and
        ``BSTINPUTS`` include the project root and its subdirectories.

    Notes
    -----
    The trailing empty entry (``...//;``) is TeX's way of saying "then look
    in the usual places", and the doubled slash means "search recursively".
    With these variables widened, ``bibtex`` can run inside the output
    directory and still find a ``.bib`` file kept with the sources.

    The trailing entry is appended even when the variable was already set.
    ``TEXINPUTS=/my/macros`` is usually exported without a trailing
    separator, and the search path would then stop there: the engine could
    not find its own class and style files.
    """
    env = dict(os.environ)
    root = str(config.root)
    recursive = root + os.sep + os.sep
    for variable in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS"):
        previous = env.get(variable, "")
        parts = [recursive, previous] if previous else [recursive]
        if not previous.endswith(os.pathsep):
            parts.append("")  # keep TeX's "then also look in the usual places"
        env[variable] = os.pathsep.join(parts)
    if extra:
        env.update(extra)
    return env


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: float = _DEFAULT_TIMEOUT,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run an external command and capture its output.

    Parameters
    ----------
    command : sequence of str
        Argument vector; never a shell string.
    cwd : pathlib.Path
        Working directory.
    timeout : float, optional
        Seconds before the process is killed.
    env : mapping of str to str or None, optional
        Environment; defaults to the current one.

    Returns
    -------
    CommandResult
        Exit status, captured streams and duration.

    Raises
    ------
    ToolNotFoundError
        When the executable does not exist.
    """
    argv = [str(item) for item in command]
    _LOG.debug("Running %s (cwd=%s)", " ".join(argv), cwd)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=dict(env) if env is not None else None,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ToolNotFoundError(
            f"Cannot execute '{argv[0]}': {exc.strerror or 'not found'}.",
            hint="Run 'thesis-cli deps' to see which parts of the toolchain are missing.",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        _LOG.error("Command timed out after %.0fs: %s", duration, " ".join(argv))
        return CommandResult(
            command=tuple(argv),
            returncode=-1,
            stdout=_as_text(exc.stdout),
            stderr=_as_text(exc.stderr),
            duration=duration,
            cwd=cwd,
            timed_out=True,
        )

    duration = time.monotonic() - started
    _LOG.debug("Exit %d after %.1fs", completed.returncode, duration)
    return CommandResult(
        command=tuple(argv),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        duration=duration,
        cwd=cwd,
    )


def _as_text(value: str | bytes | None) -> str:
    """Decode a possibly-bytes stream captured from a timed-out process."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _common_engine_flags(config: ThesisConfig) -> list[str]:
    """Flags shared by every direct engine invocation."""
    flags = [
        f"-interaction={config.build.interaction}",
        "-file-line-error",
    ]
    if config.build.halt_on_error:
        flags.append("-halt-on-error")
    flags.append("-synctex=1" if config.build.synctex else "-synctex=0")
    return flags


def latexmk_command(
    config: ThesisConfig,
    source: Path,
    *,
    output_dir: Path,
) -> list[str]:
    """Build the ``latexmk`` argument vector for *source*.

    Parameters
    ----------
    config : ThesisConfig
        Project configuration.
    source : pathlib.Path
        Document to compile.
    output_dir : pathlib.Path
        Directory that receives the artefacts.

    Returns
    -------
    list of str
        Argument vector.

    Notes
    -----
    ``latexmk`` works out by itself how many passes references and
    citations need to settle.  Counting them by hand is where it usually
    goes wrong: one pass too few and the PDF has ``??`` where the
    cross-reference numbers should be.
    """
    spec = engine_spec(config.build.engine)
    command = [LATEXMK_PROGRAM, *spec.latexmk_flags, "-bibtex", "-recorder"]
    command.extend(_common_engine_flags(config))
    command.append(f"-outdir={output_dir}")
    command.append(str(source))
    return command


def manual_sequence(
    config: ThesisConfig,
    source: Path,
    *,
    output_dir: Path,
    passes: int | None = None,
) -> list[list[str]]:
    """Build the explicit multi-pass command sequence.

    Parameters
    ----------
    config : ThesisConfig
        Project configuration.
    source : pathlib.Path
        Document to compile.
    output_dir : pathlib.Path
        Directory that receives the artefacts.
    passes : int or None, optional
        Total number of engine invocations.  Defaults to
        ``config.build.max_passes`` and is clamped to at least two, since a
        single pass can never resolve a cross-reference.

    Returns
    -------
    list of list of str
        Commands to run in order: engine, ``bibtex``, then the remaining
        engine passes.  The ``dvipdfmx`` step is *not* included here; the
        build command appends it so that the spine target can add ``-l``.

    Notes
    -----
    This is the fallback for environments without ``latexmk``.  With the
    default four passes it runs engine, bibtex, then three more engine
    passes: ``\\cite`` needs one pass after ``bibtex`` has written the
    ``.bbl``, and ``\\ref`` one more to read the numbers that pass wrote.
    The build skips the ``bibtex`` step when the first pass left no
    ``\\bibdata`` in the ``.aux``.
    """
    spec = engine_spec(config.build.engine)
    total = config.build.max_passes if passes is None else passes
    total = max(int(total), 2)

    flags = _common_engine_flags(config)
    flags.append(f"-output-directory={output_dir}")
    engine_call = [spec.program, *flags, str(source)]

    jobname = source.stem
    commands: list[list[str]] = [list(engine_call)]
    commands.append([BIBTEX_PROGRAM, jobname])
    commands.extend([list(engine_call) for _ in range(total - 1)])
    return commands


def dvipdfmx_command(
    dvi: Path,
    output: Path,
    *,
    landscape: bool = False,
) -> list[str]:
    """Build the ``dvipdfmx`` argument vector.

    Parameters
    ----------
    dvi : pathlib.Path
        Input ``.dvi`` file.
    output : pathlib.Path
        Output ``.pdf`` file.
    landscape : bool, optional
        Add ``-l``.  The spine sheet is typeset in landscape and needs it.
        It is easy to leave out when typing the command by hand, so the
        spine target always sets it.

    Returns
    -------
    list of str
        Argument vector.
    """
    command = [DVIPDFMX_PROGRAM]
    if landscape:
        command.append("-l")
    command.extend(["-o", str(output), str(dvi)])
    return command


def kpsewhich(name: str, *, cwd: Path | None = None, timeout: float = 30.0) -> Path | None:
    """Ask ``kpsewhich`` where a TeX file lives.

    Parameters
    ----------
    name : str
        File name as TeX would request it, e.g. ``"geometry.sty"``.
    cwd : pathlib.Path or None, optional
        Directory to search from.
    timeout : float, optional
        Seconds before giving up.

    Returns
    -------
    pathlib.Path or None
        Location of the file, or ``None`` when ``kpsewhich`` reports nothing
        or is itself unavailable.

    Notes
    -----
    Returning ``None`` for a missing ``kpsewhich`` rather than raising lets
    ``deps`` and ``doctor`` produce a complete report on a machine with no
    TeX installation at all, instead of stopping at the first check.
    """
    if find_executable(KPSEWHICH_PROGRAM) is None:
        return None
    result = run_command(
        [KPSEWHICH_PROGRAM, name],
        cwd=cwd or Path.cwd(),
        timeout=timeout,
    )
    if not result.ok:
        return None
    first = result.stdout.strip().splitlines()
    return Path(first[0].strip()) if first and first[0].strip() else None
