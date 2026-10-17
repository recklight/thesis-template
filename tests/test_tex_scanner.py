"""Tests for the LaTeX source scanner."""

from __future__ import annotations

from pathlib import Path

import pytest

from thesis_cli.parsers.tex_scanner import (
    LineIndex,
    clean_source,
    environment_counts,
    expand_document,
    read_group,
    scan_document,
    strip_comments,
)


class TestReadGroup:
    """Balanced-brace reading."""

    def test_reads_nested_group(self) -> None:
        assert read_group("{a{b}c}rest", 0) == ("a{b}c", 7)

    def test_ignores_escaped_brace(self) -> None:
        text = r"{a\}b}tail"
        assert read_group(text, 0) == (r"a\}b", 6)

    def test_returns_none_when_not_a_group(self) -> None:
        assert read_group("no braces", 0) is None

    def test_returns_none_when_unterminated(self) -> None:
        assert read_group("{unterminated", 0) is None


class TestCleanSource:
    """Comment, verbatim and math removal."""

    def test_comment_is_blanked_but_line_count_survives(self) -> None:
        cleaned = clean_source("a % comment\nb")
        assert cleaned.code.splitlines()[0].rstrip() == "a"
        assert cleaned.code.splitlines()[1] == "b"
        assert cleaned.code.count("\n") == 1
        assert len(cleaned.code) == len("a % comment\nb")

    def test_escaped_percent_is_not_a_comment(self) -> None:
        assert clean_source(r"100\% sure").code == r"100\% sure"

    def test_double_backslash_before_percent_starts_a_comment(self) -> None:
        # \\ is a line break, so the % that follows is a real comment.
        cleaned = clean_source("line\\\\% dropped\nnext")
        assert "dropped" not in cleaned.code
        assert "next" in cleaned.code

    def test_math_is_kept_in_code_and_removed_from_prose(self) -> None:
        cleaned = clean_source(r"before $x+1$ after")
        assert "x+1" in cleaned.code
        assert cleaned.prose.split() == ["before", "after"]
        assert cleaned.inline_math == 1

    def test_display_math_environment_is_counted(self) -> None:
        cleaned = clean_source(r"\begin{equation}E=mc^2\end{equation}")
        assert cleaned.display_math == 1
        assert cleaned.prose.strip() == ""

    def test_starred_math_environment_is_display_math(self) -> None:
        cleaned = clean_source(r"\begin{align*}a&=b\end{align*}")
        assert cleaned.display_math == 1
        assert cleaned.prose.strip() == ""

    def test_percent_inside_verbatim_is_literal(self) -> None:
        source = "\\begin{verbatim}\n50% kept\n\\end{verbatim}\nafter"
        cleaned = clean_source(source)
        assert "after" in cleaned.code
        assert cleaned.verbatim_blocks == 1

    def test_listings_body_is_removed(self) -> None:
        source = "\\begin{lstlisting}\n\\cite{ignored}\n\\end{lstlisting}\n"
        assert "ignored" not in clean_source(source).code

    def test_verb_command_is_removed(self) -> None:
        cleaned = clean_source(r"use \verb|\cite{x}| here")
        assert "cite" not in cleaned.code
        assert "here" in cleaned.code

    def test_lengths_are_preserved_for_every_view(self) -> None:
        source = "a % c\n$m$ \\begin{verbatim}v\\end{verbatim} z"
        cleaned = clean_source(source)
        assert len(cleaned.code) == len(source)
        assert len(cleaned.prose) == len(source)

    def test_strip_comments_is_the_code_view(self) -> None:
        assert strip_comments("keep % drop").rstrip() == "keep"


class TestLineIndex:
    """Offset to line-number mapping."""

    def test_maps_offsets_to_one_based_lines(self) -> None:
        index = LineIndex("one\ntwo\nthree")
        assert index.line_of(0) == 1
        assert index.line_of(4) == 2
        assert index.line_of(8) == 3


class TestEnvironmentCounts:
    """Environment tallying."""

    def test_counts_each_environment(self) -> None:
        text = r"\begin{figure}\end{figure}\begin{figure}\end{figure}\begin{table}\end{table}"
        assert environment_counts(text) == {"figure": 2, "table": 1}


class TestExpandDocument:
    """Include-chain traversal."""

    def test_follows_input_and_include(self, tmp_path: Path) -> None:
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "child.tex").write_text("child body", encoding="utf-8")
        main = tmp_path / "main.tex"
        main.write_text(r"\input{sub/child}", encoding="utf-8")
        files, missing = expand_document(main, tmp_path)
        assert [item.path.name for item in files] == ["main.tex", "child.tex"]
        assert missing == []

    def test_reports_unresolved_input(self, tmp_path: Path) -> None:
        main = tmp_path / "main.tex"
        main.write_text(r"\input{nowhere/missing}", encoding="utf-8")
        _, missing = expand_document(main, tmp_path)
        assert len(missing) == 1
        assert missing[0].target == "nowhere/missing"
        assert missing[0].location.line == 1

    def test_cycle_is_visited_once(self, tmp_path: Path) -> None:
        (tmp_path / "a.tex").write_text(r"\input{b}", encoding="utf-8")
        (tmp_path / "b.tex").write_text(r"\input{a}", encoding="utf-8")
        files, _ = expand_document(tmp_path / "a.tex", tmp_path)
        assert [item.path.name for item in files] == ["a.tex", "b.tex"]

    def test_missing_main_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            expand_document(tmp_path / "absent.tex", tmp_path)


class TestScanDocument:
    """End-to-end extraction on the synthetic project."""

    def test_finds_citations_across_files(self, project: Path) -> None:
        document = scan_document(project / "thesis.tex", project)
        assert document.cited_keys() == {"alpha", "beta"}

    def test_ignores_citations_in_comments_and_listings(self, project: Path) -> None:
        document = scan_document(project / "thesis.tex", project)
        assert "ghost" not in document.cited_keys()
        assert "ignored" not in document.cited_keys()

    def test_finds_labels_including_macro_defined_ones(self, project: Path) -> None:
        document = scan_document(project / "thesis.tex", project)
        assert {"ch:intro", "fig:plot", "eq:one", "sec:method", "tab:one"} <= document.label_names()
        assert "fig:diagram" in document.label_names(), "label from \\fig macro must be seen"

    def test_every_reference_resolves(self, project: Path) -> None:
        document = scan_document(project / "thesis.tex", project)
        undefined = {ref.name for ref in document.references} - document.label_names()
        assert undefined == set()

    def test_graphics_from_includegraphics_and_macros(self, project: Path) -> None:
        document = scan_document(project / "thesis.tex", project)
        requested = {item.raw for item in document.graphics}
        assert requested == {"figures/plot.png", "figures/diagram"}

    def test_caption_with_nested_braces_does_not_shift_arguments(self, tmp_path: Path) -> None:
        main = tmp_path / "m.tex"
        main.write_text(
            r"\fig{0.8}{figures/a}{Caption {with} \textbf{nested} braces}{fig:a}",
            encoding="utf-8",
        )
        document = scan_document(main, tmp_path)
        assert [item.raw for item in document.graphics] == ["figures/a"]
        assert document.label_names() == {"fig:a"}

    def test_locations_point_at_the_right_file_and_line(self, project: Path) -> None:
        document = scan_document(project / "thesis.tex", project)
        plot = next(item for item in document.graphics if item.raw == "figures/plot.png")
        assert plot.location.file.name == "ch1.tex"
        expected = (project / "chapters" / "ch1.tex").read_text(encoding="utf-8").splitlines()
        assert "figures/plot.png" in expected[plot.location.line - 1]

    def test_bibliography_command_is_recorded(self, project: Path) -> None:
        document = scan_document(project / "thesis.tex", project)
        assert document.bib_commands == ("bib/refs",)

    def test_duplicate_labels_are_grouped(self, tmp_path: Path) -> None:
        main = tmp_path / "m.tex"
        main.write_text("\\label{dup}\n\\label{dup}\n\\label{unique}\n", encoding="utf-8")
        document = scan_document(main, tmp_path)
        duplicates = document.duplicate_labels()
        assert set(duplicates) == {"dup"}
        assert [item.location.line for item in duplicates["dup"]] == [1, 2]

    def test_macro_parameter_labels_are_not_real_labels(self, tmp_path: Path) -> None:
        main = tmp_path / "m.tex"
        main.write_text(
            r"\newcommand{\myfig}[4]{\includegraphics{#2}\label{#4}}" + "\n",
            encoding="utf-8",
        )
        document = scan_document(main, tmp_path)
        assert document.label_names() == set()
        assert document.graphics == ()

    def test_nocite_star_sets_the_flag(self, tmp_path: Path) -> None:
        main = tmp_path / "m.tex"
        main.write_text(r"\nocite{*}", encoding="utf-8")
        assert scan_document(main, tmp_path).cite_all is True
