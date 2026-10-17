r"""Tests for the build command and the toolchain wrappers.

No TeX installation is required.  The parts that can be checked without one
are: rejecting a target that is not a compilable document, building the
right command line for each engine, and projecting ``thesis.toml`` into
``config/generated.tex``.  Anything that actually starts a program is
marked ``requires_latex`` and skipped when the engine is absent.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import thesis_cli.commands.build as build_module
from thesis_cli.commands.build import (
    BuildTarget,
    attached_report,
    build_document,
    render_generated_settings,
    validate_main_document,
    write_generated_settings,
)
from thesis_cli.commands.deps import _check_font_fontconfig
from thesis_cli.config import Engine, ThesisConfig, TocStyle, WatermarkMode, load_config
from thesis_cli.errors import (
    BuildFailedError,
    ProjectLayoutError,
    ToolNotFoundError,
    ValidationError,
)
from thesis_cli.latex.engine import (
    CommandResult,
    build_environment,
    dvipdfmx_command,
    engine_spec,
    find_executable,
    kpsewhich,
    latexmk_command,
    manual_sequence,
    require_executable,
    run_command,
)


class TestValidateMainDocument:
    r"""Compiling a fragment is rejected before TeX is started."""

    def test_a_real_document_passes(self, project: Path) -> None:
        validate_main_document(project / "thesis.tex")

    def test_a_fragment_is_rejected(self, project: Path) -> None:
        with pytest.raises(ValidationError) as info:
            validate_main_document(project / "chapters" / "ch1.tex")
        assert "fragment" in str(info.value)
        assert r"\documentclass" in str(info.value)
        assert "thesis-cli build" in (info.value.hint or "")

    def test_a_bib_file_is_rejected_by_extension(self, project: Path) -> None:
        # Pointing the engine at the bibliography instead of the document
        # is an easy slip, and TeX's own diagnosis of it is unreadable.
        with pytest.raises(ValidationError, match=r"\.bib file, not a LaTeX document"):
            validate_main_document(project / "bib" / "refs.bib")

    def test_a_class_file_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "thesis_report.cls"
        path.write_text(r"\ProvidesClass{thesis_report}", encoding="utf-8")
        with pytest.raises(ValidationError):
            validate_main_document(path)

    def test_a_commented_out_documentclass_does_not_count(self, tmp_path: Path) -> None:
        path = tmp_path / "fake.tex"
        path.write_text("% \\documentclass{report}\n% \\begin{document}\ntext\n", encoding="utf-8")
        with pytest.raises(ValidationError):
            validate_main_document(path)

    def test_a_missing_file_raises_a_layout_error(self, tmp_path: Path) -> None:
        with pytest.raises(ProjectLayoutError):
            validate_main_document(tmp_path / "absent.tex")


class TestGeneratedSettings:
    """thesis.toml projected into LaTeX."""

    def test_toc_style_becomes_a_macro_call(self, tmp_path: Path) -> None:
        config = ThesisConfig.default(tmp_path)
        assert "\\ThesisTocStyleCJKFull" in render_generated_settings(config)

        arabic = config.with_overrides(template=type(config.template)(toc_style=TocStyle.ARABIC))
        assert "\\ThesisTocStyleArabic" in render_generated_settings(arabic)

    def test_watermark_mode_is_passed_through(self, tmp_path: Path) -> None:
        config = ThesisConfig.default(tmp_path).with_overrides(
            template=type(ThesisConfig.default(tmp_path).template)(watermark=WatermarkMode.SELECTED)
        )
        assert "\\ThesisWatermark{selected}" in render_generated_settings(config)

    def test_watermark_path_loses_its_extension(self, tmp_path: Path) -> None:
        text = render_generated_settings(ThesisConfig.default(tmp_path))
        assert "{figures/watermark_placeholder}" in text
        assert ".png}" not in text

    def test_file_carries_a_do_not_edit_banner(self, tmp_path: Path) -> None:
        assert "DO NOT EDIT" in render_generated_settings(ThesisConfig.default(tmp_path))

    def test_write_creates_the_file(self, project: Path) -> None:
        config = load_config(project, required=True)
        target = write_generated_settings(config)
        assert target.is_file()
        assert target.read_text(encoding="utf-8") == render_generated_settings(config)

    def test_unchanged_content_does_not_touch_the_file(self, project: Path) -> None:
        config = load_config(project, required=True)
        first = write_generated_settings(config)
        stamp = first.stat().st_mtime_ns
        second = write_generated_settings(config)
        assert second.stat().st_mtime_ns == stamp, "rewriting would force a full recompile"


class TestEngineSpecs:
    """Command lines, without running anything."""

    @pytest.mark.parametrize(
        ("engine", "flag"),
        [
            (Engine.XELATEX, "-xelatex"),
            (Engine.PDFLATEX, "-pdf"),
            (Engine.LATEX_DVIPDFMX, "-dvi"),
        ],
    )
    def test_latexmk_flag_per_engine(self, tmp_path: Path, engine: Engine, flag: str) -> None:
        config = ThesisConfig.default(tmp_path).with_overrides(
            build=type(ThesisConfig.default(tmp_path).build)(engine=engine)
        )
        command = latexmk_command(config, tmp_path / "thesis.tex", output_dir=tmp_path / "build")
        assert command[0] == "latexmk"
        assert flag in command
        assert "-bibtex" in command
        assert any(item.startswith("-outdir=") for item in command)

    def test_dvipdfmx_is_only_needed_for_the_dvi_route(self) -> None:
        assert engine_spec(Engine.LATEX_DVIPDFMX).needs_dvipdfmx is True
        assert engine_spec(Engine.XELATEX).needs_dvipdfmx is False
        assert engine_spec(Engine.LATEX_DVIPDFMX).helper_programs == ("dvipdfmx",)

    def test_manual_sequence_runs_bibtex_between_passes(self, tmp_path: Path) -> None:
        config = ThesisConfig.default(tmp_path)
        commands = manual_sequence(config, tmp_path / "thesis.tex", output_dir=tmp_path / "build")
        programs = [item[0] for item in commands]
        assert programs[0] == "xelatex"
        assert programs[1] == "bibtex"
        assert programs.count("xelatex") >= 3, "references need more than one pass"

    def test_manual_sequence_never_runs_fewer_than_two_passes(self, tmp_path: Path) -> None:
        config = ThesisConfig.default(tmp_path)
        commands = manual_sequence(
            config, tmp_path / "thesis.tex", output_dir=tmp_path / "build", passes=1
        )
        assert [item[0] for item in commands].count("xelatex") == 2

    def test_engine_flags_include_file_line_error(self, tmp_path: Path) -> None:
        config = ThesisConfig.default(tmp_path)
        first = manual_sequence(config, tmp_path / "t.tex", output_dir=tmp_path)[0]
        assert "-file-line-error" in first
        assert "-interaction=nonstopmode" in first
        assert "-halt-on-error" in first

    def test_spine_gets_the_landscape_flag(self, tmp_path: Path) -> None:
        # Forgetting dvipdfmx -l is the classic spine mistake.
        landscape = dvipdfmx_command(tmp_path / "s.dvi", tmp_path / "s.pdf", landscape=True)
        upright = dvipdfmx_command(tmp_path / "s.dvi", tmp_path / "s.pdf")
        assert "-l" in landscape
        assert "-l" not in upright

    def test_build_environment_widens_the_search_paths(self, tmp_path: Path) -> None:
        env = build_environment(ThesisConfig.default(tmp_path))
        for variable in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS"):
            assert str(tmp_path) in env[variable]

    @pytest.mark.parametrize(
        "previous",
        [None, "/my/macros", "/my/macros" + os.pathsep],
        ids=["unset", "no-trailing-separator", "trailing-separator"],
    )
    def test_search_paths_always_end_in_the_empty_entry(
        self, tmp_path: Path, previous: str | None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The trailing empty entry is how TeX is told "then also look in the
        # usual places".  An existing TEXINPUTS is normally written without
        # a trailing separator; appended as it stands, it would close the
        # search path and the engine could not find its own .cls.
        for variable in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS"):
            if previous is None:
                monkeypatch.delenv(variable, raising=False)
            else:
                monkeypatch.setenv(variable, previous)
        env = build_environment(ThesisConfig.default(tmp_path))
        for variable in ("TEXINPUTS", "BIBINPUTS", "BSTINPUTS"):
            assert env[variable].endswith(os.pathsep), variable
            assert str(tmp_path) in env[variable]
            if previous is not None:
                assert "/my/macros" in env[variable]


class TestRunCommand:
    """The subprocess wrapper."""

    def test_captures_output_and_status(self, tmp_path: Path) -> None:
        import sys

        result = run_command([sys.executable, "-c", "print('hello')"], cwd=tmp_path)
        assert isinstance(result, CommandResult)
        assert result.ok
        assert "hello" in result.stdout
        assert result.duration >= 0.0

    def test_non_zero_status_is_reported_not_raised(self, tmp_path: Path) -> None:
        import sys

        result = run_command([sys.executable, "-c", "raise SystemExit(3)"], cwd=tmp_path)
        assert result.ok is False
        assert result.returncode == 3

    def test_missing_executable_raises_tool_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(ToolNotFoundError):
            run_command(["definitely-not-a-real-program-xyz"], cwd=tmp_path)

    def test_require_executable_message_names_the_purpose(self) -> None:
        with pytest.raises(ToolNotFoundError) as info:
            require_executable("definitely-not-a-real-program-xyz", purpose="testing")
        assert "testing" in str(info.value)
        assert info.value.exit_code == 4

    def test_find_executable_returns_none_when_absent(self) -> None:
        assert find_executable("definitely-not-a-real-program-xyz") is None

    def test_kpsewhich_returns_none_without_a_tex_installation(self, tmp_path: Path) -> None:
        if find_executable("kpsewhich") is not None:  # pragma: no cover - TeX present
            pytest.skip("kpsewhich is installed; this checks the absent case")
        assert kpsewhich("geometry.sty", cwd=tmp_path) is None


class TestBuildWithoutTeX:
    """The failure path when no engine is installed."""

    def test_missing_engine_is_reported_clearly(self, project: Path) -> None:
        if find_executable("xelatex") is not None:  # pragma: no cover - TeX present
            pytest.skip("xelatex is installed")
        with pytest.raises(ToolNotFoundError) as info:
            build_document(load_config(project, required=True), preflight=False)
        assert "xelatex" in str(info.value)
        assert "TeX distribution" in (info.value.hint or "")

    def test_preflight_failure_stops_before_any_program_runs(self, project: Path) -> None:
        (project / "figures" / "plot.png").unlink()
        with pytest.raises(BuildFailedError) as info:
            build_document(load_config(project, required=True))
        assert "Preflight" in str(info.value)
        assert info.value.exit_code == 5

    def test_preflight_reports_a_missing_bibliography(self, project: Path) -> None:
        (project / "bib" / "refs.bib").unlink()
        with pytest.raises(BuildFailedError):
            build_document(load_config(project, required=True))

    def test_preflight_reports_a_missing_watermark(self, project: Path) -> None:
        (project / "thesis.toml").write_text(
            '[project]\nmain = "thesis.tex"\n\n'
            '[bib]\nfiles = ["bib/refs.bib"]\n\n'
            '[template]\nwatermark = "all"\nwatermark_image = "figures/absent.png"\n',
            encoding="utf-8",
        )
        with pytest.raises(BuildFailedError):
            build_document(load_config(project, required=True))

    def test_the_partial_report_survives_the_failure(self, project: Path) -> None:
        # The diagnosis must not be thrown away along with the error: the CLI
        # renders these tables before printing the failure message.
        (project / "figures" / "plot.png").unlink()
        try:
            build_document(load_config(project, required=True))
        except BuildFailedError as error:
            report = attached_report(error)
            assert report is not None
            assert report.error_count >= 1
            assert report.summary["status"] == "preflight-failed"
        else:  # pragma: no cover - the call must raise
            raise AssertionError("expected BuildFailedError")

    def test_missing_generated_settings_do_not_fail_preflight(
        self, tmp_path: Path, template_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A thesis project ignores config/generated.tex, so a fresh clone lacks
        # it; build writes it before preflight looks at the \input files.
        project = tmp_path / "thesis"
        shutil.copytree(template_root, project)
        (project / "config" / "generated.tex").unlink()
        calls = TestManualPasses._fake_toolchain(monkeypatch, bibdata=False)
        build_document(load_config(project, required=True))
        assert (project / "config" / "generated.tex").is_file()
        assert calls

    def test_fragment_target_is_rejected_before_preflight(self, project: Path) -> None:
        config = load_config(project, required=True).with_overrides(main="chapters/ch1.tex")
        with pytest.raises(ValidationError):
            build_document(config)


class TestManualPasses:
    r"""The fixed sequence used when latexmk is not installed.

    The toolchain is replaced by stand-ins: the engine writes a PDF, a log
    and an ``.aux`` (with or without ``\bibdata``), and ``bibtex`` returns
    whatever status the test asks for.
    """

    @staticmethod
    def _fake_toolchain(
        monkeypatch: pytest.MonkeyPatch,
        *,
        bibdata: bool,
        bibtex_status: int = 0,
        in_child_aux: bool = False,
    ) -> list[str]:
        calls: list[str] = []

        def fake_find(name: str) -> Path | None:
            return None if name == "latexmk" else Path("bin") / name

        def fake_require(name: str, *, purpose: str) -> Path:
            del purpose
            return Path("bin") / name

        def fake_run(
            command: list[str], *, cwd: Path, timeout: float = 0.0, env: object = None
        ) -> CommandResult:
            del timeout, env
            argv = [str(item) for item in command]
            calls.append(argv[0])
            if argv[0] == "bibtex":
                return CommandResult(command=tuple(argv), returncode=bibtex_status, cwd=cwd)
            flag = next(item for item in argv if item.startswith("-output-directory="))
            output_dir = Path(flag.split("=", 1)[1])
            job = Path(argv[-1]).stem
            bib_lines = "\\citation{alpha}\n\\bibdata{bib/refs}\n" if bibdata else ""
            if in_child_aux:
                # \include writes its own .aux; the main one only points at it.
                (output_dir / "structure").mkdir(exist_ok=True)
                (output_dir / "structure" / "backpages.aux").write_text(
                    "\\relax\n" + bib_lines, encoding="utf-8"
                )
                aux = "\\relax\n\\@input{structure/backpages.aux}\n"
            else:
                aux = "\\relax\n" + bib_lines
            (output_dir / f"{job}.aux").write_text(aux, encoding="utf-8")
            (output_dir / f"{job}.pdf").write_bytes(b"%PDF-1.5\n")
            (output_dir / f"{job}.log").write_text("This is XeTeX\n", encoding="utf-8")
            return CommandResult(command=tuple(argv), returncode=0, cwd=cwd)

        monkeypatch.setattr(build_module, "find_executable", fake_find)
        monkeypatch.setattr(build_module, "require_executable", fake_require)
        monkeypatch.setattr(build_module, "run_command", fake_run)
        return calls

    def test_the_spine_never_runs_bibtex(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # coverbridge.tex cites nothing, and bibtex exits 2 on such an .aux.
        (project / "structure").mkdir()
        (project / "structure" / "coverbridge.tex").write_text(
            "\\documentclass{article}\n\\begin{document}\nspine\n\\end{document}\n",
            encoding="utf-8",
        )
        calls = self._fake_toolchain(monkeypatch, bibdata=False, bibtex_status=2)
        report = build_document(load_config(project, required=True), target=BuildTarget.SPINE)
        assert calls == ["xelatex"] * 4
        assert report.summary["output"].endswith("coverbridge.pdf")

    def test_a_document_without_a_bibliography_skips_bibtex(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._fake_toolchain(monkeypatch, bibdata=False, bibtex_status=2)
        build_document(load_config(project, required=True))
        assert "bibtex" not in calls

    def test_bibtex_runs_after_the_first_pass(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._fake_toolchain(monkeypatch, bibdata=True)
        build_document(load_config(project, required=True))
        assert calls == ["xelatex", "bibtex", "xelatex", "xelatex", "xelatex"]

    def test_bibdata_in_an_included_aux_still_runs_bibtex(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # \bibliography inside an \include'd file: the main .aux holds only
        # \@input, and BibTeX follows it to find \bibdata.
        calls = self._fake_toolchain(monkeypatch, bibdata=True, in_child_aux=True)
        build_document(load_config(project, required=True))
        assert calls == ["xelatex", "bibtex", "xelatex", "xelatex", "xelatex"]

    def test_bibtex_warnings_do_not_fail_the_build(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._fake_toolchain(monkeypatch, bibdata=True, bibtex_status=1)
        report = build_document(load_config(project, required=True))
        assert report.summary["output"] != "none"

    def test_bibtex_errors_still_fail_the_build(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._fake_toolchain(monkeypatch, bibdata=True, bibtex_status=2)
        with pytest.raises(BuildFailedError, match="bibtex exited with status 2"):
            build_document(load_config(project, required=True))


@pytest.mark.requires_latex
class TestBuildWithTeX:
    """Only runs where a TeX installation and the template's CJK font are available."""

    def test_builds_the_shipped_template(self, tmp_path: Path, template_root: Path) -> None:
        # The synthetic project is broken on purpose for the audits, so the
        # real template is what has to compile.
        if find_executable("xelatex") is None:
            pytest.skip("xelatex is not installed")
        project = tmp_path / "thesis"
        shutil.copytree(template_root, project)
        config = load_config(project, required=True)
        if not _check_font_fontconfig(config.deps.cjk_font).found:
            pytest.skip(f"the template's CJK font {config.deps.cjk_font!r} is not installed")
        report = build_document(config, target=BuildTarget.THESIS)
        assert report.summary["output"] != "none"
