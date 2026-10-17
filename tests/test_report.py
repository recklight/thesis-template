"""Tests for the reporting layer and its exit-code contract."""

from __future__ import annotations

import io
import json

from thesis_cli.errors import CheckFailedError
from thesis_cli.report import CommandReport, Finding, ReportTable, Severity, render_report


def _finding(severity: Severity, kind: str = "k") -> Finding:
    return Finding(severity=severity, kind=kind, message="message", location="file.tex:1")


class TestSeverity:
    """Ordering and counting."""

    def test_error_sorts_before_warning_before_info(self) -> None:
        assert Severity.ERROR.rank < Severity.WARNING.rank < Severity.INFO.rank

    def test_counts(self) -> None:
        report = CommandReport(
            command="x",
            findings=(
                _finding(Severity.WARNING),
                _finding(Severity.ERROR),
                _finding(Severity.INFO),
            ),
        )
        assert report.error_count == 1
        assert report.warning_count == 1
        assert report.is_clean is False

    def test_sorted_findings_put_errors_first(self) -> None:
        report = CommandReport(
            command="x",
            findings=(_finding(Severity.INFO), _finding(Severity.ERROR)),
        )
        assert [item.severity for item in report.sorted_findings()] == [
            Severity.ERROR,
            Severity.INFO,
        ]


class TestExitCodes:
    """The contract a CI job relies on."""

    def test_clean_report_is_zero(self) -> None:
        report = CommandReport(command="x")
        assert report.exit_code() == 0
        assert report.exit_code(strict=True) == 0
        assert report.is_clean is True

    def test_error_always_fails(self) -> None:
        report = CommandReport(command="x", findings=(_finding(Severity.ERROR),))
        assert report.exit_code() == CheckFailedError.exit_code
        assert report.exit_code(strict=True) == CheckFailedError.exit_code

    def test_warning_only_fails_under_strict(self) -> None:
        report = CommandReport(command="x", findings=(_finding(Severity.WARNING),))
        assert report.exit_code() == 0
        assert report.exit_code(strict=True) == CheckFailedError.exit_code

    def test_info_never_fails(self) -> None:
        report = CommandReport(command="x", findings=(_finding(Severity.INFO),))
        assert report.exit_code(strict=True) == 0


class TestSerialisation:
    """JSON output."""

    def test_round_trips(self) -> None:
        report = CommandReport(
            command="check-refs",
            findings=(_finding(Severity.ERROR, "undefined-citation"),),
            tables=(ReportTable(title="T", columns=("a", "b"), rows=(("1", "2"),)),),
            summary={"count": 3},
        )
        payload = json.loads(report.to_json())
        assert payload["command"] == "check-refs"
        assert payload["counts"]["error"] == 1
        assert payload["findings"][0]["kind"] == "undefined-citation"
        assert payload["tables"][0]["rows"] == [["1", "2"]]
        assert payload["summary"]["count"] == 3

    def test_chinese_text_is_not_escaped(self) -> None:
        report = CommandReport(
            command="stats",
            findings=(Finding(severity=Severity.WARNING, kind="k", message="中文訊息"),),
        )
        assert "中文訊息" in report.to_json()

    def test_non_serialisable_values_fall_back_to_str(self) -> None:
        from pathlib import Path

        report = CommandReport(command="x", summary={"path": Path("a/b")})
        assert "a" in json.loads(report.to_json())["summary"]["path"]


class TestRendering:
    """Both output modes produce something readable."""

    def test_plain_text_contains_the_findings(self) -> None:
        report = CommandReport(
            command="figures",
            findings=(_finding(Severity.ERROR, "figure-missing"),),
            tables=(ReportTable(title="T", columns=("a",), rows=(("1",),)),),
            summary={"k": "v"},
        )
        stream = io.StringIO()
        render_report(report, stream=stream)
        text = stream.getvalue()
        assert "figures" in text
        assert "figure-missing" in text
        assert "1 error(s)" in text

    def test_json_mode_writes_only_json(self) -> None:
        report = CommandReport(command="stats", summary={"a": 1})
        stream = io.StringIO()
        render_report(report, as_json=True, stream=stream)
        assert json.loads(stream.getvalue())["command"] == "stats"

    def test_verbose_adds_the_detail_lines(self) -> None:
        report = CommandReport(
            command="x",
            findings=(
                Finding(
                    severity=Severity.ERROR,
                    kind="k",
                    message="m",
                    location="f:1",
                    detail="extra context here",
                ),
            ),
        )
        stream = io.StringIO()
        render_report(report, verbose=True, stream=stream)
        assert "extra context here" in stream.getvalue()

    def test_empty_report_renders(self) -> None:
        stream = io.StringIO()
        render_report(CommandReport(command="clean"), stream=stream)
        assert "0 error(s)" in stream.getvalue()
