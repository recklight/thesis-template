"""Word counting for mixed Chinese/English LaTeX prose.

Splitting on whitespace is the usual way to count words, and it is wrong for
a Chinese thesis: a 300-character paragraph with no spaces counts as one
"word".  Counting characters is just as wrong for the English abstract.
So every character is classified by its Unicode block and two figures are
reported side by side.  CJK characters are counted one by one, which is
how a Chinese character count is defined.  Latin words are
the whitespace-delimited runs of the remaining text that contain at least
one letter or digit.

CJK punctuation (the fullwidth comma, the ideographic full stop) is counted
separately and belongs to neither total, matching the convention that
punctuation is not a word.  Fullwidth *digits* are not punctuation: they
carry meaning, so ``１１３`` counts as one word rather than as three marks
to be discarded.  Likewise the letters and numbers that sit in the CJK
Symbols and Punctuation block (々, 〇 in 二〇二四, the Suzhou numerals)
count as characters; only its punctuation and symbols are punctuation.

Implementation note
-------------------
The classification is vectorised: the text is reinterpreted as a NumPy array
of code points in one step, and membership of the CJK blocks is decided for
the whole array at once with :func:`numpy.searchsorted` over a sorted table
of block edges.  A per-character Python loop would run interpreted code for
every character of a 60-page thesis; here the work is one UTF-32
conversion and three ``searchsorted`` calls (CJK, punctuation, whitespace).

References
----------
The block boundaries follow Blocks.txt of the Unicode Standard, Version
17.0.0 (2025): CJK Unified Ideographs and Extensions A-J, and the two CJK
Compatibility Ideographs blocks, described in the East Asia chapter of the
standard.  The split of U+3001-U+303F follows the General_Category of each
code point (P* and S* are punctuation).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

import numpy as np

__all__ = [
    "CJK_BLOCKS",
    "CJK_PUNCTUATION_BLOCKS",
    "WHITESPACE_BLOCKS",
    "TextCounts",
    "count_text",
    "is_cjk",
    "split_latin_words",
]

CJK_BLOCKS: Final[tuple[tuple[int, int], ...]] = (
    # Letters and numbers of the CJK Symbols and Punctuation block.
    (0x3005, 0x3007),  # 々 iteration mark, 〆 closing mark, 〇 ideographic zero
    (0x3021, 0x3029),  # Suzhou numerals one to nine
    (0x3031, 0x3035),  # vertical kana repeat marks
    (0x3038, 0x303C),  # Suzhou ten to thirty, 〻 vertical iteration mark, 〼
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x20000, 0x2A6DF),  # Extension B
    (0x2A700, 0x2EBEF),  # Extensions C-F
    (0x2EBF0, 0x2EE5F),  # Extension I
    (0x2F800, 0x2FA1F),  # Compatibility Ideographs Supplement
    (0x30000, 0x3134F),  # Extension G
    (0x31350, 0x323AF),  # Extension H
    (0x323B0, 0x3347F),  # Extension J
)
"""Code-point ranges counted as Chinese characters (inclusive bounds)."""

CJK_PUNCTUATION_BLOCKS: Final[tuple[tuple[int, int], ...]] = (
    # The punctuation and symbols of CJK Symbols and Punctuation.  U+3000
    # IDEOGRAPHIC SPACE is whitespace and handled by WHITESPACE_BLOCKS; the
    # letters and numbers of the block are in CJK_BLOCKS; the tone marks
    # U+302A-U+302F are neither.
    (0x3001, 0x3004),  # 、。〃〄
    (0x3008, 0x3020),  # brackets, 〒, 〜, quotation marks, 〠
    (0x3030, 0x3030),  # 〰 wavy dash
    (0x3036, 0x3037),  # 〶 〷
    (0x303D, 0x303F),  # 〽 〾 〿
    (0xFE10, 0xFE1F),  # Vertical Forms
    (0xFE30, 0xFE4F),  # CJK Compatibility Forms
    # The fullwidth ASCII run is split around U+FF10-U+FF19, the fullwidth
    # digits: those are digits and belong to the word count, not here.
    (0xFF01, 0xFF0F),  # Fullwidth ! through /
    (0xFF1A, 0xFF20),  # Fullwidth : through @
    (0xFF3B, 0xFF40),  # Fullwidth [ through `
    (0xFF5B, 0xFF65),  # Fullwidth { through the halfwidth middle dot
)
"""Code-point ranges counted as CJK punctuation rather than as characters."""

WHITESPACE_BLOCKS: Final[tuple[tuple[int, int], ...]] = (
    (0x09, 0x0D),  # tab, newline, vertical tab, form feed, carriage return
    (0x1C, 0x20),  # file/group/record/unit separators, space
    (0x85, 0x85),  # next line
    (0xA0, 0xA0),  # no-break space
    (0x1680, 0x1680),  # ogham space mark
    (0x2000, 0x200A),  # en quad through hair space
    (0x2028, 0x2029),  # line and paragraph separator
    (0x202F, 0x202F),  # narrow no-break space
    (0x205F, 0x205F),  # medium mathematical space
    (0x3000, 0x3000),  # ideographic space
)
"""Code-point ranges excluded from :attr:`TextCounts.characters`.

These are exactly the code points Python's ``re`` module matches with
``\\s``, so the character tally and the word tokeniser agree on what
whitespace is.  Counting only the four ASCII spacers would let a fullwidth
or non-breaking space inflate the total.
"""

_WORD_RE: Final[re.Pattern[str]] = re.compile(r"\S+")
# Any Unicode letter or digit, excluding the underscore that ``\w`` also
# matches.  A character class written as an explicit Latin-1 range would
# sweep in U+00D7 MULTIPLICATION SIGN and U+00F7 DIVISION SIGN, which sit
# between the accented letters and are not word characters.
_ALNUM_RE: Final[re.Pattern[str]] = re.compile(r"[^\W_]")


def _edges(blocks: Iterable[tuple[int, int]]) -> np.ndarray:
    """Flatten inclusive ranges into a sorted array of half-open boundaries.

    Parameters
    ----------
    blocks : iterable of tuple of int
        ``(first, last)`` inclusive code-point pairs, in any order.

    Returns
    -------
    numpy.ndarray
        Sorted 1-D array ``[start0, end0 + 1, start1, end1 + 1, ...]``.  A
        code point belongs to one of the blocks exactly when
        ``searchsorted(edges, cp, side="right")`` is odd.

    Raises
    ------
    ValueError
        If a range is empty (``last < first``).

    Notes
    -----
    Ranges that overlap or merely touch are merged first.  The parity trick
    above only works on disjoint, non-adjacent intervals: two touching
    ranges would put the same boundary in the array twice and flip the
    answer for every code point after it.  Merging here means a later edit
    to the block tables cannot silently break the classification.

    Examples
    --------
    >>> _edges([(5, 9), (10, 12)]).tolist()
    [5, 13]
    >>> _edges([(20, 25), (0, 3)]).tolist()
    [0, 4, 20, 26]
    """
    merged: list[list[int]] = []
    for first, last in sorted(blocks):
        if last < first:
            raise ValueError(f"empty code-point range ({first:#x}, {last:#x}).")
        if merged and first <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], last)
        else:
            merged.append([first, last])
    flat: list[int] = []
    for first, last in merged:
        flat.extend((first, last + 1))
    return np.asarray(flat, dtype=np.int64)


_CJK_EDGES: Final[np.ndarray] = _edges(CJK_BLOCKS)
_PUNCT_EDGES: Final[np.ndarray] = _edges(CJK_PUNCTUATION_BLOCKS)
_SPACE_EDGES: Final[np.ndarray] = _edges(WHITESPACE_BLOCKS)


def _codepoints(text: str) -> np.ndarray:
    """Convert *text* to a 1-D array of code points without a Python loop.

    Parameters
    ----------
    text : str
        Any string, including astral-plane characters.

    Returns
    -------
    numpy.ndarray
        ``int64`` array of code points, one element per character.

    Notes
    -----
    Encoding to UTF-32LE and reinterpreting the buffer gives one 32-bit code
    point per character in a single pass; everything after that is array
    arithmetic.
    """
    if not text:
        return np.empty(0, dtype=np.int64)
    buffer = text.encode("utf-32-le")
    return np.frombuffer(buffer, dtype="<u4").astype(np.int64)


def _in_blocks(points: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Return a boolean mask of code points falling inside *edges*."""
    if points.size == 0:
        return np.zeros(0, dtype=bool)
    return (np.searchsorted(edges, points, side="right") % 2).astype(bool)


def is_cjk(char: str) -> bool:
    """Return ``True`` when *char* counts as a CJK character.

    Parameters
    ----------
    char : str
        A single character.

    Returns
    -------
    bool
        Whether the character lies in one of :data:`CJK_BLOCKS`.

    Raises
    ------
    ValueError
        If *char* is not exactly one character.

    Examples
    --------
    >>> is_cjk("論")
    True
    >>> is_cjk("A"), is_cjk("，")
    (False, False)
    """
    if len(char) != 1:
        raise ValueError(f"is_cjk() expects a single character, got {len(char)}.")
    return bool(_in_blocks(_codepoints(char), _CJK_EDGES)[0])


@dataclass(frozen=True, slots=True)
class TextCounts:
    """Counts produced by :func:`count_text`.

    Attributes
    ----------
    cjk_chars : int
        Number of characters in :data:`CJK_BLOCKS`: the ideographs plus
        marks such as 々 and 〇.
    latin_words : int
        Number of whitespace-delimited tokens containing a letter or digit.
    cjk_punctuation : int
        Number of fullwidth/ideographic punctuation marks.
    characters : int
        Total number of characters considered, whitespace excluded.
    """

    cjk_chars: int = 0
    latin_words: int = 0
    cjk_punctuation: int = 0
    characters: int = 0

    @property
    def total(self) -> int:
        """int: CJK characters plus Latin words, the figure graduate offices ask for."""
        return self.cjk_chars + self.latin_words

    def __add__(self, other: TextCounts) -> TextCounts:
        """Add two count objects field by field."""
        if not isinstance(other, TextCounts):  # pragma: no cover - defensive
            return NotImplemented
        return TextCounts(
            cjk_chars=self.cjk_chars + other.cjk_chars,
            latin_words=self.latin_words + other.latin_words,
            cjk_punctuation=self.cjk_punctuation + other.cjk_punctuation,
            characters=self.characters + other.characters,
        )


def split_latin_words(text: str) -> list[str]:
    """Split *text* into word-like tokens.

    Parameters
    ----------
    text : str
        Text with CJK characters already removed.

    Returns
    -------
    list of str
        Whitespace-delimited tokens that contain at least one letter or
        digit.  Stray punctuation runs such as ``"--"`` are dropped.

    Examples
    --------
    >>> split_latin_words("sampling rate -- estimation, 2024")
    ['sampling', 'rate', 'estimation,', '2024']

    Runs of punctuation are dropped, and so are the arithmetic signs that
    sit inside the accented-Latin range:

    >>> split_latin_words("3 × 4 = 12")
    ['3', '4', '12']
    """
    return [token for token in _WORD_RE.findall(text) if _ALNUM_RE.search(token)]


def count_text(text: str) -> TextCounts:
    """Count CJK characters and Latin words in mixed-script prose.

    Parameters
    ----------
    text : str
        Text with LaTeX markup already removed, usually
        :attr:`thesis_cli.parsers.tex_scanner.TexFile.prose`.

    Returns
    -------
    TextCounts
        Character and word tallies.

    Notes
    -----
    CJK characters are replaced by a space before the Latin tokeniser runs,
    so a Chinese sentence that is not separated by spaces cannot inflate the
    English word count.

    Examples
    --------
    >>> counts = count_text("取樣頻率 governs the aliasing limit.")
    >>> counts.cjk_chars, counts.latin_words
    (4, 4)
    >>> count_text("純中文，沒有空白。").cjk_chars
    7

    Fullwidth digits are words, not punctuation, and a fullwidth space is
    whitespace rather than a character:

    >>> count_text("民國１１３年")
    TextCounts(cjk_chars=3, latin_words=1, cjk_punctuation=0, characters=6)
    >>> count_text("甲　乙").characters
    2
    """
    if not text:
        return TextCounts()

    points = _codepoints(text)
    cjk_mask = _in_blocks(points, _CJK_EDGES)
    punct_mask = _in_blocks(points, _PUNCT_EDGES)
    cjk_chars = int(np.count_nonzero(cjk_mask))
    cjk_punctuation = int(np.count_nonzero(punct_mask))

    if cjk_chars or cjk_punctuation:
        remove_mask = cjk_mask | punct_mask
        replaced = np.where(remove_mask, np.int64(ord(" ")), points)
        latin_source = replaced.astype("<u4").tobytes().decode("utf-32-le")
    else:
        latin_source = text

    latin_words = len(split_latin_words(latin_source))
    characters = int(np.count_nonzero(~_in_blocks(points, _SPACE_EDGES)))

    return TextCounts(
        cjk_chars=cjk_chars,
        latin_words=latin_words,
        cjk_punctuation=cjk_punctuation,
        characters=characters,
    )
