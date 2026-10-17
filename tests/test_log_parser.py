r"""Tests for the TeX log classifier.

The sample log is assembled from the message shapes that matter: a
file:line error, an overfull box with its size, undefined references and
citations, a rerun request, and the ``mktextfm`` line that is the only
visible trace of a silently substituted font.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from thesis_cli.latex.log_parser import (
    MessageKind,
    Severity,
    parse_log,
    parse_log_text,
    parse_missfont,
    summarise,
)

SAMPLE_LOG = r"""This is XeTeX, Version 3.141592653-2.6-0.999995 (TeX Live 2023)
**thesis.tex
(./thesis.tex
LaTeX2e <2023-11-01>
Overfull \hbox (30.14pt too wide) in paragraph at lines 365--367
Overfull \hbox (2.5pt too wide) in paragraph at lines 12--14
Underfull \hbox (badness 10000) in paragraph at lines 40--42
LaTeX Warning: Reference `fig:x' on page 5 undefined on input line 100.
LaTeX Warning: Citation `ghost' on page 5 undefined on input line 101.
LaTeX Warning: Label `dup' multiply defined.
kpathsea: Running mktextfm bkaiuf9
Missing character: There is no 中 in font cmr10!
./chapters/ch1.tex:12: Undefined control sequence.
LaTeX Warning: There were undefined references.
LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right.
Package hyperref Warning: Token not allowed in a PDF string.
LaTeX Font Warning: Font shape `TU/lmr/m/n' undefined.
LaTeX Font Warning: Size substitutions with differences up to 1.0pt have occurred.
No file thesis.toc.
)
"""


class TestClassification:
    """Each message kind is recognised."""

    def test_banner_is_captured(self) -> None:
        assert parse_log_text(SAMPLE_LOG).engine_banner.startswith("This is XeTeX")

    def test_overfull_boxes_carry_size_and_line(self) -> None:
        report = parse_log_text(SAMPLE_LOG)
        boxes = report.of_kind(MessageKind.OVERFULL_HBOX)
        assert len(boxes) == 2
        assert {item.amount for item in boxes} == {30.14, 2.5}
        assert boxes[0].line == 365

    def test_worst_overfull_is_the_largest(self) -> None:
        worst = parse_log_text(SAMPLE_LOG).worst_overfull
        assert worst is not None
        assert worst.amount == 30.14

    def test_underfull_box_is_information_only(self) -> None:
        boxes = parse_log_text(SAMPLE_LOG).of_kind(MessageKind.UNDERFULL_HBOX)
        assert len(boxes) == 1
        assert boxes[0].severity is Severity.INFO

    def test_undefined_reference_and_citation(self) -> None:
        report = parse_log_text(SAMPLE_LOG)
        assert len(report.of_kind(MessageKind.UNDEFINED_REFERENCE)) == 1
        assert len(report.of_kind(MessageKind.UNDEFINED_CITATION)) == 1
        assert report.of_kind(MessageKind.UNDEFINED_REFERENCE)[0].line == 100

    def test_multiply_defined_label(self) -> None:
        assert len(parse_log_text(SAMPLE_LOG).of_kind(MessageKind.MULTIPLY_DEFINED)) == 1

    def test_file_line_error_is_an_error_with_a_location(self) -> None:
        errors = parse_log_text(SAMPLE_LOG).of_kind(MessageKind.ERROR)
        assert len(errors) == 1
        assert errors[0].severity is Severity.ERROR
        assert errors[0].file == "./chapters/ch1.tex"
        assert errors[0].line == 12
        assert errors[0].location() == "./chapters/ch1.tex:12"

    def test_missing_font_is_detected_from_mktextfm(self) -> None:
        report = parse_log_text(SAMPLE_LOG)
        fonts = report.of_kind(MessageKind.MISSING_FONT)
        assert any("bkaiuf9" in item.message for item in fonts)
        assert report.missing_fonts == ("bkaiuf9",)

    def test_missing_character_is_reported(self) -> None:
        assert len(parse_log_text(SAMPLE_LOG).of_kind(MessageKind.MISSING_CHARACTER)) == 1

    def test_rerun_request_is_flagged(self) -> None:
        assert parse_log_text(SAMPLE_LOG).rerun_needed is True

    def test_missing_file_is_reported(self) -> None:
        report = parse_log_text(SAMPLE_LOG)
        assert any(
            "thesis.toc" in item.message for item in report.of_kind(MessageKind.MISSING_FILE)
        )

    def test_package_warning_is_classified(self) -> None:
        report = parse_log_text(SAMPLE_LOG)
        warnings = report.of_kind(MessageKind.PACKAGE_WARNING)
        assert len(warnings) == 1
        assert warnings[0].message.startswith("hyperref:")

    def test_undefined_font_shape_counts_as_a_missing_font(self) -> None:
        # A substituted font shape is a silently degraded build, so it is
        # reported alongside mktextfm rather than as a generic warning.
        report = parse_log_text(SAMPLE_LOG)
        fonts = report.of_kind(MessageKind.MISSING_FONT)
        assert any("Font shape" in item.message for item in fonts)

    def test_other_font_warnings_stay_font_warnings(self) -> None:
        report = parse_log_text(SAMPLE_LOG)
        warnings = report.of_kind(MessageKind.FONT_WARNING)
        assert len(warnings) == 1
        assert "Size substitutions" in warnings[0].message

    def test_counts_and_severity_split(self) -> None:
        report = parse_log_text(SAMPLE_LOG)
        counts = report.counts()
        assert counts["overfull-hbox"] == 2
        assert report.has_errors is True
        assert len(report.errors) == 1
        assert len(report.warnings) >= 6


class TestLineUnwrapping:
    """TeX breaks log lines at max_print_line; they must be rejoined."""

    LONG_MESSAGE = (
        r"Overfull \hbox (12.34pt too wide) in paragraph at lines 100--101 "
        r"of file chapters/ch1.tex"
    )

    def test_a_line_broken_at_the_print_width_is_rejoined(self) -> None:
        assert len(self.LONG_MESSAGE) > 79
        wrapped = self.LONG_MESSAGE[:79] + "\n" + self.LONG_MESSAGE[79:]
        boxes = parse_log_text(wrapped).of_kind(MessageKind.OVERFULL_HBOX)
        assert len(boxes) == 1
        assert boxes[0].amount == 12.34
        assert boxes[0].message.endswith("chapters/ch1.tex")

    def test_without_unwrapping_the_tail_of_the_line_is_lost(self) -> None:
        wrapped = self.LONG_MESSAGE[:79] + "\n" + self.LONG_MESSAGE[79:]
        boxes = parse_log_text(wrapped, print_width=0).of_kind(MessageKind.OVERFULL_HBOX)
        assert len(boxes) == 1
        assert not boxes[0].message.endswith("chapters/ch1.tex")

    def test_an_error_whose_path_was_split_is_still_classified(self) -> None:
        # A long source path pushes the "file:line: message" shape past the
        # print width, splitting it in two. Without rejoining, neither half
        # matches and the error disappears from the report entirely.
        error = (
            "./chapters/a-very-long-directory-name/deeper/ch1.tex:42: Undefined control sequence."
        )
        assert len(error) > 79
        wrapped = error[:79] + "\n" + error[79:]
        errors = parse_log_text(wrapped).of_kind(MessageKind.ERROR)
        assert len(errors) == 1
        assert errors[0].line == 42
        assert errors[0].message == "Undefined control sequence."

    def test_unwrapping_does_not_merge_ordinary_lines(self) -> None:
        report = parse_log_text(SAMPLE_LOG, print_width=0)
        assert len(report.of_kind(MessageKind.OVERFULL_HBOX)) == 2


class TestFileHandling:
    """Reading logs from disk."""

    def test_missing_log_returns_empty_report(self, tmp_path: Path) -> None:
        report = parse_log(tmp_path / "absent.log")
        assert report.messages == ()
        assert report.has_errors is False

    def test_reads_utf8(self, tmp_path: Path) -> None:
        path = tmp_path / "thesis.log"
        path.write_text(SAMPLE_LOG, encoding="utf-8")
        assert parse_log(path).has_errors is True

    def test_reads_non_utf8_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "latin1.log"
        path.write_bytes(b"This is pdfTeX\n! Undefined control sequence caf\xe9\n")
        assert parse_log(path).has_errors is True


class TestMissfont:
    """missfont.log is the only trace of a degraded build."""

    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert parse_missfont(tmp_path / "missfont.log") == ()

    def test_duplicate_lines_are_collapsed(self, tmp_path: Path) -> None:
        path = tmp_path / "missfont.log"
        path.write_text("mktextfm bkaiuf9\n" * 24 + "mktextfm bsmiuf1\n", encoding="utf-8")
        assert parse_missfont(path) == ("mktextfm bkaiuf9", "mktextfm bsmiuf1")


class TestSummarise:
    """Merging several runs."""

    def test_totals_are_added(self) -> None:
        one = parse_log_text(SAMPLE_LOG)
        merged = summarise([one, one])
        assert merged["overfull-hbox"] == 4


class TestFileLineErrors:
    r"""Every ``-file-line-error`` line is an error, not only the ones
    whose wording happens to contain "Error".

    The engine is always invoked with ``-file-line-error``, so these are
    the shape real failures arrive in.  "Missing $ inserted" and "Too many
    }'s" say nothing about an error in their wording; a classifier that
    looked for the word would leave a failed build with no finding
    pointing at the offending line.
    """

    ERRORS = (
        "./thesis.tex:42: Missing $ inserted.",
        "./thesis.tex:7: Missing number, treated as zero.",
        "./thesis.tex:9: Too many }'s.",
        r"./ch1.tex:88: Paragraph ended before \thesisTitle was complete.",
        r"./ch1.tex:12: Extra alignment tab has been changed to \cr.",
        "./thesis.tex:5: LaTeX Error: File `nope.sty' not found.",
        "./thesis.tex:3: Undefined control sequence.",
        "C:/thesis.tex:11: Missing } inserted.",  # the drive-letter branch
    )

    NOT_ERRORS = (
        "./thesis.tex:12: LaTeX Warning: Reference `x' undefined.",
        "./thesis.tex:12: Package hyperref Warning: Token not allowed.",
        r"./thesis.tex:9: LaTeX Font Warning: Font shape `TU/lmr/m/n' undefined.",
        "./thesis.tex:9: Class thesis_report Info: nothing to see.",
        "Package: fancyhdr 2021/01/04 v4.0.1",
        "LaTeX Font Info:    Trying to load font information on input line 12.",
        "Document Class: thesis_report 2024/01/01",
        "Chapter 3: 15: prose that merely looks like a location",
    )

    @pytest.mark.parametrize("line", ERRORS)
    def test_error_is_reported(self, line: str) -> None:
        report = parse_log_text("This is XeTeX\n" + line + "\n")
        assert report.has_errors, f"not classified as an error: {line}"

    @pytest.mark.parametrize("line", NOT_ERRORS)
    def test_non_error_is_not_reported(self, line: str) -> None:
        report = parse_log_text("This is XeTeX\n" + line + "\n")
        assert not report.has_errors, f"wrongly classified as an error: {line}"

    def test_file_and_line_are_kept(self) -> None:
        report = parse_log_text("This is XeTeX\n./chapters/ch3.tex:214: Missing $ inserted.\n")
        error = report.errors[0]
        assert error.file == "./chapters/ch3.tex"
        assert error.line == 214
        assert error.location() == "./chapters/ch3.tex:214"
