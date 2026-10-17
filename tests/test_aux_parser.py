"""Tests for the ``.aux`` reader.

Both ``\\newlabel`` shapes are covered: the two-field form plain LaTeX
writes and the five-field form ``hyperref`` writes.  A parser that assumes
one of them silently loses half the data.
"""

from __future__ import annotations

from pathlib import Path

from thesis_cli.parsers.aux_parser import parse_aux, parse_aux_text

PLAIN_AUX = r"""\relax
\citation{alpha}
\citation{beta,gamma}
\bibstyle{ieeetr}
\bibdata{refs}
\newlabel{ch:intro}{{1}{1}}
\newlabel{fig:plot}{{1.1}{3}}
\bibcite{alpha}{1}
\bibcite{beta}{2}
\@writefile{toc}{\contentsline {chapter}{\numberline {1}Intro}{1}}
\@writefile{lof}{\contentsline {figure}{\numberline {1.1}A plot}{3}}
"""

HYPERREF_AUX = (
    "\\relax\n"
    "\\citation{fp1,fp2,fp3,fp4}\n"
    "\\newlabel{eq:nn}{{2.1}{6}{Chapter {title} with braces\\relax }{equation.2.1.1}{}}\n"
    "\\newlabel{sec:a}{{2}{5}{A section\\relax }{section.2}{}}\n"
    "\\bibcite{fp1}{1}\n"
    "\\bibstyle{ieeetr}\n"
    "\\bibdata{ensemble}\n"
)


class TestPlainFormat:
    """Two-field ``\\newlabel``."""

    def test_labels(self) -> None:
        aux = parse_aux_text(PLAIN_AUX)
        assert aux.label_names == {"ch:intro", "fig:plot"}
        label = next(item for item in aux.labels if item.name == "fig:plot")
        assert (label.number, label.page) == ("1.1", "3")
        assert label.anchor is None
        assert label.has_hyperref_fields is False

    def test_citations_keep_order_and_split_on_commas(self) -> None:
        aux = parse_aux_text(PLAIN_AUX)
        assert aux.citations == ("alpha", "beta", "gamma")

    def test_bibcite_bibdata_bibstyle(self) -> None:
        aux = parse_aux_text(PLAIN_AUX)
        assert dict(aux.bibcite) == {"alpha": "1", "beta": "2"}
        assert aux.bibdata == ("refs",)
        assert aux.bibstyle == "ieeetr"

    def test_writefile_destinations(self) -> None:
        aux = parse_aux_text(PLAIN_AUX)
        assert aux.toc_entries == 1
        assert len(aux.writefiles) == 2


class TestHyperrefFormat:
    """Five-field ``\\newlabel``."""

    def test_anchor_is_extracted(self) -> None:
        aux = parse_aux_text(HYPERREF_AUX)
        label = next(item for item in aux.labels if item.name == "eq:nn")
        assert label.number == "2.1"
        assert label.page == "6"
        assert label.anchor == "equation.2.1.1"
        assert label.has_hyperref_fields is True

    def test_nested_braces_in_the_title_field_do_not_break_the_scan(self) -> None:
        aux = parse_aux_text(HYPERREF_AUX)
        label = next(item for item in aux.labels if item.name == "eq:nn")
        assert "braces" in label.fields[2]
        assert len(label.fields) == 5

    def test_multiple_keys_in_one_citation(self) -> None:
        aux = parse_aux_text(HYPERREF_AUX)
        assert aux.citation_keys == {"fp1", "fp2", "fp3", "fp4"}


class TestEdgeCases:
    """A half-written ``.aux`` must not crash an audit."""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        aux = parse_aux(tmp_path / "absent.aux")
        assert aux.labels == ()
        assert aux.citations == ()

    def test_truncated_newlabel_is_skipped(self) -> None:
        aux = parse_aux_text("\\newlabel{broken\n\\citation{ok}\n")
        assert aux.citation_keys == {"ok"}

    def test_similar_command_names_are_not_confused(self) -> None:
        aux = parse_aux_text(r"\bibcitex{no}{1}" + "\n" + r"\bibcite{yes}{1}")
        assert dict(aux.bibcite) == {"yes": "1"}

    def test_reads_from_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "thesis.aux"
        path.write_text(PLAIN_AUX, encoding="utf-8")
        aux = parse_aux(path)
        assert aux.path == path
        assert aux.bibstyle == "ieeetr"

    def test_follows_at_input_children(self, tmp_path: Path) -> None:
        (tmp_path / "child.aux").write_text(r"\newlabel{from:child}{{9}{9}}", encoding="utf-8")
        parent = tmp_path / "main.aux"
        parent.write_text("\\@input{child.aux}\n\\newlabel{from:parent}{{1}{1}}", encoding="utf-8")
        aux = parse_aux(parent)
        assert aux.label_names == {"from:parent", "from:child"}

    def test_latin1_bytes_do_not_raise(self, tmp_path: Path) -> None:
        path = tmp_path / "latin1.aux"
        path.write_bytes(b"\\newlabel{a}{{1}{1}}\n% caf\xe9\n")
        assert parse_aux(path).label_names == {"a"}
