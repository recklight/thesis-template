r"""Structural guards on the LaTeX template.

These cannot replace a real compile, but they catch the mistakes that a
compile reports two hundred lines away from their cause, and the kind of rot
that builds up unnoticed in a template:

* unbalanced braces;
* a ``\Thesis*`` macro used but never defined;
* a ``\Thesis*`` macro defined but never invoked, i.e. dead code;
* an ``\input`` that does not resolve;
* files whose job is to be included accidentally becoming compilable, or
  the main file accidentally ceasing to be;
* constructs that compile on one path and stop on another: an ``\item``
  label cut short by a ``]``, a lettered appendix chapter handed to the
  Chinese numerals, a command the spine's ``article`` class lacks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from thesis_cli.commands.build import validate_main_document
from thesis_cli.config import load_config
from thesis_cli.errors import ValidationError
from thesis_cli.parsers.tex_scanner import clean_source, scan_document

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "template"

_DEFINE = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|def|newenvironment|newlength"
    r"|newsavebox|newif|newcounter|DeclareRobustCommand)"
    r"\s*\*?\s*\{?\\?([A-Za-z@]+)"
)
# The optional "if" prefix matches the use of a \newif conditional, which
# is spelled \ifFoo rather than \Foo.
_USE = re.compile(r"\\((?:if)?(?:Thesis|thesis)[A-Za-z@]*)")

# Deliberate alternatives: the thesis writer or thesis.toml picks exactly one
# of each group, so the others are meant to sit unused.  \ThesisWatermarkThisPage
# is written by hand on the pages to mark in "selected" mode.
_ALTERNATIVES = frozenset(
    {
        "ThesisLabelsChinese",
        "ThesisLabelsEnglish",
        "ThesisTocStyleArabic",
        "ThesisTocStyleCJK",
        "ThesisTocStyleCJKFull",
        "ThesisWatermarkThisPage",
    }
)


def _template_sources() -> list[Path]:
    return sorted([*TEMPLATE.rglob("*.tex"), *TEMPLATE.rglob("*.cls")])


def _brace_balance(text: str) -> int:
    """Return the net brace depth of *text*, honouring backslash escapes."""
    depth = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return depth


@pytest.fixture(scope="module")
def macro_usage() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Return ``(definitions, invocations)`` for every ``\\Thesis*`` macro."""
    definitions: dict[str, list[str]] = {}
    invocations: dict[str, list[str]] = {}
    for path in _template_sources():
        code = clean_source(path.read_text(encoding="utf-8")).code
        name = path.relative_to(TEMPLATE).as_posix()
        sites = {
            match.group(1): match.start()
            for match in _DEFINE.finditer(code)
            if match.group(1).lower().startswith(("thesis", "ifthesis"))
        }
        for macro in sites:
            definitions.setdefault(macro, []).append(name)
            # \newif\ifFoo also defines \Footrue and \Foofalse.
            if macro.startswith("if"):
                definitions.setdefault(macro[2:] + "true", []).append(name)
                definitions.setdefault(macro[2:] + "false", []).append(name)
        for match in _USE.finditer(code):
            macro = match.group(1)
            if macro in sites and abs(match.start() - sites[macro]) < 40:
                continue  # the definition itself
            invocations.setdefault(macro, []).append(name)
    return definitions, invocations


class TestSyntax:
    """Cheap structural checks over every template file."""

    def test_braces_balance_in_every_file(self) -> None:
        offenders = []
        for path in _template_sources():
            code = clean_source(path.read_text(encoding="utf-8")).code
            depth = _brace_balance(code)
            if depth != 0:
                offenders.append(f"{path.relative_to(TEMPLATE).as_posix()}: {depth:+d}")
        assert offenders == []

    def test_every_file_is_utf8(self) -> None:
        for path in _template_sources():
            path.read_bytes().decode("utf-8")

    def test_no_tab_characters(self) -> None:
        # Tabs in LaTeX source are a portability nuisance and hide alignment
        # mistakes; the whole template is spaces.
        offenders = [
            path.relative_to(TEMPLATE).as_posix()
            for path in _template_sources()
            if "\t" in path.read_text(encoding="utf-8")
        ]
        assert offenders == []


class TestMacros:
    r"""Every ``\Thesis*`` macro is both defined and used."""

    def test_none_is_used_without_being_defined(
        self, macro_usage: tuple[dict[str, list[str]], dict[str, list[str]]]
    ) -> None:
        definitions, invocations = macro_usage
        known = set(definitions)
        offenders = {
            macro: sorted(set(places))
            for macro, places in invocations.items()
            if macro not in known and not macro.startswith("thesis@")
        }
        assert offenders == {}

    def test_none_is_defined_without_being_used(
        self, macro_usage: tuple[dict[str, list[str]], dict[str, list[str]]]
    ) -> None:
        definitions, invocations = macro_usage
        offenders = {
            macro: places
            for macro, places in definitions.items()
            if macro not in invocations
            and macro not in _ALTERNATIVES
            and not macro.startswith("thesis@")
        }
        assert offenders == {}, "dead macros in the template"

    def test_the_three_toc_styles_exist(
        self, macro_usage: tuple[dict[str, list[str]], dict[str, list[str]]]
    ) -> None:
        definitions, _ = macro_usage
        for style in ("ThesisTocStyleArabic", "ThesisTocStyleCJK", "ThesisTocStyleCJKFull"):
            assert style in definitions, style

    def test_figure_macros_match_the_scanner_table(self) -> None:
        from thesis_cli.config import DEFAULT_FIGURE_MACROS, DEFAULT_LABEL_MACROS

        source = (TEMPLATE / "macros" / "figure_macros.tex").read_text(encoding="utf-8")
        code = clean_source(source).code
        for macro in DEFAULT_FIGURE_MACROS:
            if macro == "includegraphics":
                continue
            pattern = re.compile(r"\\newcommand\{\\" + re.escape(macro) + r"\}\[(\d+)\]")
            match = pattern.search(code)
            assert match is not None, f"\\{macro} is in the scanner table but not in the template"
            arity = int(match.group(1))
            assert DEFAULT_FIGURE_MACROS[macro] <= arity, (
                f"\\{macro} takes {arity} arguments but the scanner reads its file name "
                f"from argument {DEFAULT_FIGURE_MACROS[macro]}"
            )
            if macro in DEFAULT_LABEL_MACROS:
                assert DEFAULT_LABEL_MACROS[macro] <= arity, (
                    f"\\{macro} takes {arity} arguments but the scanner reads its label "
                    f"from argument {DEFAULT_LABEL_MACROS[macro]}"
                )


class TestDocumentAssembly:
    """The template is a working project, not a pile of files."""

    def test_the_main_file_is_compilable(self) -> None:
        validate_main_document(TEMPLATE / "thesis.tex")

    @pytest.mark.parametrize(
        "fragment",
        [
            "config/names.tex",
            "config/definitions.tex",
            "macros/figure_macros.tex",
            "structure/frontpages.tex",
            "chapters/ch1_introduction.tex",
        ],
    )
    def test_fragments_are_refused_as_build_targets(self, fragment: str) -> None:
        with pytest.raises(ValidationError):
            validate_main_document(TEMPLATE / fragment)

    def test_the_spine_is_a_separate_compilable_document(self) -> None:
        validate_main_document(TEMPLATE / "structure" / "coverbridge.tex")

    def test_every_input_resolves(self) -> None:
        config = load_config(TEMPLATE, required=True)
        document = scan_document(
            config.main_path,
            config.root,
            macros=config.figure_macros,
            label_macros=config.label_macros,
        )
        assert document.missing_inputs == ()

    def test_the_assembly_reaches_every_chapter_and_content_file(self) -> None:
        config = load_config(TEMPLATE, required=True)
        document = scan_document(
            config.main_path,
            config.root,
            macros=config.figure_macros,
            label_macros=config.label_macros,
        )
        reached = {path.name for path in document.paths}
        for name in sorted(p.name for p in (TEMPLATE / "chapters").glob("*.tex")):
            assert name in reached, f"{name} is never pulled in"
        for name in sorted(p.name for p in (TEMPLATE / "content").glob("*.tex")):
            assert name in reached, f"{name} is never pulled in"

    def test_no_content_file_is_empty(self) -> None:
        # A zero-byte file still satisfies \input, so nothing else in the
        # toolchain would ever report one.
        for path in sorted((TEMPLATE / "content").glob("*.tex")):
            assert path.stat().st_size > 0, path.name

    def test_every_snippet_is_syntactically_balanced(self) -> None:
        offenders = []
        for path in sorted((TEMPLATE / "snippets").glob("*.tex")):
            code = clean_source(path.read_text(encoding="utf-8")).code
            if _brace_balance(code) != 0:
                offenders.append(path.name)
        assert offenders == []

    def test_the_template_config_loads(self) -> None:
        config = load_config(TEMPLATE, required=True)
        assert config.main_path.is_file()
        assert all(path.is_file() for path in config.bib_paths)
        assert all(path.is_dir() for path in config.figure_dirs)
        assert config.watermark_path.is_file()


def _optional_argument(text: str, start: int) -> str:
    """Return a ``[...]`` argument the way LaTeX delimits it.

    It ends at the first ``]`` outside braces; brackets do not nest.
    """
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "]" and depth == 0:
            return text[start:index]
    return text[start:]


def _code(relative: str) -> str:
    return clean_source((TEMPLATE / relative).read_text(encoding="utf-8")).code


class TestCompilePaths:
    """Constructs that look fine and stop a build on some route."""

    def test_item_labels_close_where_latex_closes_them(self) -> None:
        # \item[$x[n]$] ends its label at the first ], leaving $x[n open.
        offenders = []
        for path in _template_sources():
            code = clean_source(path.read_text(encoding="utf-8")).code
            for match in re.finditer(r"\\item\s*\[", code):
                label = _optional_argument(code, match.end())
                if label.count("$") % 2:
                    offenders.append(f"{path.relative_to(TEMPLATE).as_posix()}: [{label}]")
        assert offenders == []

    def test_the_detector_catches_an_unbraced_bracket(self) -> None:
        assert _optional_argument("$x[n]$]", 0) == "$x[n"
        assert _optional_argument("{$x[n]$}]", 0) == "{$x[n]$}"

    def test_appendix_chapters_skip_the_chinese_numerals(self) -> None:
        # After \appendix the chapter number is a letter; \ThesisNumberToCJK
        # on "A" is a "Missing number" error that halts the build.
        backpages = _code("structure/backpages.tex")
        assert re.search(r"\\appendix\s*\\ThesisAppendixNumbering\b", backpages)
        numbering = _code("config/chapter_numbering.tex")
        body = numbering[numbering.index("\\newcommand{\\ThesisAppendixNumbering}") :]
        body = body[: body.index("\n}")]
        assert "\\renewcommand{\\countermapping}[1]{##1}" in body
        assert "ThesisNumberToCJK" not in body

    def test_the_spine_provides_what_article_lacks(self) -> None:
        # definitions.tex renames \chaptername and \bibname; article has
        # neither, and \renewcommand on a missing command is an error.
        spine = _code("structure/coverbridge.tex")
        position = spine.index("\\input{config/definitions.tex}")
        for name in ("chaptername", "bibname"):
            assert "\\renewcommand{\\" + name + "}" in _code("config/definitions.tex")
            provided = spine.find("\\providecommand{\\" + name + "}")
            assert 0 <= provided < position, name

    @pytest.mark.parametrize(
        ("document", "packages"),
        [
            ("thesis.tex", ("graphicx", "xcolor", "hyperref")),
            ("structure/coverbridge.tex", ("graphicx",)),
        ],
    )
    def test_the_dvi_route_names_the_dvipdfmx_driver(
        self, document: str, packages: tuple[str, ...]
    ) -> None:
        # In DVI mode graphicx, xcolor and hyperref default to dvips, and the
        # dvips driver cannot read the PNG figures the template ships.
        code = _code(document)
        guard = code.index("\\ifPDFTeX\\ifnum\\pdfoutput<1")
        for package in packages:
            option = code.index("\\PassOptionsToPackage{dvipdfmx}{" + package + "}")
            loaded = re.search(r"\\usepackage(?:\[[^\]]*\])?\{" + package + r"\}", code)
            assert loaded is not None, package
            assert guard < option < loaded.start(), package

    def test_the_watermark_is_loaded_only_by_the_generated_settings(self) -> None:
        # A default \ThesisWatermark{all} in watermark.tex would load the
        # image even when thesis.toml says "off".
        assert "\\ThesisWatermark{" not in _code("config/watermark.tex")
        assert "\\ThesisWatermark{all}" in _code("config/generated.tex")
