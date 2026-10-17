"""Tests for the dependency check and the environment doctor.

Both commands must produce a complete report on a machine with no TeX
installation at all.  That is how most machines look on the first day, and
stopping at the first missing program would say almost nothing about the
rest.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from thesis_cli.commands.deps import check_dependencies
from thesis_cli.commands.doctor import (
    MINIMAL_DOCUMENT,
    run_doctor,
    run_smoke_test,
    write_smoke_document,
)
from thesis_cli.config import Engine, ThesisConfig, load_config
from thesis_cli.latex.engine import find_executable

HAS_TEX = find_executable("xelatex") is not None or find_executable("pdflatex") is not None


class TestDependencies:
    """Every requirement is reported, present or not."""

    def test_report_covers_programs_packages_and_fonts(self, config: ThesisConfig) -> None:
        report = check_dependencies(config)
        kinds = {row[1] for row in report.tables[0].rows}
        assert {"program", "package", "font"} <= kinds

    def test_required_packages_are_all_listed(self, config: ThesisConfig) -> None:
        report = check_dependencies(config)
        listed = {row[0] for row in report.tables[0].rows}
        for package in config.required_packages:
            assert f"{package}.sty" in listed

    def test_engine_choice_changes_the_package_list(self, project: Path) -> None:
        unicode_config = load_config(project, required=True)
        unicode_names = {row[0] for row in check_dependencies(unicode_config).tables[0].rows}
        assert "xeCJK.sty" in unicode_names

        pdflatex = unicode_config.with_overrides(
            build=type(unicode_config.build)(engine=Engine.PDFLATEX)
        )
        pdflatex_names = {row[0] for row in check_dependencies(pdflatex).tables[0].rows}
        assert "CJKutf8.sty" in pdflatex_names
        assert "xeCJK.sty" not in pdflatex_names
        # The spine is rotated with \rotatebox; nothing in the template loads CJKvert.
        assert "CJKvert.sty" not in pdflatex_names

    def test_latexmk_is_optional(self, config: ThesisConfig) -> None:
        # build runs a fixed pass sequence when latexmk is missing.
        rows = {row[0]: row for row in check_dependencies(config).tables[0].rows}
        assert rows["latexmk"][2] == "no"
        if find_executable("latexmk") is None:
            finding = next(
                item for item in check_dependencies(config).findings if item.location == "latexmk"
            )
            assert finding.severity.value == "warning"

    def test_dvipdfmx_is_required_only_for_the_dvi_route(self, project: Path) -> None:
        config = load_config(project, required=True)
        assert "dvipdfmx" not in {row[0] for row in check_dependencies(config).tables[0].rows}
        dvi = config.with_overrides(build=type(config.build)(engine=Engine.LATEX_DVIPDFMX))
        assert "dvipdfmx" in {row[0] for row in check_dependencies(dvi).tables[0].rows}

    @pytest.mark.skipif(HAS_TEX, reason="this checks the no-TeX case")
    def test_without_tex_the_report_is_still_complete(self, config: ThesisConfig) -> None:
        report = check_dependencies(config)
        assert report.summary["checked"] > 15
        assert report.summary["kpsewhich"] is False
        assert report.error_count > 0

    @pytest.mark.skipif(HAS_TEX, reason="this checks the no-TeX case")
    def test_unverifiable_checks_are_warnings_not_errors(self, config: ThesisConfig) -> None:
        # Without kpsewhich the packages cannot be checked; that is a
        # different thing from knowing they are missing.
        report = check_dependencies(config)
        unverifiable = [item for item in report.findings if "cannot verify" in item.message]
        assert unverifiable
        assert all(item.severity.value == "warning" for item in unverifiable)

    def test_json_output(self, config: ThesisConfig) -> None:
        payload = json.loads(check_dependencies(config).to_json())
        assert payload["command"] == "deps"
        assert "engine" in payload["summary"]


class TestSmokeDocument:
    """The smallest document that proves the toolchain can typeset CJK."""

    def test_is_written_to_disk(self, tmp_path: Path) -> None:
        path = write_smoke_document(tmp_path)
        assert path.is_file()
        assert path.read_text(encoding="utf-8") == MINIMAL_DOCUMENT

    def test_contains_what_it_claims_to_test(self) -> None:
        assert "\\documentclass" in MINIMAL_DOCUMENT
        assert "\\begin{document}" in MINIMAL_DOCUMENT
        assert "中文" in MINIMAL_DOCUMENT
        assert "\\begin{equation}" in MINIMAL_DOCUMENT

    def test_the_checked_in_fixture_matches_the_constant(self) -> None:
        fixture = Path(__file__).resolve().parent / "fixtures" / "minimal_thesis" / "minimal.tex"
        assert fixture.is_file(), "the fixture must exist for offline inspection"
        assert fixture.read_text(encoding="utf-8") == MINIMAL_DOCUMENT, (
            "fixtures/minimal_thesis/minimal.tex has drifted from MINIMAL_DOCUMENT; "
            "regenerate it with write_smoke_document()"
        )

    @pytest.mark.skipif(HAS_TEX, reason="this checks the no-TeX case")
    def test_smoke_test_is_skipped_without_an_engine(self, config: ThesisConfig) -> None:
        result = run_smoke_test(config)
        assert result.attempted is False
        assert result.succeeded is False
        assert "not installed" in result.message

    def test_smoke_test_leaves_nothing_behind(self, config: ThesisConfig, tmp_path: Path) -> None:
        before = set(tmp_path.iterdir())
        run_smoke_test(config)
        assert set(tmp_path.iterdir()) == before


class TestDoctor:
    """The whole-environment report."""

    def test_reports_the_python_environment(self, config: ThesisConfig) -> None:
        report = run_doctor(config, smoke=False)
        components = {row[0] for row in report.tables[0].rows}
        assert {"python", "platform", "numpy", "typer"} <= components

    def test_missing_template_files_are_warnings(self, config: ThesisConfig) -> None:
        report = run_doctor(config, smoke=False)
        missing = [item for item in report.findings if item.kind == "missing-template-file"]
        # The synthetic project is not built from the template, so every
        # template file is absent: each one must be reported individually.
        assert len(missing) >= 10
        assert all(item.severity.value == "warning" for item in missing)

    def test_a_template_project_has_no_layout_warnings(
        self, tmp_path: Path, template_root: Path
    ) -> None:
        from thesis_cli.commands.init_project import init_project

        destination = tmp_path / "mythesis"
        init_project(destination, template_dir=template_root)
        report = run_doctor(load_config(destination, required=True), smoke=False)
        assert not [item for item in report.findings if item.kind == "missing-template-file"]

    def test_missing_main_document_is_an_error(self, project: Path) -> None:
        (project / "thesis.tex").unlink()
        report = run_doctor(load_config(project, required=True), smoke=False)
        assert any(item.kind == "missing-main" for item in report.findings)

    def test_missing_config_is_reported(self, tmp_path: Path) -> None:
        report = run_doctor(ThesisConfig.default(tmp_path), smoke=False)
        assert any(item.kind == "missing-config" for item in report.findings)

    def test_outside_a_project_only_the_machine_is_checked(self, tmp_path: Path) -> None:
        # The README has a fresh clone run doctor before any project exists.
        report = run_doctor(ThesisConfig.default(tmp_path), smoke=False)
        kinds = {item.kind for item in report.findings}
        assert not kinds & {"missing-template-file", "missing-main"}
        assert "Project layout" not in {table.title for table in report.tables}

    def test_an_explicit_template_dir_is_checked_without_a_config(self, tmp_path: Path) -> None:
        report = run_doctor(ThesisConfig.default(tmp_path), smoke=False, template_dir=tmp_path)
        assert any(item.kind == "missing-template-file" for item in report.findings)
        assert not any(item.kind == "missing-main" for item in report.findings)

    def test_summary_fields(self, config: ThesisConfig) -> None:
        summary = run_doctor(config, smoke=False).summary
        for key in ("config_file", "engine", "latexmk", "smoke_attempted", "disk_free_mb"):
            assert key in summary

    def test_json_output(self, config: ThesisConfig) -> None:
        payload = json.loads(run_doctor(config, smoke=False).to_json())
        assert payload["command"] == "doctor"
