"""End-to-end tests of the command line interface.

These drive the real Typer application through its runner, so they cover
argument parsing, the exit-code mapping and the fact that ``--json`` puts
JSON and nothing else on stdout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import tomli
from typer.testing import CliRunner

from thesis_cli import __version__
from thesis_cli.cli import app
from thesis_cli.errors import CheckFailedError, ConfigError, ValidationError
from thesis_cli.logging_utils import configure_logging

runner = CliRunner()


def _run(*args: str) -> object:
    return runner.invoke(app, list(args))


class TestTopLevel:
    """Global options."""

    def test_help_lists_every_command(self) -> None:
        result = _run("--help")
        assert result.exit_code == 0
        for command in (
            "init",
            "build",
            "check-refs",
            "figures",
            "stats",
            "clean",
            "deps",
            "doctor",
        ):
            assert command in result.stdout

    def test_version(self) -> None:
        result = _run("--version")
        assert result.exit_code == 0
        assert __version__ in result.stdout

    def test_every_copy_of_the_version_agrees(self) -> None:
        # pyproject.toml, the package and CITATION.cff each carry the version,
        # and a release has to change all of them together with this test.
        root = Path(__file__).resolve().parents[1]
        pyproject = tomli.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        citation = (root / "CITATION.cff").read_text(encoding="utf-8")
        assert __version__ == "0.1.0"
        assert pyproject["project"]["version"] == __version__
        assert f'\nversion: "{__version__}"\n' in citation.replace("\r\n", "\n")

    def test_no_arguments_shows_help(self) -> None:
        # click 8.1 exits 0 here and click 8.2 exits 2; both print the usage.
        result = _run()
        assert result.exit_code in (0, 2)
        assert "Usage" in result.output


class TestAuditCommands:
    """The read-only commands on the synthetic project."""

    def test_check_refs_is_clean(self, project: Path) -> None:
        result = _run("-C", str(project), "check-refs")
        assert result.exit_code == 0

    def test_check_refs_json_is_parseable(self, project: Path) -> None:
        result = _run("-C", str(project), "check-refs", "--json")
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["command"] == "check-refs"

    def test_check_refs_fails_on_an_undefined_citation(self, project: Path) -> None:
        path = project / "chapters" / "ch2.tex"
        path.write_text(path.read_text(encoding="utf-8") + "\n\\cite{nowhere}\n", encoding="utf-8")
        result = _run("-C", str(project), "check-refs")
        assert result.exit_code == CheckFailedError.exit_code

    def test_strict_turns_a_warning_into_a_failure(self, project: Path) -> None:
        path = project / "bib" / "refs.bib"
        path.write_text(
            path.read_text(encoding="utf-8") + "\n@misc{orphan, title={T}, year={2020}}\n",
            encoding="utf-8",
        )
        assert _run("-C", str(project), "check-refs").exit_code == 0
        assert _run("-C", str(project), "check-refs", "--strict").exit_code != 0

    def test_figures_is_clean(self, project: Path) -> None:
        assert _run("-C", str(project), "figures").exit_code == 0

    def test_figures_reports_a_case_mismatch(self, project: Path) -> None:
        (project / "figures" / "plot.png").rename(project / "figures" / "Plot.png")
        result = _run("-C", str(project), "figures", "--json")
        assert result.exit_code == CheckFailedError.exit_code
        payload = json.loads(result.stdout)
        assert payload["summary"]["case_mismatch"] == 1

    def test_stats_json(self, project: Path) -> None:
        result = _run("-C", str(project), "stats", "--json")
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["summary"]["cjk_chars"] > 0

    def test_stats_only_chapters(self, project: Path) -> None:
        result = _run("-C", str(project), "stats", "--only-chapters", "--json")
        payload = json.loads(result.stdout)
        assert payload["summary"]["files_listed"] == 2

    def test_deps_runs_without_a_tex_installation(self, project: Path) -> None:
        result = _run("-C", str(project), "deps", "--json")
        payload = json.loads(result.stdout)
        assert payload["command"] == "deps"

    def test_doctor_without_the_smoke_test(self, project: Path) -> None:
        result = _run("-C", str(project), "doctor", "--no-smoke", "--json")
        payload = json.loads(result.stdout)
        assert payload["summary"]["smoke_attempted"] is False

    def test_doctor_outside_a_project_fails_only_on_the_toolchain(self, tmp_path: Path) -> None:
        # Right after installing there is no project yet.  A missing program,
        # package or font may fail the check, but the absent thesis files must not.
        result = _run("-C", str(tmp_path), "doctor", "--no-smoke", "--json")
        payload = json.loads(result.stdout)
        errors = {item["kind"] for item in payload["findings"] if item["severity"] == "error"}
        assert errors <= {"missing-program", "missing-package", "missing-font"}
        if not errors:
            assert result.exit_code == 0


class TestCleanCommand:
    """Deletion through the CLI."""

    def test_dry_run_keeps_the_file(self, project: Path) -> None:
        (project / "thesis.aux").write_text("x", encoding="utf-8")
        result = _run("-C", str(project), "clean", "--dry-run", "--json")
        assert result.exit_code == 0
        assert (project / "thesis.aux").is_file()
        assert json.loads(result.stdout)["summary"]["dry_run"] is True

    def test_real_run_removes_the_file(self, project: Path) -> None:
        (project / "thesis.aux").write_text("x", encoding="utf-8")
        result = _run("-C", str(project), "clean", "--json")
        assert result.exit_code == 0
        assert not (project / "thesis.aux").exists()

    def test_sources_survive(self, project: Path) -> None:
        (project / "thesis.aux").write_text("x", encoding="utf-8")
        _run("-C", str(project), "clean")
        assert (project / "thesis.tex").is_file()
        assert (project / "bib" / "refs.bib").is_file()
        assert (project / "figures" / "plot.png").is_file()


class TestInitCommand:
    """Project creation through the CLI."""

    def test_creates_a_usable_project(self, tmp_path: Path, template_root: Path) -> None:
        destination = tmp_path / "new"
        result = _run("init", str(destination), "--template", str(template_root), "--json")
        assert result.exit_code == 0
        assert (destination / "thesis.tex").is_file()
        assert (destination / "thesis.toml").is_file()

    def test_set_values_are_applied(self, tmp_path: Path, template_root: Path) -> None:
        destination = tmp_path / "new"
        result = _run(
            "init",
            str(destination),
            "--template",
            str(template_root),
            "--set",
            "cTitle=測試題目",
            "--json",
        )
        assert result.exit_code == 0
        assert "測試題目" in (destination / "config" / "names.tex").read_text(encoding="utf-8")

    def test_set_values_are_escaped_for_latex(self, tmp_path: Path, template_root: Path) -> None:
        destination = tmp_path / "new"
        result = _run(
            "init",
            str(destination),
            "--template",
            str(template_root),
            "--set",
            "eTitle=50% of R&D {draft}",
        )
        assert result.exit_code == 0
        names = (destination / "config" / "names.tex").read_text(encoding="utf-8")
        assert "\\renewcommand{\\eTitle}{50\\% of R\\&D \\{draft\\}}" in names

    def test_bad_set_argument_is_rejected(self, tmp_path: Path, template_root: Path) -> None:
        result = _run(
            "init", str(tmp_path / "new"), "--template", str(template_root), "--set", "oops"
        )
        assert result.exit_code == ValidationError.exit_code
        assert "name=value" in result.output


class TestBuildCommand:
    """Build failures are reported with the right status."""

    def test_fragment_target_is_rejected(self, project: Path) -> None:
        (project / "thesis.toml").write_text(
            '[project]\nmain = "chapters/ch1.tex"\n\n[template]\nwatermark = "off"\n',
            encoding="utf-8",
        )
        result = _run("-C", str(project), "build")
        assert result.exit_code == ValidationError.exit_code

    def test_preflight_failure_exits_with_the_build_code(self, project: Path) -> None:
        (project / "figures" / "plot.png").unlink()
        result = _run("-C", str(project), "build")
        assert result.exit_code == 5


class TestErrorReporting:
    """Deliberate failures produce a message, not a traceback."""

    def test_bad_configuration(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text('[build]\nengine = "nope"\n', encoding="utf-8")
        result = _run("-C", str(tmp_path), "check-refs")
        assert result.exit_code == ConfigError.exit_code
        assert result.exception is None or isinstance(result.exception, SystemExit)

    @pytest.mark.parametrize("command", ["check-refs", "figures", "stats"])
    def test_missing_main_document(self, tmp_path: Path, command: str) -> None:
        (tmp_path / "thesis.toml").write_text('[project]\nmain = "absent.tex"\n', encoding="utf-8")
        result = _run("-C", str(tmp_path), command)
        assert result.exit_code == 3


class TestWithoutAProject:
    """A directory with no thesis.toml anywhere above it is not a project."""

    @staticmethod
    def _someone_elses_files(root: Path) -> list[Path]:
        files = [
            root / "paper" / "paper.tex",
            root / "paper" / "paper.log",
            root / "some" / "deep" / "analysis.asv",
            root / "some" / "deep" / "notes.bak",
        ]
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x", encoding="utf-8")
        return files

    def test_clean_refuses_and_deletes_nothing(self, tmp_path: Path) -> None:
        files = self._someone_elses_files(tmp_path)
        result = _run("-C", str(tmp_path), "clean")
        assert result.exit_code == ConfigError.exit_code
        assert all(path.is_file() for path in files)
        assert "thesis.toml" in result.output

    @pytest.mark.parametrize("command", ["check-refs", "figures", "stats", "build"])
    def test_project_commands_exit_with_the_config_code(self, tmp_path: Path, command: str) -> None:
        assert _run("-C", str(tmp_path), command).exit_code == ConfigError.exit_code

    def test_deps_still_checks_the_machine(self, tmp_path: Path) -> None:
        result = _run("-C", str(tmp_path), "deps", "--json")
        assert json.loads(result.stdout)["command"] == "deps"


def _stdout_only_runner() -> CliRunner:
    """Return a runner whose ``result.stdout`` holds stdout alone.

    click 8.1 mixes stderr into stdout unless told otherwise; click 8.2
    keeps them apart and rejects the argument.
    """
    try:
        return CliRunner(mix_stderr=False)  # type: ignore[call-arg]
    except TypeError:
        return CliRunner()


class TestOutputStreams:
    """--json writes JSON to stdout and everything else to stderr."""

    def test_log_messages_go_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        logger = configure_logging(2)
        try:
            logger.warning("a warning that must not reach stdout")
            logger.info("progress information")
        finally:
            configure_logging(0)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "a warning that must not reach stdout" in captured.err

    def test_interactive_prompts_stay_off_stdout(self, tmp_path: Path, template_root: Path) -> None:
        result = _stdout_only_runner().invoke(
            app,
            ["init", str(tmp_path / "new"), "--template", str(template_root), "-i", "--json"],
            input="\n" * 20,
        )
        assert result.exit_code == 0
        assert "Press Enter" not in result.stdout
        assert json.loads(result.stdout)["command"] == "init"
