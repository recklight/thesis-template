"""Tests for the word-count command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from thesis_cli.commands.stats import _strip_command_arguments, collect_statistics
from thesis_cli.config import ThesisConfig, load_config
from thesis_cli.errors import ProjectLayoutError


def _row(report: object, name: str) -> tuple[str, ...]:
    table = report.tables[0]  # type: ignore[attr-defined]
    for row in table.rows:
        if row[0].endswith(name):
            return row
    raise AssertionError(f"no row for {name}: {[r[0] for r in table.rows]}")


class TestCounting:
    """What the table reports."""

    def test_chapters_are_listed(self, config: ThesisConfig) -> None:
        report = collect_statistics(config)
        names = {row[0] for row in report.tables[0].rows}
        assert "chapters/ch1.tex" in names
        assert "chapters/ch2.tex" in names

    def test_cjk_and_latin_are_counted_separately(self, config: ThesisConfig) -> None:
        row = _row(collect_statistics(config), "ch1.tex")
        assert int(row[1]) > 0, "Chinese characters"
        assert int(row[2]) > 0, "English words"

    def test_structure_and_float_counts(self, config: ThesisConfig) -> None:
        row = _row(collect_statistics(config), "ch1.tex")
        assert row[3] == "1", "one chapter"
        assert row[5] == "1", "one figure environment"
        assert row[7] == "1", "one display equation"
        assert row[8] == "2", "two citation keys"

    def test_totals_row_is_present(self, config: ThesisConfig) -> None:
        rows = collect_statistics(config).tables[0].rows
        assert any(row[0] == "TOTAL" for row in rows)

    def test_comments_are_excluded(self, project: Path) -> None:
        before = collect_statistics(load_config(project, required=True))
        path = project / "chapters" / "ch1.tex"
        path.write_text(
            path.read_text(encoding="utf-8") + "\n% 這一整行都是註解不應該被計入字數\n",
            encoding="utf-8",
        )
        after = collect_statistics(load_config(project, required=True))
        assert after.summary["cjk_chars"] == before.summary["cjk_chars"]

    def test_math_is_excluded(self, project: Path) -> None:
        before = collect_statistics(load_config(project, required=True))
        path = project / "chapters" / "ch1.tex"
        path.write_text(
            path.read_text(encoding="utf-8") + "\n$\\alpha + \\beta = \\gamma$\n",
            encoding="utf-8",
        )
        after = collect_statistics(load_config(project, required=True))
        assert after.summary["latin_words"] == before.summary["latin_words"]

    def test_listings_are_excluded(self, config: ThesisConfig) -> None:
        # ch1 contains a lstlisting block with English words in it.
        report = collect_statistics(config)
        assert "lstlisting" not in json.dumps(report.to_dict())

    def test_label_and_cite_arguments_are_not_words(self, project: Path) -> None:
        before = collect_statistics(load_config(project, required=True))
        path = project / "chapters" / "ch2.tex"
        path.write_text(
            path.read_text(encoding="utf-8") + "\n\\label{sec:a-long-identifier-here}\n",
            encoding="utf-8",
        )
        after = collect_statistics(load_config(project, required=True))
        assert after.summary["latin_words"] == before.summary["latin_words"]

    def test_a_single_unbroken_line_counts_correctly(self, project: Path) -> None:
        # Abstracts are written as one long line; a line-oriented counter
        # would report 1 here.
        (project / "content").mkdir()
        long_line = "摘要內容" * 100
        (project / "content" / "abstract.tex").write_text(long_line, encoding="utf-8")
        main = project / "thesis.tex"
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                r"\input{chapters/ch1.tex}",
                "\\input{content/abstract.tex}\n\\input{chapters/ch1.tex}",
            ),
            encoding="utf-8",
        )
        report = collect_statistics(load_config(project, required=True))
        assert _row(report, "abstract.tex")[1] == "400"


class TestMarkupIsNotProse:
    """Commands, environment names and macro settings are not words."""

    MARKUP_ONLY = (
        "\\chapter{緒論}\n\\section{研究背景}\n"
        "\\begin{itemize}\n\\item 第一點\n\\item 第二點\n\\end{itemize}\n"
        "\\textbf{重點}\\noindent\n"
        "\\begin{table}[htbp]\\centering\n\\begin{tabular}{lrr}\n項目 & 甲 & 乙 \\\\[2pt]\n"
        "\\end{tabular}\n\\end{table}\n"
    )

    @staticmethod
    def _replace_chapter(project: Path, text: str) -> ThesisConfig:
        (project / "chapters" / "ch2.tex").write_text(text, encoding="utf-8")
        return load_config(project, required=True)

    def test_a_chapter_of_markup_and_chinese_has_no_latin_words(self, project: Path) -> None:
        report = collect_statistics(self._replace_chapter(project, self.MARKUP_ONLY))
        row = _row(report, "ch2.tex")
        assert row[2] == "0"
        assert int(row[1]) > 0

    def test_figure_macro_settings_are_not_words(self, project: Path) -> None:
        text = (
            "\\chapter{方法}\n"
            "\\fig{0.6}{figures/diagram}{Two words}{fig:diagram}\n"
            "\\figtr{0.6}{figures/diagram}{圖說}{fig:trimmed}{10 20 10 20}\n"
            "\\begin{figure}\\centering\n"
            "\\mpfiga{0.45}{t}{figures/diagram}{圖說}{fig:panel}\n"
            "\\end{figure}\n"
        )
        report = collect_statistics(self._replace_chapter(project, text))
        assert _row(report, "ch2.tex")[2] == "2"

    def test_the_extra_text_of_figt_is_prose(self, project: Path) -> None:
        text = "\\chapter{方法}\n\\figt{0.6}{figures/diagram}{圖說}{fig:a}{A note below.}\n"
        report = collect_statistics(self._replace_chapter(project, text))
        assert _row(report, "ch2.tex")[2] == "3"

    def test_figure_macros_count_as_figures(self, project: Path) -> None:
        text = (
            "\\chapter{方法}\n"
            "\\fig{0.6}{figures/diagram}{圖說}{fig:a}\n"
            "\\hfigtr{0.6}{figures/diagram}{圖說}{fig:b}{0 0 0 0}\n"
            "\\begin{figure}\\mpfig{0.4}{figures/diagram}{圖說}{fig:c}\\end{figure}\n"
        )
        report = collect_statistics(self._replace_chapter(project, text))
        assert _row(report, "ch2.tex")[5] == "3"

    def test_starred_headings_are_not_chapters(self, project: Path) -> None:
        (project / "content").mkdir()
        (project / "content" / "front.tex").write_text(
            "\\chapter*{摘要}\n摘要內容。\n\\chapter*{誌謝}\n誌謝內容。\n", encoding="utf-8"
        )
        main = project / "thesis.tex"
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                r"\input{chapters/ch1.tex}",
                "\\input{content/front.tex}\n\\input{chapters/ch1.tex}",
            ),
            encoding="utf-8",
        )
        report = collect_statistics(load_config(project, required=True))
        assert _row(report, "front.tex")[3] == "0"
        assert report.summary["chapters"] == 2


class TestFileSelection:
    """Which files appear in the table."""

    def test_preamble_files_are_collapsed_by_default(self, project: Path) -> None:
        (project / "config").mkdir()
        (project / "config" / "setup.tex").write_text(
            "\\usepackage{amsmath}\n\\newcommand{\\foo}{bar baz qux}\n", encoding="utf-8"
        )
        main = project / "thesis.tex"
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                r"\begin{document}", "\\input{config/setup.tex}\n\\begin{document}"
            ),
            encoding="utf-8",
        )
        report = collect_statistics(load_config(project, required=True))
        names = [row[0] for row in report.tables[0].rows]
        assert "config/setup.tex" not in names
        assert any(row.startswith("excluded:") for row in names)

    def test_all_files_lists_everything(self, project: Path) -> None:
        (project / "config").mkdir()
        (project / "config" / "setup.tex").write_text("\\usepackage{amsmath}\n", encoding="utf-8")
        main = project / "thesis.tex"
        main.write_text(
            main.read_text(encoding="utf-8").replace(
                r"\begin{document}", "\\input{config/setup.tex}\n\\begin{document}"
            ),
            encoding="utf-8",
        )
        report = collect_statistics(load_config(project, required=True), all_files=True)
        names = [row[0] for row in report.tables[0].rows]
        assert "config/setup.tex" in names

    def test_only_chapters_restricts_the_table(self, config: ThesisConfig) -> None:
        report = collect_statistics(config, only_chapters=True)
        names = [row[0] for row in report.tables[0].rows if row[0] != "TOTAL"]
        assert names == ["chapters/ch1.tex", "chapters/ch2.tex"]


class TestFindings:
    """An empty chapter is almost always a mistake."""

    def test_empty_chapter_is_warned_about(self, project: Path) -> None:
        (project / "chapters" / "ch2.tex").write_text("", encoding="utf-8")
        report = collect_statistics(load_config(project, required=True))
        assert any(item.kind == "empty-chapter" for item in report.findings)
        assert report.exit_code(strict=True) != 0

    def test_full_chapters_produce_no_findings(self, config: ThesisConfig) -> None:
        assert collect_statistics(config).findings == ()


class TestOutput:
    """Machine-readable output."""

    def test_json_summary_keys(self, config: ThesisConfig) -> None:
        payload = json.loads(collect_statistics(config).to_json())
        summary = payload["summary"]
        for key in ("cjk_chars", "latin_words", "combined", "figures", "citations"):
            assert key in summary

    def test_missing_main_raises(self, project: Path) -> None:
        (project / "thesis.tex").unlink()
        with pytest.raises(ProjectLayoutError):
            collect_statistics(load_config(project, required=True))


class TestCommandNameBoundaries:
    r"""A stripped command name must end where the pattern says it does.

    The alternation lists ``include`` before ``includegraphics``, so
    without an end-of-name guard ``\includegraphics{figures/plot.png}``
    would lose only its first eight characters and leave the word
    ``graphics`` and the file path in the English word count.
    """

    def test_includegraphics_is_removed_whole(self) -> None:
        text = r"before \includegraphics[width=1cm]{figures/plot.png} after"
        assert _strip_command_arguments(text).split() == ["before", "after"]

    def test_include_is_still_removed(self) -> None:
        assert _strip_command_arguments(r"a \include{chapters/ch1} b").split() == ["a", "b"]

    def test_an_unlisted_command_is_left_alone(self) -> None:
        # \reflectbox is not in the list and its argument is prose, so it
        # must come through untouched.  Without the end-of-name guard the
        # \ref alternative would bite off its first four characters and
        # leave "lectbox" as an English word.
        source = r"see \reflectbox{mirrored words} here"
        assert _strip_command_arguments(source) == source

    def test_a_starred_form_is_still_removed(self) -> None:
        assert _strip_command_arguments(r"x \newcommand*{\foo}{bar} y").split() == ["x", "y"]
