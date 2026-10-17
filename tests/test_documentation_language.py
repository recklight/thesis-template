"""Guards on the documentation language and on what the template may contain.

README.md is English; README.zh-TW.md and the other docs are Traditional
Chinese with Taiwan usage.  The two READMEs are kept in step, so both get
the same layout checks.  Two things are easy to break by accident once a
project has Chinese text in it, and neither shows up in a normal review:

* a paragraph pasted from a mainland-Chinese source brings Simplified
  characters or mainland terminology with it;
* a real name, an institution or a local disk path leaks into the template
  that other people are meant to copy.

Both are checked here over the whole repository.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

import pytest

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
README_ZH = REPO / "README.zh-TW.md"

# Characters whose Traditional counterpart is a *different* code point.
# Every entry below was chosen because it has no use in Traditional Chinese;
# characters shared by both scripts (面, 骨, 出, 入, 率, 示, 境, 只, 里, 后,
# 台, 几, 准 ...) are deliberately absent, because a hit on one of those
# would be a false alarm.  This module is left out of its own scan, since
# the table necessarily contains what it forbids.
SIMPLIFIED_ONLY = (
    "这说书图产东报单发国过号会还将节类历码门问题应种众专转装状"
    "观见语议试论证识钟银错长闻间闭队难页顺须风飞馆马验养鱼鸟鸣齐龙"
    "个为们时来业动员务实现处买卖乐习乡亲仅从优传伤价体关开无与"
    "车轮软网络数据库编译运输给结线终统绝继续维护环总该详细简杂"
    "确认执删选项设备级标记录检测频样滤声归档压缩显隐"
)

# Mainland wording and the Taiwan wording to use instead.  Written as
# escapes, so searching the repository for one of these terms finds a real
# slip rather than this list.
MAINLAND_TERMS = {
    "\u6587\u4ef6\u5939": "資料夾",
    "\u5185\u5b58": "記憶體",
    "\u51fd\u6570": "函式",
    "\u6570\u7ec4": "陣列",
    "\u4f18\u5316": "最佳化",
    "\u5206\u8fa8\u7387": "解析度",
    "\u9ed8\u8ba4": "預設",
    "\u8f6f\u4ef6": "軟體",
    "\u5b57\u4f53": "字型",
    "\u53c2\u6570": "參數",
    "\u7f13\u5b58": "快取",
    "\u94fe\u63a5": "連結",
    "\u91c7\u6837\u7387": "取樣率",
    "\u566a\u58f0": "雜訊",
    "\u6ee4\u6ce2\u5668": "濾波器",
    "\u7f51\u7edc": "網路",
}

_SELF = Path(__file__).resolve()

#: Every level-2 heading of the English README, in order.
EXPECTED_H2 = [
    "Contents",
    "Overview",
    "Installation",
    "Quick start",
    "Method",
    "Usage examples",
    "CLI reference",
    "Project structure",
    "Configuration file thesis.toml",
    "Data preparation",
    "Exit codes and CI",
    "Output format",
    "Design notes",
    "Development and testing",
    "References",
    "License",
]

#: The same headings in the Traditional Chinese README.
EXPECTED_H2_ZH = [
    "目錄",
    "專案簡介",
    "安裝",
    "快速開始",
    "方法說明",
    "使用範例",
    "指令說明對照表",
    "專案結構",
    "設定檔 thesis.toml",
    "資料準備",
    "退出碼與 CI 整合",
    "輸出格式",
    "設計重點",
    "開發與測試",
    "References",
    "License",
]

_CJK = re.compile(r"[\u4e00-\u9fff]")


def _text_files() -> list[Path]:
    """Collect every file in the repository that may carry Chinese prose."""
    paths: list[Path] = [README, README_ZH, REPO / "CHANGELOG.md", REPO / "CONTRIBUTING.md"]
    for pattern in ("template/**/*.tex", "template/**/*.cls", "src/**/*.py", "tests/**/*.py"):
        paths.extend(sorted(REPO.glob(pattern)))
    return [path for path in paths if path.is_file() and path.resolve() != _SELF]


def _prose_files() -> list[Path]:
    """Collect the files whose Chinese prose is read by a human."""
    paths: list[Path] = [README, README_ZH, REPO / "CHANGELOG.md", REPO / "CONTRIBUTING.md"]
    for pattern in ("template/**/*.tex", "template/**/*.cls", "template/**/*.toml"):
        paths.extend(sorted(REPO.glob(pattern)))
    return [path for path in paths if path.is_file()]


def _report(path: Path, number: int, detail: str) -> str:
    return f"{path.relative_to(REPO).as_posix()}:{number}: {detail}"


def _h2_headings(path: Path) -> list[str]:
    return re.findall(r"^## (.+?)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)


class TestTraditionalChinese:
    """The Chinese documentation is written in Taiwanese Traditional Chinese."""

    def test_no_simplified_only_characters(self) -> None:
        banned = set(SIMPLIFIED_ONLY)
        offenders: list[str] = []
        for path in _text_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for char in line:
                    if char in banned:
                        offenders.append(
                            _report(path, number, f"simplified character U+{ord(char):04X}")
                        )
        assert offenders == []

    def test_no_mainland_terminology(self) -> None:
        offenders: list[str] = []
        for path in _text_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for term, preferred in MAINLAND_TERMS.items():
                    if term in line:
                        offenders.append(_report(path, number, f"use {preferred!r} instead"))
        assert offenders == []

    def test_no_character_outside_the_traditional_repertoire(self) -> None:
        # Big5 encodes Traditional Chinese only, so a CJK character it cannot
        # represent is almost certainly a Simplified form.  This catches the
        # characters the hand-written table above does not list.
        #
        # Scoped to the prose that must read as Traditional Chinese.  Test
        # modules legitimately contain rare boundary code points (a character
        # at the end of a Unicode block, say), which Big5 also lacks without
        # being Simplified.
        offenders: list[str] = []
        for path in _prose_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for char in line:
                    if not "一" <= char <= "鿿":
                        continue
                    try:
                        char.encode("cp950")
                    except UnicodeEncodeError:
                        offenders.append(
                            _report(path, number, f"not Traditional: U+{ord(char):04X}")
                        )
        assert offenders == []


class TestReadmes:
    """The English and the Chinese README have the same layout."""

    @staticmethod
    def _check_header(path: Path, language_switch: str) -> None:
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0].startswith("# thesis-cli — ")
        assert lines[2].startswith("[![CI](")
        assert lines[3].startswith("[![Python](")
        assert lines[4].startswith("[![License: MIT](")
        assert lines[6] == language_switch

    def test_english_headings_are_in_order(self) -> None:
        assert _h2_headings(README) == EXPECTED_H2

    def test_chinese_headings_are_in_order(self) -> None:
        assert _h2_headings(README_ZH) == EXPECTED_H2_ZH

    def test_english_header_author_and_license(self) -> None:
        self._check_header(README, "English | [繁體中文](README.zh-TW.md)")
        text = README.read_text(encoding="utf-8")
        assert "\nAuthor: RL\n" in text
        assert "MIT License, Copyright (c) 2026 RL. See [LICENSE](LICENSE)." in text

    def test_chinese_header_author_and_license(self) -> None:
        self._check_header(README_ZH, "[English](README.md) | 繁體中文")
        text = README_ZH.read_text(encoding="utf-8")
        assert "\n作者：RL\n" in text
        assert "MIT License，Copyright (c) 2026 RL。詳見 [LICENSE](LICENSE)。" in text

    def test_english_readme_is_english(self) -> None:
        # The Chinese left in it is example text: 緒論, 室內定位, 二〇二四,
        # the chapter-number styles and the values passed to init --set.
        text = README.read_text(encoding="utf-8")
        assert len(_CJK.findall(text)) < 40
        assert len(re.findall(r"\b[a-z]{3,}\b", text)) > 3000

    def test_chinese_readme_is_chinese(self) -> None:
        assert len(_CJK.findall(README_ZH.read_text(encoding="utf-8"))) > 2000

    @pytest.mark.parametrize("path", [README, README_ZH], ids=["README.md", "README.zh-TW.md"])
    def test_code_and_option_names_are_not_translated(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        for token in (
            "thesis-cli build",
            "thesis.toml",
            "--strict",
            "--json",
            "check-refs",
            "figures.extensions",
            "python -m pytest -q",
        ):
            assert token in text, token


class TestNoPersonalOrLocalContent:
    """Nothing identifying may ship in the repository.

    The checks below are written as *categories* rather than as a list of
    banned words.  A denylist of real names, institutions and machine paths
    would have to spell them out in this file, which defeats its purpose; a
    category names nobody, and it also matches a name or a path that nobody
    thought to put on a list.
    """

    # A drive-qualified path that goes at least one directory deep.  A bare
    # "C:/x" in a doctest is a synthetic example; "D:\something\..." is a
    # real location on somebody's machine.
    DRIVE_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'<>|]*[\\/]")

    # Any absolute path at all, which is what must never reach a template
    # that other people copy.
    ABSOLUTE_PATH = re.compile(
        r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]|(?<![\w.])/(?:home|Users|mnt|media|opt|srv)/"
    )

    # A personal account or contact detail.
    HOME_DIR = re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+")
    EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}")

    # A student or staff number: a short letter prefix followed by a long
    # run of digits.
    ID_NUMBER = re.compile(r"\b[A-Za-z]{1,3}\d{7,}\b")

    @staticmethod
    def _scan(paths: list[Path], pattern: re.Pattern[str]) -> list[str]:
        offenders: list[str] = []
        for path in paths:
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "http" in line:  # URLs are not filesystem paths
                    continue
                match = pattern.search(line)
                if match is not None:
                    offenders.append(_report(path, number, f"matched {match.group(0)!r}"))
        return offenders

    def test_no_machine_specific_paths(self) -> None:
        assert self._scan(_text_files(), self.DRIVE_PATH) == []

    def test_no_home_directories(self) -> None:
        assert self._scan(_text_files(), self.HOME_DIR) == []

    def test_no_email_addresses(self) -> None:
        assert self._scan(_text_files(), self.EMAIL) == []

    def test_no_identification_numbers(self) -> None:
        assert self._scan(_text_files(), self.ID_NUMBER) == []

    def test_no_absolute_path_anywhere_in_the_template(self) -> None:
        # Every file that 'thesis-cli init' copies, not a chosen few: the
        # template is what lands in somebody else's project.
        template_files = sorted(p for p in (REPO / "template").rglob("*") if p.is_file())
        assert template_files, "the template is missing"
        readable = [p for p in template_files if p.suffix not in {".png", ".pdf"}]
        assert self._scan(readable, self.ABSOLUTE_PATH) == []

    @pytest.mark.parametrize("name", ["thesis.tex", "config/names.tex", "structure/frontpages.tex"])
    def test_front_matter_holds_placeholders_only(self, name: str) -> None:
        # The three files where a real name would otherwise sit.
        path = REPO / "template" / name
        assert path.is_file()
        text = path.read_text(encoding="utf-8")
        assert self.EMAIL.search(text) is None
        assert self.ID_NUMBER.search(text) is None

    def test_no_bounding_box_files_are_shipped(self) -> None:
        # An ebb-generated .bb records the absolute path of the machine that
        # produced it in its %%Title comment.
        assert list((REPO / "template").rglob("*.bb")) == []
        assert list((REPO / "template").rglob("*.xbb")) == []

    def test_the_patterns_actually_catch_something(self) -> None:
        # A guard that matches nothing is indistinguishable from a guard
        # that works, so each pattern is exercised against a sample.
        assert self.DRIVE_PATH.search(r"opened D:\thesis\ch1.tex")
        assert self.DRIVE_PATH.search("opened X:/data/run/")
        assert self.DRIVE_PATH.search("C:/x") is None
        assert self.HOME_DIR.search("/home/someone/thesis")
        assert self.HOME_DIR.search("/Users/someone/thesis")
        assert self.EMAIL.search("write to someone@example.edu")
        assert self.ID_NUMBER.search("s1234567 submitted")
        assert self.ID_NUMBER.search("TeX Live 2023") is None
        assert self.ABSOLUTE_PATH.search("/opt/texlive/bin")
        assert self.ABSOLUTE_PATH.search("figures/plot.pdf") is None


class TestNoMangledEscapes:
    r"""LaTeX macro names in the documentation keep their backslash.

    The snippets are generated from Python strings in ``make_snippets.py``,
    and a LaTeX string that loses its ``r`` prefix still looks right at a
    glance: the ``\f`` of ``\fig`` becomes a form feed, the ``\b`` of
    ``\begin`` a backspace, the ``\t`` of ``\tablename`` a tab, and ``\n`` a
    line break.  LaTeX comments and Markdown show the result without
    complaint, so the control character, or a macro stem at the start of a
    line, is the only trace.  The READMEs and the template comments get the
    same check.
    """

    # Control characters that stand where a backslash and a letter should be.
    FORBIDDEN: ClassVar[dict[str, str]] = {
        chr(0x07): r"\a",
        chr(0x08): r"\b",
        chr(0x0B): r"\v",
        chr(0x0C): r"\f",
        chr(0x0D): r"\r",
        chr(0x00): r"\0",
    }

    # What a macro name looks like once its backslash and first letter
    # have been swallowed.
    MANGLED_STEMS = (
        "egin{",  # \begin
        "ibliography",  # \bibliography
        "ibdata",  # \bibdata
        "igurename",  # \figurename
        "ablename",  # \tablename
        "ewlabel",  # \newlabel
        "ocite",  # \nocite
        "ootnote",  # \footnote
    )

    @classmethod
    def _mangled(cls, line: str, *, at_line_start: bool) -> list[str]:
        """Return the macro stems in *line* that lost their backslash."""
        found = [
            stem for char in cls.FORBIDDEN for stem in cls.MANGLED_STEMS if char + stem in line
        ]
        if at_line_start:
            found += [stem for stem in cls.MANGLED_STEMS if line.lstrip().startswith(stem)]
        return sorted(set(found))

    def test_no_control_characters(self) -> None:
        offenders: list[str] = []
        for path in _prose_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for char, escape in self.FORBIDDEN.items():
                    if char in line:
                        offenders.append(_report(path, number, f"control character from {escape}"))
        assert offenders == []

    @pytest.mark.parametrize("path", [README, README_ZH], ids=["README.md", "README.zh-TW.md"])
    def test_no_tab_characters_in_the_readmes(self, path: Path) -> None:
        # The READMEs are indented with spaces.  A tab in one is either an
        # editor slip or a \t whose backslash went missing, which is how
        # \tablename turns into a tab and "ablename".
        assert "\t" not in path.read_text(encoding="utf-8")

    def test_no_macro_name_lost_its_first_letter(self) -> None:
        offenders: list[str] = []
        for path in _prose_files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for stem in self._mangled(line, at_line_start=True):
                    offenders.append(_report(path, number, f"orphaned macro stem {stem!r}"))
        assert offenders == []

    def test_the_detector_catches_each_shape(self) -> None:
        # The samples are built with chr() so this file cannot contain the
        # control characters it looks for.
        assert self._mangled("`" + chr(0x0C) + "igurename`", at_line_start=False) == ["igurename"]
        assert self._mangled("`" + chr(0x08) + "egin{document}`", at_line_start=False) == ["egin{"]
        # A lost \n leaves no control character at all: the stem simply
        # starts the next line.
        assert self._mangled("    ewlabel` format", at_line_start=True) == ["ewlabel"]
        assert self._mangled("a clean line about figures", at_line_start=True) == []
