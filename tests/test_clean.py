"""Tests for artefact identification and removal.

``clean`` is the only command here that can destroy work, so the tests are
written from the destructive side: every case that must *not* be deleted has
its own test, and the "spared" reasons are asserted, not just the deletions.
"""

from __future__ import annotations

from pathlib import Path

from conftest import write_tiny_png
from thesis_cli.commands.clean import clean_project
from thesis_cli.config import ThesisConfig, load_config
from thesis_cli.latex.artifacts import (
    PROTECTED_EXTENSIONS,
    ArtifactReason,
    execute_clean,
    plan_clean,
)


def _names(plan: object) -> set[str]:
    return {item.path.name for item in plan.candidates}  # type: ignore[attr-defined]


class TestArtefactSelection:
    """What ends up in the plan."""

    def test_artefact_with_a_matching_source_is_selected(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        (tmp_path / "thesis.aux").write_text("x", encoding="utf-8")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        assert _names(plan) == {"thesis.aux"}
        assert plan.candidates[0].reason is ArtifactReason.ENGINE_ARTIFACT
        assert plan.candidates[0].source is not None

    def test_orphan_artefact_is_spared(self, tmp_path: Path) -> None:
        # Nothing proves a TeX run of *this* project produced the file, so
        # it is spared: it may belong to another document sharing the folder.
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        (tmp_path / "other_project.aux").write_text("x", encoding="utf-8")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        assert _names(plan) == set()
        spared = {path.name for path, _ in plan.skipped}
        assert "other_project.aux" in spared

    def test_every_artefact_extension_is_recognised(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        for extension in (".aux", ".bbl", ".blg", ".log", ".toc", ".lof", ".lot", ".out"):
            (tmp_path / f"thesis{extension}").write_text("x", encoding="utf-8")
        (tmp_path / "thesis.synctex.gz").write_bytes(b"x")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        assert "thesis.synctex.gz" in _names(plan)
        assert len(plan.candidates) == 9

    def test_epstopdf_output_is_a_derived_graphic(self, tmp_path: Path) -> None:
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "plot.eps").write_text("%!PS", encoding="utf-8")
        (tmp_path / "figures" / "plot-eps-converted-to.pdf").write_bytes(b"%PDF")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        assert _names(plan) == {"plot-eps-converted-to.pdf"}
        assert plan.candidates[0].reason is ArtifactReason.DERIVED_GRAPHIC

    def test_editor_backups_and_thumbnails(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex.bak").write_text("x", encoding="utf-8")
        (tmp_path / "notes.asv").write_text("x", encoding="utf-8")
        (tmp_path / "Thumbs.db").write_bytes(b"x")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        assert _names(plan) == {"thesis.tex.bak", "notes.asv", "Thumbs.db"}
        assert {item.reason for item in plan.candidates} == {ArtifactReason.EDITOR_BACKUP}

    def test_output_pdf_is_opt_in(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        (tmp_path / "thesis.pdf").write_bytes(b"%PDF")
        assert _names(plan_clean(ThesisConfig.default(tmp_path))) == set()
        with_outputs = plan_clean(ThesisConfig.default(tmp_path), include_outputs=True)
        assert _names(with_outputs) == {"thesis.pdf"}
        assert with_outputs.candidates[0].reason is ArtifactReason.OUTPUT

    def test_unrelated_pdf_is_spared_even_with_outputs(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        (tmp_path / "reference_paper.pdf").write_bytes(b"%PDF")
        plan = plan_clean(ThesisConfig.default(tmp_path), include_outputs=True)
        assert _names(plan) == set()
        assert any("reference_paper.pdf" in path.name for path, _ in plan.skipped)

    def test_output_directory_is_scanned(self, tmp_path: Path) -> None:
        # build/ never holds a .tex: thesis.tex in the project root is what
        # proves build/thesis.aux came from this project.
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        build = tmp_path / "build"
        build.mkdir()
        for extension in (".aux", ".log", ".toc", ".bbl", ".xdv", ".fdb_latexmk"):
            (build / f"thesis{extension}").write_text("x", encoding="utf-8")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        assert len(plan.candidates) == 6
        sources = {item.source for item in plan.candidates}
        assert sources == {(tmp_path / "thesis.tex").resolve()}
        assert plan.skipped == ()

    def test_spine_artefacts_in_the_output_directory_are_candidates(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        (tmp_path / "structure").mkdir()
        (tmp_path / "structure" / "coverbridge.tex").write_text("x", encoding="utf-8")
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "coverbridge.aux").write_text("x", encoding="utf-8")
        (tmp_path / "build" / "coverbridge.log").write_text("x", encoding="utf-8")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        assert _names(plan) == {"coverbridge.aux", "coverbridge.log"}
        assert {item.source.name for item in plan.candidates if item.source} == {"coverbridge.tex"}

    def test_included_chapter_artefacts_keep_their_folder(self, tmp_path: Path) -> None:
        # \include writes build/chapters/ch1.aux for chapters/ch1.tex.
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        (tmp_path / "chapters").mkdir()
        (tmp_path / "chapters" / "ch1.tex").write_text("x", encoding="utf-8")
        (tmp_path / "build" / "chapters").mkdir(parents=True)
        (tmp_path / "build" / "chapters" / "ch1.aux").write_text("x", encoding="utf-8")
        assert _names(plan_clean(ThesisConfig.default(tmp_path))) == {"ch1.aux"}

    def test_an_orphan_in_the_output_directory_is_spared(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "other_document.aux").write_text("x", encoding="utf-8")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        assert _names(plan) == set()
        assert {path.name for path, _ in plan.skipped} == {"other_document.aux"}

    def test_the_spine_pdf_counts_as_an_output(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        (tmp_path / "structure").mkdir()
        (tmp_path / "structure" / "coverbridge.tex").write_text("x", encoding="utf-8")
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "coverbridge.pdf").write_bytes(b"%PDF")
        plan = plan_clean(ThesisConfig.default(tmp_path), include_outputs=True)
        assert _names(plan) == {"coverbridge.pdf"}


class TestSourcesAreNeverTouched:
    """Rule 3: authored formats are not candidates under any configuration."""

    def test_source_extensions_are_protected(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        for name in ("thesis_report.cls", "extra.sty", "refs.bib", "ieeetr.bst", "notes.md"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        write_tiny_png(tmp_path / "figures" / "plot.png")
        assert _names(plan_clean(ThesisConfig.default(tmp_path))) == set()

    def test_a_pattern_cannot_promote_a_source_to_a_candidate(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text(
            '[clean]\nextra_patterns = ["*.tex", "*.bib"]\n', encoding="utf-8"
        )
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        (tmp_path / "refs.bib").write_text("x", encoding="utf-8")
        config = load_config(tmp_path, required=True)
        assert _names(plan_clean(config)) == set()

    def test_the_protected_set_covers_the_obvious_formats(self) -> None:
        assert {".tex", ".cls", ".sty", ".bib", ".bst", ".png", ".eps"} <= PROTECTED_EXTENSIONS


class TestExecution:
    """Turning a plan into deletions."""

    def test_dry_run_deletes_nothing(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        artefact = tmp_path / "thesis.aux"
        artefact.write_text("x", encoding="utf-8")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        removed, failures = execute_clean(plan, dry_run=True)
        assert removed == [artefact]
        assert failures == []
        assert artefact.is_file()

    def test_real_run_deletes(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        artefact = tmp_path / "thesis.aux"
        artefact.write_text("x", encoding="utf-8")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        removed, failures = execute_clean(plan)
        assert removed == [artefact]
        assert failures == []
        assert not artefact.exists()
        assert (tmp_path / "thesis.tex").is_file()

    def test_a_file_that_vanished_between_plan_and_run_is_not_an_error(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        artefact = tmp_path / "thesis.aux"
        artefact.write_text("x", encoding="utf-8")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        artefact.unlink()
        removed, failures = execute_clean(plan)
        assert removed == []
        assert failures == []

    def test_plan_totals(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        (tmp_path / "thesis.aux").write_text("12345", encoding="utf-8")
        plan = plan_clean(ThesisConfig.default(tmp_path))
        assert plan.total_bytes == 5
        assert plan.by_reason() == {"engine-artifact": 1}


class TestCleanCommand:
    """The report the CLI renders."""

    def test_reports_removals(self, project: Path) -> None:
        (project / "thesis.aux").write_text("x", encoding="utf-8")
        report = clean_project(load_config(project, required=True))
        assert report.summary["removed"] == 1
        assert report.error_count == 0
        assert not (project / "thesis.aux").exists()

    def test_nothing_to_do_is_reported_as_information(self, project: Path) -> None:
        report = clean_project(load_config(project, required=True))
        assert report.summary["candidates"] == 0
        assert any(item.kind == "already-clean" for item in report.findings)
        assert report.is_clean

    def test_dry_run_keeps_the_files(self, project: Path) -> None:
        (project / "thesis.log").write_text("x", encoding="utf-8")
        report = clean_project(load_config(project, required=True), dry_run=True)
        assert report.summary["dry_run"] is True
        assert (project / "thesis.log").is_file()

    def test_spared_files_can_be_listed(self, project: Path) -> None:
        (project / "unrelated.aux").write_text("x", encoding="utf-8")
        report = clean_project(load_config(project, required=True), show_skipped=True)
        assert report.summary["spared"] == 1
        titles = {table.title for table in report.tables}
        assert "Spared" in titles


class TestOutputsAreLocated:
    """``--outputs`` must not mistake a figure for the compiled thesis.

    A graphic named ``figures/thesis.pdf`` shares the main document's stem.
    Only the copies in the project root and in the output directory are
    build outputs; the figure has to survive.
    """

    @staticmethod
    def _project(root: Path) -> ThesisConfig:
        (root / "thesis.tex").write_text("x", encoding="utf-8")
        (root / "thesis.pdf").write_text("x", encoding="utf-8")
        (root / "figures").mkdir()
        (root / "figures" / "thesis.pdf").write_text("x", encoding="utf-8")
        (root / "build").mkdir()
        (root / "build" / "thesis.pdf").write_text("x", encoding="utf-8")
        return ThesisConfig.default(root)

    def test_a_figure_sharing_the_main_stem_is_spared(self, tmp_path: Path) -> None:
        plan = plan_clean(self._project(tmp_path), include_outputs=True)
        names = {candidate.path.relative_to(tmp_path).as_posix() for candidate in plan.candidates}
        assert "figures/thesis.pdf" not in names
        assert ("figures/thesis.pdf", "not a build output of the configured main document") in {
            (path.relative_to(tmp_path).as_posix(), why) for path, why in plan.skipped
        }

    def test_the_root_and_output_copies_are_candidates(self, tmp_path: Path) -> None:
        plan = plan_clean(self._project(tmp_path), include_outputs=True)
        names = {candidate.path.relative_to(tmp_path).as_posix() for candidate in plan.candidates}
        assert names == {"thesis.pdf", "build/thesis.pdf"}
