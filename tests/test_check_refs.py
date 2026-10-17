r"""Tests for the four-way cross-reference audit.

Each of the four comparisons gets its own failing case, because each of them
catches a defect that LaTeX itself reports only as a warning in the log, or
not at all:

* a ``\cite`` with no entry prints ``[?]``;
* an entry nothing cites never reaches the reference list;
* a ``\ref`` with no label prints ``??``;
* a label defined twice silently resolves to one of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from thesis_cli.commands.check_refs import check_references
from thesis_cli.config import ThesisConfig, load_config
from thesis_cli.errors import ProjectLayoutError
from thesis_cli.report import Severity


def _kinds(report: object) -> set[str]:
    return {item.kind for item in report.findings}  # type: ignore[attr-defined]


def _append(path: Path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


class TestCleanProject:
    """The synthetic project is meant to pass."""

    def test_no_findings(self, config: ThesisConfig) -> None:
        report = check_references(config)
        assert report.findings == ()
        assert report.is_clean

    def test_summary_counts(self, config: ThesisConfig) -> None:
        summary = check_references(config).summary
        assert summary["cited_keys"] == 2
        assert summary["bib_entries"] == 2
        assert summary["labels"] >= 5

    def test_exit_code_is_zero_even_under_strict(self, config: ThesisConfig) -> None:
        report = check_references(config)
        assert report.exit_code() == 0
        assert report.exit_code(strict=True) == 0


class TestUndefinedCitation:
    """Comparison 1: source key not in any database."""

    def test_is_an_error(self, project: Path) -> None:
        _append(project / "chapters" / "ch2.tex", "\n未知的引用 \\cite{nowhere}.\n")
        report = check_references(load_config(project, required=True))
        finding = next(item for item in report.findings if item.kind == "undefined-citation")
        assert finding.severity is Severity.ERROR
        assert "nowhere" in finding.message
        assert "ch2.tex" in finding.location
        assert report.exit_code() != 0


class TestUnusedBibEntry:
    """Comparison 2: database key nothing cites."""

    def test_is_a_warning(self, project: Path) -> None:
        _append(
            project / "bib" / "refs.bib",
            "\n@misc{orphan, title = {Never cited}, year = {2020}}\n",
        )
        report = check_references(load_config(project, required=True))
        finding = next(item for item in report.findings if item.kind == "unused-bib-entry")
        assert finding.severity is Severity.WARNING
        assert "orphan" in finding.message
        assert report.exit_code() == 0
        assert report.exit_code(strict=True) != 0

    def test_nocite_star_suppresses_the_warning(self, project: Path) -> None:
        _append(
            project / "bib" / "refs.bib",
            "\n@misc{orphan, title = {Never cited}, year = {2020}}\n",
        )
        _append(project / "chapters" / "ch2.tex", "\n\\nocite{*}\n")
        report = check_references(load_config(project, required=True))
        assert "unused-bib-entry" not in _kinds(report)


class TestUndefinedReference:
    """Comparison 3: \\ref target with no \\label."""

    def test_is_an_error(self, project: Path) -> None:
        _append(project / "chapters" / "ch2.tex", "\n參見 \\ref{fig:nonexistent}。\n")
        report = check_references(load_config(project, required=True))
        finding = next(item for item in report.findings if item.kind == "undefined-reference")
        assert finding.severity is Severity.ERROR
        assert "fig:nonexistent" in finding.message

    def test_eqref_is_covered_too(self, project: Path) -> None:
        _append(project / "chapters" / "ch2.tex", "\n\\eqref{eq:missing}\n")
        report = check_references(load_config(project, required=True))
        assert "undefined-reference" in _kinds(report)


class TestDuplicateLabel:
    """Comparison 4: the same label defined twice."""

    def test_is_an_error_naming_both_places(self, project: Path) -> None:
        _append(project / "chapters" / "ch2.tex", "\n\\label{ch:intro}\n")
        report = check_references(load_config(project, required=True))
        finding = next(item for item in report.findings if item.kind == "duplicate-label")
        assert finding.severity is Severity.ERROR
        assert "ch:intro" in finding.message
        assert "ch1.tex" in finding.detail
        assert "ch2.tex" in finding.detail


class TestBibliographyQuality:
    """Database defects are surfaced alongside the reference audit."""

    def test_trailing_comma_reaches_the_report(self, project: Path) -> None:
        (project / "bib" / "refs.bib").write_text(
            "@article{alpha, author={A. One, }, title={T}, journal={J}, year={2001}}\n"
            "@misc{beta, title={T}, year={2002}}\n",
            encoding="utf-8",
        )
        report = check_references(load_config(project, required=True))
        assert "trailing-comma-in-names" in _kinds(report)

    def test_conflicting_duplicate_across_two_databases(self, project: Path) -> None:
        second = project / "bib" / "extra.bib"
        second.write_text("@misc{alpha, title={A different alpha}}\n", encoding="utf-8")
        (project / "thesis.toml").write_text(
            '[project]\nmain = "thesis.tex"\n\n'
            '[bib]\nfiles = ["bib/refs.bib", "bib/extra.bib"]\n\n'
            '[template]\nwatermark = "off"\n',
            encoding="utf-8",
        )
        report = check_references(load_config(project, required=True))
        assert "conflicting-duplicate" in _kinds(report)


class TestConfigurationDrift:
    """The source and thesis.toml must agree about the database."""

    def test_bibliography_command_mismatch_is_warned(self, project: Path) -> None:
        main = project / "thesis.tex"
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                r"\bibliography{bib/refs}", r"\bibliography{bib/other}"
            ),
            encoding="utf-8",
        )
        report = check_references(load_config(project, required=True))
        finding = next(item for item in report.findings if item.kind == "bib-mismatch")
        assert "other" in finding.message

    def test_same_file_name_in_another_folder_is_a_mismatch(self, project: Path) -> None:
        main = project / "thesis.tex"
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                r"\bibliography{bib/refs}", r"\bibliography{old/refs}"
            ),
            encoding="utf-8",
        )
        report = check_references(load_config(project, required=True))
        assert "bib-mismatch" in _kinds(report)

    def test_a_bib_suffix_in_the_source_is_not_a_mismatch(self, project: Path) -> None:
        main = project / "thesis.tex"
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                r"\bibliography{bib/refs}", r"\bibliography{./bib/refs.bib}"
            ),
            encoding="utf-8",
        )
        report = check_references(load_config(project, required=True))
        assert "bib-mismatch" not in _kinds(report)

    def test_bibliography_style_mismatch_is_warned(self, project: Path) -> None:
        main = project / "thesis.tex"
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                r"\bibliographystyle{ieeetr}", r"\bibliographystyle{plain}"
            ),
            encoding="utf-8",
        )
        report = check_references(load_config(project, required=True))
        finding = next(item for item in report.findings if item.kind == "bib-mismatch")
        assert "plain" in finding.message
        assert "ieeetr" in finding.message

    def test_unresolvable_input_is_an_error(self, project: Path) -> None:
        _append(project / "thesis.tex", "")
        main = project / "thesis.tex"
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                r"\input{chapters/ch2.tex}", r"\input{chapters/ch9.tex}"
            ),
            encoding="utf-8",
        )
        report = check_references(load_config(project, required=True))
        assert "missing-input" in _kinds(report)


class TestAuxCrossCheck:
    """A stale .aux means the PDF on disk is out of date."""

    def test_citation_absent_from_the_aux_is_warned(self, project: Path) -> None:
        (project / "thesis.aux").write_text(
            "\\relax\n\\citation{alpha}\n\\bibdata{bib/refs}\n", encoding="utf-8"
        )
        report = check_references(load_config(project, required=True))
        finding = next(item for item in report.findings if item.kind == "stale-aux")
        assert "beta" in finding.detail

    def test_the_aux_of_a_real_build_passes_strict(self, project: Path) -> None:
        # LaTeX copies \bibliography{bib/refs} into the .aux as it stands,
        # directory included.
        (project / "build").mkdir()
        (project / "build" / "thesis.aux").write_text(
            "\\relax\n\\citation{alpha}\n\\citation{beta}\n"
            "\\bibstyle{ieeetr}\n\\bibdata{bib/refs}\n",
            encoding="utf-8",
        )
        report = check_references(load_config(project, required=True))
        assert report.findings == ()
        assert report.exit_code(strict=True) == 0

    def test_bibstyle_drift_is_warned(self, project: Path) -> None:
        (project / "thesis.aux").write_text(
            "\\relax\n\\citation{alpha}\n\\citation{beta}\n"
            "\\bibstyle{plain}\n\\bibdata{bib/refs}\n",
            encoding="utf-8",
        )
        report = check_references(load_config(project, required=True))
        mismatches = [item for item in report.findings if item.kind == "bib-mismatch"]
        assert len(mismatches) == 1
        assert "plain" in mismatches[0].message

    def test_bibdata_drift_is_warned(self, project: Path) -> None:
        (project / "thesis.aux").write_text(
            "\\relax\n\\citation{alpha}\n\\citation{beta}\n\\bibdata{ensemble}\n",
            encoding="utf-8",
        )
        report = check_references(load_config(project, required=True))
        mismatches = [item for item in report.findings if item.kind == "bib-mismatch"]
        assert any("ensemble" in item.message for item in mismatches)

    def test_aux_can_be_skipped(self, project: Path) -> None:
        (project / "thesis.aux").write_text("\\relax\n\\citation{alpha}\n", encoding="utf-8")
        report = check_references(load_config(project, required=True), use_aux=False)
        assert "stale-aux" not in _kinds(report)
        assert report.summary["aux"] == "not read"


class TestErrorHandling:
    """Failure modes of the command itself."""

    def test_missing_main_document(self, project: Path) -> None:
        (project / "thesis.tex").unlink()
        try:
            check_references(load_config(project, required=True))
        except ProjectLayoutError as error:
            assert "Main document not found" in str(error)
            assert error.exit_code == 3
        else:  # pragma: no cover - the call must raise
            raise AssertionError("expected ProjectLayoutError")

    def test_json_output_is_well_formed(self, config: ThesisConfig) -> None:
        payload = json.loads(check_references(config).to_json())
        assert payload["command"] == "check-refs"
        assert payload["counts"] == {"error": 0, "warning": 0, "info": 0}

    def test_an_unresolved_root_is_not_a_bib_mismatch(
        self, config: ThesisConfig, project: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A ThesisConfig built by hand can carry a relative root, or an
        # absolute one with '..' in it, while bib_paths are always resolved.
        monkeypatch.chdir(project.parent)
        for root in (Path(project.name), project / "chapters" / ".."):
            report = check_references(config.with_overrides(root=root))
            assert "bib-mismatch" not in _kinds(report)
