"""Tests for the BibTeX reader and its data-quality audit.

The bad-data fixtures are modelled on the failure modes that survive a
"successful" BibTeX run: a name list ending in a comma, a whole citation
pasted into the ``year`` field, an entry whose fields are all empty, and one
key defined twice with different content in two files.
"""

from __future__ import annotations

from pathlib import Path

from thesis_cli.parsers.bib_parser import (
    BibProblemKind,
    detect_duplicate_keys,
    parse_bib,
    parse_bib_files,
    parse_bib_text,
)

GOOD = """@ARTICLE{good,
  author  = {A. Author and B. Second},
  title   = {A Perfectly Fine Title},
  journal = {Journal of Things},
  volume  = {3},
  year    = {1999}
}
"""

BAD = """@article{trailing,
  author  = {B. Writer, },
  title   = {Has a trailing comma in the name list},
  journal = {J},
  year    = {2004}
}

@article{pasted,
  author = {A. Author},
  year   = {: Some Book. Some Press, City, 1988}
}

@misc{ek,
  author = {},
  title  = {},
  year   = {}
}
"""


def _kinds(problems: object) -> set[str]:
    return {item.kind.value for item in problems}  # type: ignore[attr-defined]


class TestParsing:
    """Entry and field extraction."""

    def test_reads_key_type_and_fields(self) -> None:
        database = parse_bib_text(GOOD)
        entry = database.entries[0]
        assert entry.key == "good"
        assert entry.entry_type == "article"
        assert entry.get("YEAR") == "1999"
        assert entry.get("volume") == "3"

    def test_quoted_values_keep_inner_braces(self) -> None:
        # Inner braces are BibTeX's capitalisation protection: they are part
        # of the value, and only the outer delimiters are stripped.
        database = parse_bib_text('@misc{q, title = "A quoted {Title} with braces", year = "2001"}')
        assert database.entries[0].get("title") == "A quoted {Title} with braces"
        assert database.entries[0].get("year") == "2001"

    def test_bare_numeric_value(self) -> None:
        database = parse_bib_text("@misc{n, title = {T}, year = 2010}")
        assert database.entries[0].get("year") == "2010"

    def test_string_macro_and_concatenation(self) -> None:
        source = (
            '@string{acme = "Acme Press"}\n'
            "@book{b, author={A}, title={T}, publisher = acme, year={2000}}\n"
        )
        assert parse_bib_text(source).entries[0].get("publisher") == "Acme Press"

    def test_comment_and_preamble_blocks_are_skipped(self) -> None:
        source = "@comment{ignored stuff}\n@preamble{ {\\newcommand{\\x}{}} }\n" + GOOD
        assert [item.key for item in parse_bib_text(source).entries] == ["good"]

    def test_parenthesised_entry_delimiters(self) -> None:
        database = parse_bib_text("@misc(paren, title = {T}, year = {2001})")
        assert database.entries[0].key == "paren"

    def test_whitespace_in_values_is_collapsed(self) -> None:
        database = parse_bib_text("@misc{w, title = {A\n   multi-line\n   title}}")
        assert database.entries[0].get("title") == "A multi-line title"

    def test_good_entry_has_no_problems(self) -> None:
        assert parse_bib_text(GOOD).problems == ()


class TestQualityAudit:
    """Defects that a plain BibTeX run leaves buried in the .blg file."""

    def test_trailing_comma_in_author_is_an_error(self) -> None:
        problems = parse_bib_text(BAD).problems
        trailing = [
            item
            for item in problems
            if item.kind is BibProblemKind.TRAILING_COMMA_IN_NAMES and item.key == "trailing"
        ]
        assert len(trailing) == 1
        assert trailing[0].severity == "error"
        assert "comma at the end" in trailing[0].message

    def test_dangling_and_in_author_is_an_error(self) -> None:
        database = parse_bib_text("@misc{d, author = {A. One and }, title={T}}")
        assert BibProblemKind.TRAILING_COMMA_IN_NAMES.value in _kinds(database.problems)

    def test_year_holding_a_whole_citation_is_an_error(self) -> None:
        problems = [
            item
            for item in parse_bib_text(BAD).problems
            if item.key == "pasted" and item.kind is BibProblemKind.MALFORMED_YEAR
        ]
        assert len(problems) == 1
        assert problems[0].severity == "error"

    def test_year_without_a_four_digit_number_is_an_error(self) -> None:
        database = parse_bib_text("@misc{y, title={T}, year={forthcoming}}")
        malformed = [
            item for item in database.problems if item.kind is BibProblemKind.MALFORMED_YEAR
        ]
        assert len(malformed) == 1

    def test_missing_required_fields_are_reported(self) -> None:
        problems = [
            item
            for item in parse_bib_text(BAD).problems
            if item.key == "pasted" and item.kind is BibProblemKind.MISSING_FIELD
        ]
        missing = {item.message for item in problems}
        assert any("title" in text for text in missing)
        assert any("journal" in text for text in missing)

    def test_empty_fields_are_warnings(self) -> None:
        problems = [
            item
            for item in parse_bib_text(BAD).problems
            if item.key == "ek" and item.kind is BibProblemKind.EMPTY_FIELD
        ]
        assert len(problems) == 3
        assert {item.severity for item in problems} == {"warning"}

    def test_unknown_entry_type_is_a_warning(self) -> None:
        database = parse_bib_text("@nonsense{x, title={T}}")
        kinds = _kinds(database.problems)
        assert BibProblemKind.UNKNOWN_ENTRY_TYPE.value in kinds

    def test_entry_without_a_key_is_reported(self) -> None:
        database = parse_bib_text("@misc{, title={T}}")
        assert BibProblemKind.SYNTAX.value in _kinds(database.problems)


class TestDuplicateKeys:
    """BibTeX keeps the first definition and mentions the rest only in the .blg."""

    def test_identical_duplicates_are_a_warning(self) -> None:
        source = "@misc{k, title={Same}}\n@misc{k, title={Same}}\n"
        problems = detect_duplicate_keys(parse_bib_text(source).entries)
        assert [item.kind for item in problems] == [BibProblemKind.DUPLICATE_KEY]
        assert problems[0].severity == "warning"

    def test_conflicting_duplicates_are_an_error(self) -> None:
        source = "@misc{k, title={One}}\n@misc{k, title={Two}}\n"
        problems = detect_duplicate_keys(parse_bib_text(source).entries)
        assert [item.kind for item in problems] == [BibProblemKind.CONFLICTING_DUPLICATE]
        assert problems[0].severity == "error"

    def test_single_file_parse_detects_duplicates(self, tmp_path: Path) -> None:
        path = tmp_path / "d.bib"
        path.write_text("@misc{k, title={One}}\n@misc{k, title={Two}}\n", encoding="utf-8")
        assert BibProblemKind.CONFLICTING_DUPLICATE.value in _kinds(parse_bib(path).problems)

    def test_cross_file_conflict_is_reported_once(self, tmp_path: Path) -> None:
        first = tmp_path / "a.bib"
        second = tmp_path / "b.bib"
        first.write_text("@misc{shared, title={From A}}\n", encoding="utf-8")
        second.write_text("@misc{shared, title={From B}}\n", encoding="utf-8")
        database = parse_bib_files([first, second])
        conflicts = [
            item for item in database.problems if item.kind is BibProblemKind.CONFLICTING_DUPLICATE
        ]
        assert len(conflicts) == 1
        assert "a.bib" in conflicts[0].message and "b.bib" in conflicts[0].message


class TestFileHandling:
    """Reading from disk."""

    def test_missing_file_is_a_syntax_problem_not_an_exception(self, tmp_path: Path) -> None:
        database = parse_bib(tmp_path / "absent.bib")
        assert database.entries == ()
        assert BibProblemKind.SYNTAX.value in _kinds(database.problems)

    def test_keys_and_errors_helpers(self, tmp_path: Path) -> None:
        path = tmp_path / "mix.bib"
        path.write_text(GOOD + BAD, encoding="utf-8")
        database = parse_bib(path)
        assert database.keys == {"good", "trailing", "pasted", "ek"}
        assert all(item.severity == "error" for item in database.errors())
        assert len(database.by_key()["good"]) == 1

    def test_line_numbers_point_at_the_entry(self, tmp_path: Path) -> None:
        path = tmp_path / "lines.bib"
        path.write_text("\n\n" + GOOD, encoding="utf-8")
        assert parse_bib(path).entries[0].line == 3


class TestStringMacroScope:
    r"""``@string`` macros accumulate across databases.

    BibTeX reads ``\bibliography{a,b}`` in order and a macro defined in
    ``a.bib`` resolves in ``b.bib``.  Parsing each file on its own would
    store ``publisher = acme`` in the second file as the literal word.
    """

    def test_a_macro_crosses_into_the_next_file(self, tmp_path: Path) -> None:
        (tmp_path / "a.bib").write_text(
            '@string{acme = "Acme Press"}\n@misc{one, title={T1}}\n', encoding="utf-8"
        )
        (tmp_path / "b.bib").write_text(
            "@book{two, author={A}, title={T2}, publisher = acme, year={2001}}\n",
            encoding="utf-8",
        )
        database = parse_bib_files([tmp_path / "a.bib", tmp_path / "b.bib"])
        entry = next(item for item in database.entries if item.key == "two")
        assert entry.get("publisher") == "Acme Press"
        assert database.errors() == ()

    def test_the_reverse_order_leaves_the_macro_unresolved(self, tmp_path: Path) -> None:
        # Order matters to BibTeX, so it must matter here: a macro used
        # before it is defined stays the literal word, as BibTeX leaves it.
        (tmp_path / "a.bib").write_text(
            '@string{acme = "Acme Press"}\n@misc{one, title={T1}}\n', encoding="utf-8"
        )
        (tmp_path / "b.bib").write_text(
            "@book{two, author={A}, title={T2}, publisher = acme, year={2001}}\n",
            encoding="utf-8",
        )
        database = parse_bib_files([tmp_path / "b.bib", tmp_path / "a.bib"])
        entry = next(item for item in database.entries if item.key == "two")
        assert entry.get("publisher") == "acme"

    def test_an_inherited_macro_can_be_passed_in_directly(self) -> None:
        database = parse_bib_text("@misc{m, publisher = acme}", strings={"acme": "Acme Ltd"})
        assert database.entries[0].get("publisher") == "Acme Ltd"
