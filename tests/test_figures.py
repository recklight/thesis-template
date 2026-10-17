r"""Tests for graphics resolution.

The case this module is about: ``\includegraphics{figures/plot.png}``
against ``Plot.png`` on disk.  On Windows and on a default macOS volume the
filesystem answers "yes, that file exists"; on a Linux CI runner it answers
"no" and the build dies.  Resolution must therefore compare names as
strings, never by asking the filesystem, and these tests pin that behaviour
down on whatever platform they happen to run on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import write_tiny_png
from thesis_cli.commands.figures import (
    DirectoryIndex,
    FigureStatus,
    audit_figures,
    resolve_reference,
)
from thesis_cli.config import ThesisConfig, load_config
from thesis_cli.parsers.tex_scanner import GraphicRef, SourceLocation, scan_document
from thesis_cli.report import Severity


def _reference(raw: str, *, macro: str = "includegraphics") -> GraphicRef:
    return GraphicRef(
        raw=raw,
        macro=macro,
        location=SourceLocation(file=Path("body.tex"), line=1),
    )


class TestDirectoryIndex:
    """Exact-string directory listing."""

    def test_exact_match(self, tmp_path: Path) -> None:
        write_tiny_png(tmp_path / "Plot.png")
        index = DirectoryIndex()
        assert index.contains_exact(tmp_path, "Plot.png") is True
        assert index.contains_exact(tmp_path, "plot.png") is False

    def test_actual_case_reports_the_real_name(self, tmp_path: Path) -> None:
        write_tiny_png(tmp_path / "Case_1_test.png")
        assert DirectoryIndex().actual_case(tmp_path, "case_1_test.png") == "Case_1_test.png"

    def test_missing_directory_is_empty(self, tmp_path: Path) -> None:
        assert DirectoryIndex().entries(tmp_path / "nowhere") == {}


class TestResolveReference:
    """One reference against the filesystem."""

    def test_exact_hit(self, tmp_path: Path) -> None:
        write_tiny_png(tmp_path / "figures" / "plot.png")
        result = resolve_reference(
            _reference("figures/plot.png"),
            root=tmp_path,
            search_dirs=(),
            extensions=(".pdf", ".png"),
        )
        assert result.status is FigureStatus.OK
        assert result.resolved is not None
        assert result.resolved.name == "plot.png"

    def test_case_mismatch_is_detected_on_every_platform(self, tmp_path: Path) -> None:
        # The source says case_1_test.png, the disk has Case_1_test.png.
        # This has to fail here even on a case-insensitive filesystem,
        # because it fails on a case-sensitive one.
        write_tiny_png(tmp_path / "figures" / "Case_1_test.png")
        result = resolve_reference(
            _reference("figures/case_1_test.png"),
            root=tmp_path,
            search_dirs=(),
            extensions=(".png",),
        )
        assert result.status is FigureStatus.CASE_MISMATCH
        assert result.actual_name == "figures/Case_1_test.png"

    def test_extension_is_probed_in_configured_order(self, tmp_path: Path) -> None:
        write_tiny_png(tmp_path / "figures" / "plot.png")
        result = resolve_reference(
            _reference("figures/plot"),
            root=tmp_path,
            search_dirs=(),
            extensions=(".pdf", ".png", ".eps"),
        )
        assert result.status is FigureStatus.OK
        assert result.resolved is not None
        assert result.resolved.suffix == ".png"

    def test_first_extension_wins(self, tmp_path: Path) -> None:
        write_tiny_png(tmp_path / "figures" / "plot.png")
        (tmp_path / "figures" / "plot.pdf").write_bytes(b"%PDF-1.4\n")
        result = resolve_reference(
            _reference("figures/plot"),
            root=tmp_path,
            search_dirs=(),
            extensions=(".pdf", ".png"),
        )
        assert result.resolved is not None
        assert result.resolved.suffix == ".pdf"

    def test_eps_reference_is_satisfied_by_the_converted_pdf(self, tmp_path: Path) -> None:
        # epstopdf turns plot.eps into plot-eps-converted-to.pdf, which
        # pdfLaTeX uses when the .eps itself is gone.
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "plot-eps-converted-to.pdf").write_bytes(b"%PDF-1.4\n")
        result = resolve_reference(
            _reference("figures/plot.eps"),
            root=tmp_path,
            search_dirs=(),
            extensions=(".pdf", ".eps"),
        )
        assert result.status is FigureStatus.OK

    def test_an_explicit_extension_is_not_swapped_for_another(self, tmp_path: Path) -> None:
        # graphicx stops with "File `figures/plot.png' not found" here.
        (tmp_path / "figures").mkdir()
        (tmp_path / "figures" / "plot.pdf").write_bytes(b"%PDF-1.4\n")
        for raw in ("figures/plot.png", "figures/plot.eps"):
            result = resolve_reference(
                _reference(raw),
                root=tmp_path,
                search_dirs=(),
                extensions=(".pdf", ".png", ".eps"),
            )
            assert result.status is FigureStatus.MISSING, raw

    def test_a_mis_cased_directory_is_a_case_mismatch(self, tmp_path: Path) -> None:
        # Windows finds Figures/plot.png in figures/; Linux does not.
        write_tiny_png(tmp_path / "figures" / "plot.png")
        result = resolve_reference(
            _reference("Figures/plot.png"),
            root=tmp_path,
            search_dirs=(),
            extensions=(".png",),
        )
        assert result.status is FigureStatus.CASE_MISMATCH
        assert result.actual_name == "figures/plot.png"

    def test_a_mis_cased_nested_directory_is_a_case_mismatch(self, tmp_path: Path) -> None:
        write_tiny_png(tmp_path / "figures" / "sub" / "inner.png")
        result = resolve_reference(
            _reference("figures/Sub/inner"),
            root=tmp_path,
            search_dirs=(),
            extensions=(".pdf", ".png"),
        )
        assert result.status is FigureStatus.CASE_MISMATCH
        assert result.actual_name == "figures/sub/inner.png"

    def test_a_missing_directory_is_missing(self, tmp_path: Path) -> None:
        write_tiny_png(tmp_path / "figures" / "plot.png")
        result = resolve_reference(
            _reference("graphs/plot.png"),
            root=tmp_path,
            search_dirs=(),
            extensions=(".png",),
        )
        assert result.status is FigureStatus.MISSING

    def test_leading_dot_slash_is_normalised(self, tmp_path: Path) -> None:
        write_tiny_png(tmp_path / "figures" / "plot.png")
        result = resolve_reference(
            _reference("./figures/plot.png"),
            root=tmp_path,
            search_dirs=(),
            extensions=(".png",),
        )
        assert result.status is FigureStatus.OK

    def test_search_directory_is_used(self, tmp_path: Path) -> None:
        write_tiny_png(tmp_path / "figures" / "plot.png")
        result = resolve_reference(
            _reference("plot.png"),
            root=tmp_path,
            search_dirs=(tmp_path / "figures",),
            extensions=(".png",),
        )
        assert result.status is FigureStatus.OK

    def test_missing_file_reports_what_was_searched(self, tmp_path: Path) -> None:
        result = resolve_reference(
            _reference("figures/absent.png"),
            root=tmp_path,
            search_dirs=(),
            extensions=(".png",),
        )
        assert result.status is FigureStatus.MISSING
        assert any("absent.png" in item for item in result.searched)

    def test_macro_built_path_is_not_an_error(self, tmp_path: Path) -> None:
        result = resolve_reference(
            _reference(r"\thesisWatermarkImage"),
            root=tmp_path,
            search_dirs=(),
            extensions=(".png",),
        )
        assert result.status is FigureStatus.MACRO


class TestAuditFigures:
    """The command as a whole."""

    def test_clean_project_has_no_findings(self, config: ThesisConfig) -> None:
        report = audit_figures(config)
        assert report.error_count == 0
        assert report.summary["ok"] == 2

    def test_case_mismatch_becomes_an_error_finding(self, project: Path) -> None:
        (project / "figures" / "plot.png").rename(project / "figures" / "Plot.png")
        report = audit_figures(load_config(project, required=True))
        kinds = {item.kind for item in report.findings}
        assert "figure-case-mismatch" in kinds
        mismatch = next(item for item in report.findings if item.kind == "figure-case-mismatch")
        assert mismatch.severity is Severity.ERROR
        assert "Plot.png" in mismatch.message
        assert "ch1.tex" in mismatch.location

    def test_missing_file_becomes_an_error_finding(self, project: Path) -> None:
        (project / "figures" / "plot.png").unlink()
        report = audit_figures(load_config(project, required=True))
        assert any(item.kind == "figure-missing" for item in report.findings)

    def test_unused_graphics_are_warned_about(self, project: Path) -> None:
        write_tiny_png(project / "figures" / "orphan.png")
        report = audit_figures(load_config(project, required=True))
        unused = [item for item in report.findings if item.kind == "figure-unused"]
        assert len(unused) == 1
        assert "orphan.png" in unused[0].location

    def test_epstopdf_output_is_not_reported_as_unused(self, project: Path) -> None:
        write_tiny_png(project / "figures" / "plot-eps-converted-to.pdf")
        report = audit_figures(load_config(project, required=True))
        assert not any("converted-to" in item.location for item in report.findings)

    def test_unused_graphics_are_found_with_a_relative_root(
        self, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # figure_dirs are resolved paths; a hand-built config may still give
        # the root as a relative one.
        write_tiny_png(project / "figures" / "orphan.png")
        monkeypatch.chdir(project.parent)
        config = load_config(project, required=True).with_overrides(root=Path(project.name))
        unused = [item for item in audit_figures(config).findings if item.kind == "figure-unused"]
        assert [item.location for item in unused] == ["figures/orphan.png"]

    def test_unused_report_can_be_switched_off(self, project: Path) -> None:
        write_tiny_png(project / "figures" / "orphan.png")
        report = audit_figures(load_config(project, required=True), report_unused=False)
        assert not any(item.kind == "figure-unused" for item in report.findings)

    def test_report_is_json_serialisable(self, config: ThesisConfig) -> None:
        import json

        payload = json.loads(audit_figures(config).to_json())
        assert payload["command"] == "figures"
        assert "summary" in payload

    def test_document_can_be_supplied(self, config: ThesisConfig) -> None:
        document = scan_document(
            config.main_path,
            config.root,
            macros=config.figure_macros,
            label_macros=config.label_macros,
        )
        report = audit_figures(config, document=document)
        assert report.summary["references"] == 2


class TestCaseSiblings:
    """Two files whose names differ only in case can coexist.

    A case-sensitive filesystem holds ``Plot.png`` and ``plot.png`` side by
    side.  An index that kept one real name per lowercased key would report
    the correctly spelled reference as a case mismatch against its sibling,
    a false alarm on exactly the platform this check exists for.
    """

    def test_both_spellings_are_exact_matches(self, tmp_path: Path) -> None:
        index = DirectoryIndex()
        # Seed the cache directly: the host filesystem may be case
        # insensitive, which would make the two files impossible to create.
        index._cache[tmp_path] = {"plot.png": ("Plot.png", "plot.png")}
        assert index.contains_exact(tmp_path, "Plot.png") is True
        assert index.contains_exact(tmp_path, "plot.png") is True
        assert index.contains_exact(tmp_path, "PLOT.png") is False

    def test_a_third_spelling_still_reports_a_real_name(self, tmp_path: Path) -> None:
        index = DirectoryIndex()
        index._cache[tmp_path] = {"plot.png": ("Plot.png", "plot.png")}
        assert index.actual_case(tmp_path, "PLOT.png") == "Plot.png"

    def test_entries_are_sorted_so_the_report_is_stable(self, tmp_path: Path) -> None:
        write_tiny_png(tmp_path / "Case_1_test.png")
        assert DirectoryIndex().entries(tmp_path) == {"case_1_test.png": ("Case_1_test.png",)}
