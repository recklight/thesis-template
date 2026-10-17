"""Tests for the mixed Chinese/English word counter."""

from __future__ import annotations

import re

import numpy as np
import pytest

from thesis_cli.parsers.cjk import (
    _CJK_EDGES,
    _PUNCT_EDGES,
    _SPACE_EDGES,
    CJK_BLOCKS,
    CJK_PUNCTUATION_BLOCKS,
    WHITESPACE_BLOCKS,
    TextCounts,
    _edges,
    _in_blocks,
    count_text,
    is_cjk,
    split_latin_words,
)


class TestIsCjk:
    """Single-character classification."""

    @pytest.mark.parametrize("char", ["論", "文", "一", "鿕"])
    def test_ideographs(self, char: str) -> None:
        assert is_cjk(char) is True

    @pytest.mark.parametrize("char", ["A", "z", "5", " ", "，", "。", "（", "α"])
    def test_non_ideographs(self, char: str) -> None:
        assert is_cjk(char) is False

    def test_rejects_multi_character_input(self) -> None:
        with pytest.raises(ValueError, match="single character"):
            is_cjk("兩個")

    def test_astral_plane_extension_b(self) -> None:
        assert is_cjk(chr(0x20000)) is True

    @pytest.mark.parametrize(
        "point",
        [0x2EBF0, 0x2EE5F, 0x31350, 0x323AF, 0x323B0, 0x3347F],
        ids=["ext-i-first", "ext-i-last", "ext-h-first", "ext-h-last", "ext-j-first", "ext-j-last"],
    )
    def test_extensions_h_i_and_j(self, point: int) -> None:
        # Blocks.txt, Unicode 17.0.0.
        assert is_cjk(chr(point)) is True

    def test_the_last_block_ends_where_extension_j_ends(self) -> None:
        assert is_cjk(chr(0x33480)) is False


class TestSplitLatinWords:
    """Tokenisation of the non-CJK remainder."""

    def test_splits_on_whitespace(self) -> None:
        assert split_latin_words("sampling rate estimation") == [
            "sampling",
            "rate",
            "estimation",
        ]

    def test_drops_pure_punctuation_tokens(self) -> None:
        assert split_latin_words("a -- b") == ["a", "b"]

    def test_keeps_numbers_and_attached_punctuation(self) -> None:
        assert split_latin_words("in 2024, we") == ["in", "2024,", "we"]

    def test_empty_input(self) -> None:
        assert split_latin_words("") == []

    @pytest.mark.parametrize("sign", ["×", "÷"])
    def test_arithmetic_signs_are_not_words(self, sign: str) -> None:
        # U+00D7 and U+00F7 sit inside the accented-Latin range, so a token
        # class written as a literal range counts "3 × 4" as three words.
        assert split_latin_words(f"3 {sign} 4") == ["3", "4"]

    def test_accented_and_non_latin_letters_are_words(self) -> None:
        assert split_latin_words("café Ω я") == ["café", "Ω", "я"]

    def test_fullwidth_alphanumerics_are_words(self) -> None:
        assert split_latin_words("ＡＢＣ １２３") == [
            "ＡＢＣ",
            "１２３",
        ]


class TestCountText:
    """The figure a graduate office asks for."""

    def test_pure_chinese_counts_characters_not_words(self) -> None:
        counts = count_text("純中文沒有空白")
        assert counts.cjk_chars == 7
        assert counts.latin_words == 0

    def test_chinese_punctuation_is_counted_separately(self) -> None:
        counts = count_text("純中文，沒有空白。")
        assert counts.cjk_chars == 7
        assert counts.cjk_punctuation == 2

    def test_pure_english_counts_words_not_characters(self) -> None:
        counts = count_text("this sentence has five words")
        assert counts.cjk_chars == 0
        assert counts.latin_words == 5

    def test_mixed_script(self) -> None:
        counts = count_text("取樣頻率 governs the aliasing limit.")
        assert counts.cjk_chars == 4
        assert counts.latin_words == 4

    def test_fullwidth_digits_are_words_not_punctuation(self) -> None:
        # U+FF10-U+FF19 sit in the middle of the fullwidth ASCII run.  A
        # block table that spans U+FF01-U+FF20 in one piece files a year
        # written in fullwidth digits under punctuation and drops it from
        # both totals.
        counts = count_text("民國１１３年")
        assert counts.cjk_chars == 3
        assert counts.cjk_punctuation == 0
        assert counts.latin_words == 1

    def test_ideographic_zero_is_a_character(self) -> None:
        # 〇 sits in the CJK Symbols and Punctuation block but is a number.
        counts = count_text("二〇二四年")
        assert counts.cjk_chars == 5
        assert counts.cjk_punctuation == 0

    def test_the_iteration_mark_is_a_character(self) -> None:
        assert count_text("人々").cjk_chars == 2

    def test_brackets_and_stops_of_the_same_block_are_punctuation(self) -> None:
        counts = count_text("「引號」、句號。〜")
        assert counts.cjk_chars == 4
        assert counts.cjk_punctuation == 5

    def test_fullwidth_punctuation_on_both_sides_of_the_digits(self) -> None:
        counts = count_text("！／：＠［｀｛")
        assert counts.cjk_punctuation == 7
        assert counts.latin_words == 0

    def test_chinese_without_spaces_cannot_inflate_the_word_count(self) -> None:
        # The bug this guards against: treating an unspaced Chinese paragraph
        # as one enormous English "word", or as zero words.
        counts = count_text("這是一段完全沒有空白的中文句子" * 20)
        assert counts.cjk_chars == 15 * 20
        assert counts.latin_words == 0

    def test_single_long_line_counts_correctly(self) -> None:
        # Abstracts are routinely written as one unbroken line; a
        # line-oriented counter would report 1 here.
        text = "摘要內容重複" * 50 + " English tail words"
        counts = count_text(text)
        assert counts.cjk_chars == 6 * 50
        assert counts.latin_words == 3

    def test_empty_text(self) -> None:
        assert count_text("") == TextCounts()

    def test_total_is_characters_plus_words(self) -> None:
        counts = count_text("中文 and english")
        assert counts.total == counts.cjk_chars + counts.latin_words

    def test_counts_are_additive(self) -> None:
        first = count_text("一二三 one")
        second = count_text("四五 two three")
        combined = first + second
        assert combined.cjk_chars == 5
        assert combined.latin_words == 3

    def test_whitespace_is_excluded_from_the_character_total(self) -> None:
        assert count_text("  a\t\nb  ").characters == 2

    @pytest.mark.parametrize(
        "space",
        [" ", " ", "　", " ", " ", " ", "\u000b", "\u000c"],
        ids=["nbsp", "emsp", "ideographic", "narrow-nbsp", "medium-math", "ogham", "vtab", "ff"],
    )
    def test_non_ascii_whitespace_is_excluded_too(self, space: str) -> None:
        # An exclusion list of only space/tab/CR/LF lets a fullwidth or
        # non-breaking space count as a character of the thesis.
        assert count_text(f"a{space}b").characters == 2

    def test_ideographic_space_is_whitespace_not_punctuation(self) -> None:
        counts = count_text("甲　乙")
        assert counts.cjk_chars == 2
        assert counts.cjk_punctuation == 0
        assert counts.characters == 2

    def test_astral_plane_characters_count_once(self) -> None:
        assert count_text(chr(0x20000) + chr(0x20001)).cjk_chars == 2


class TestVectorisation:
    """The classification is an array computation, not a Python loop."""

    def test_matches_a_reference_implementation_on_random_input(self) -> None:
        rng = np.random.default_rng(20240601)
        sample = rng.integers(0x20, 0xA000, size=4000)
        text = "".join(chr(int(value)) for value in sample)

        def reference(char: str) -> bool:
            point = ord(char)
            return any(first <= point <= last for first, last in CJK_BLOCKS)

        expected = sum(1 for char in text if reference(char))
        assert count_text(text).cjk_chars == expected

    def test_large_input_is_handled(self) -> None:
        counts = count_text("字" * 200_000)
        assert counts.cjk_chars == 200_000


class TestBlockTables:
    """The parity trick behind the vectorised lookup, and its preconditions."""

    @pytest.mark.parametrize(
        ("blocks", "edges"),
        [
            (CJK_BLOCKS, _CJK_EDGES),
            (CJK_PUNCTUATION_BLOCKS, _PUNCT_EDGES),
            (WHITESPACE_BLOCKS, _SPACE_EDGES),
        ],
        ids=["cjk", "punctuation", "whitespace"],
    )
    def test_every_shipped_table_matches_a_naive_range_test(
        self, blocks: tuple[tuple[int, int], ...], edges: np.ndarray
    ) -> None:
        # Exhaustive over every plane the tables touch, against the obvious
        # implementation.  The searchsorted parity is only correct while the
        # ranges stay disjoint, and this checks that for the real tables.
        points = np.arange(0, 0x34000, dtype=np.int64)
        expected = np.array(
            [any(first <= point <= last for first, last in blocks) for point in points]
        )
        assert np.array_equal(_in_blocks(points, edges), expected)

    def test_whitespace_table_agrees_with_the_regex_tokeniser(self) -> None:
        # The character tally and the word splitter must draw the same line,
        # otherwise a space counts as a character in one place and not the
        # other.
        by_table = {
            point for point in range(0x34000) if _in_blocks(np.array([point]), _SPACE_EDGES)[0]
        }
        by_regex = {point for point in range(0x34000) if re.fullmatch(r"\s", chr(point))}
        assert by_table == by_regex

    def test_touching_ranges_are_merged(self) -> None:
        # Left unmerged, the shared boundary appears twice and flips the
        # parity for every code point above it.
        assert _edges([(5, 9), (10, 12)]).tolist() == [5, 13]
        assert bool(_in_blocks(np.array([11], dtype=np.int64), _edges([(5, 9), (10, 12)]))[0])

    def test_overlapping_ranges_are_merged(self) -> None:
        assert _edges([(5, 20), (10, 12)]).tolist() == [5, 21]

    def test_unsorted_input_is_accepted(self) -> None:
        assert _edges([(20, 25), (0, 3)]).tolist() == [0, 4, 20, 26]

    def test_empty_range_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="empty code-point range"):
            _edges([(9, 5)])
