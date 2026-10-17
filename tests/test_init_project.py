r"""Tests for project creation.

The point of ``init`` is that a new thesis starts from a neutral template
rather than from somebody else's finished one, so the tests check that the
copy is complete and that every name field starts as a placeholder.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from thesis_cli.commands import init_project as init_module
from thesis_cli.commands.init_project import (
    NAME_FIELDS,
    apply_name_values,
    init_project,
    locate_template_dir,
    parse_assignments,
    render_config_toml,
)
from thesis_cli.config import DEFAULT_ARTIFACT_EXTENSIONS, CleanConfig, load_config
from thesis_cli.errors import ProjectLayoutError, ValidationError


class TestApplyNameValues:
    r"""Substituting into ``\renewcommand`` lines."""

    def test_replaces_the_named_value(self) -> None:
        source = "\\renewcommand{\\cTitle}{論文中文題目}\n"
        result = apply_name_values(source, {"cTitle": "室內定位研究"})
        assert result.strip() == "\\renewcommand{\\cTitle}{室內定位研究}"

    def test_leaves_other_macros_alone(self) -> None:
        source = "\\renewcommand{\\cTitle}{A}\n\\renewcommand{\\myCname}{B}\n"
        result = apply_name_values(source, {"cTitle": "X"})
        assert "\\renewcommand{\\myCname}{B}" in result

    def test_comments_survive(self) -> None:
        source = "%% a comment\n\\renewcommand{\\cTitle}{A}\n"
        assert "%% a comment" in apply_name_values(source, {"cTitle": "X"})

    def test_unknown_macro_is_ignored(self) -> None:
        source = "\\renewcommand{\\cTitle}{A}\n"
        assert apply_name_values(source, {"nothing": "X"}) == source

    def test_empty_value_clears_the_field(self) -> None:
        source = "\\renewcommand{\\advisorCnameB}{Someone}\n"
        assert apply_name_values(source, {"advisorCnameB": ""}).strip().endswith("{}")

    @pytest.mark.parametrize(
        ("value", "written"),
        [
            ("50% of R&D", "50\\% of R\\&D"),
            ("{draft} #1", "\\{draft\\} \\#1"),
            ("$x_1$", "\\$x\\_1\\$"),
            ("a^b~c", "a\\textasciicircum{}b\\textasciitilde{}c"),
            ("back\\slash", "back\\textbackslash{}slash"),
        ],
    )
    def test_latex_special_characters_are_escaped(self, value: str, written: str) -> None:
        # An unescaped % comments out the closing brace of \renewcommand.
        result = apply_name_values("\\renewcommand{\\eTitle}{X}\n", {"eTitle": value})
        assert result == f"\\renewcommand{{\\eTitle}}{{{written}}}\n"

    def test_an_escaped_value_can_be_replaced_again(self) -> None:
        once = apply_name_values("\\renewcommand{\\eTitle}{X}\n", {"eTitle": "a^b {c} 5%"})
        twice = apply_name_values(once, {"eTitle": "Plain"})
        assert twice == "\\renewcommand{\\eTitle}{Plain}\n"


class TestParseAssignments:
    """``--set name=value`` parsing."""

    def test_parses_pairs(self) -> None:
        assert parse_assignments(["cTitle=A", "myCname=B"]) == {"cTitle": "A", "myCname": "B"}

    def test_accepts_a_leading_backslash(self) -> None:
        assert parse_assignments([r"\cTitle=A"]) == {"cTitle": "A"}

    def test_value_may_contain_equals(self) -> None:
        assert parse_assignments(["cTitle=a=b"]) == {"cTitle": "a=b"}

    def test_missing_equals_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="name=value"):
            parse_assignments(["cTitle"])

    def test_unknown_field_is_rejected_with_the_list(self) -> None:
        with pytest.raises(ValidationError) as info:
            parse_assignments(["nonsense=x"])
        assert "cTitle" in (info.value.hint or "")

    def test_empty_input(self) -> None:
        assert parse_assignments([]) == {}


class TestRenderConfigToml:
    """The generated thesis.toml."""

    def test_is_valid_and_round_trips(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text(render_config_toml(), encoding="utf-8")
        config = load_config(tmp_path, required=True)
        assert config.main == "thesis.tex"
        assert config.build.engine.value == "xelatex"

    def test_every_section_is_present(self) -> None:
        text = render_config_toml()
        for section in ("[project]", "[build]", "[bib]", "[figures]", "[template]", "[clean]"):
            assert section in text

    def test_keys_are_commented(self) -> None:
        assert text_has_comment_above(render_config_toml(), "engine =")


def text_has_comment_above(text: str, key: str) -> bool:
    """Return ``True`` when the line before *key* is a comment."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(key) and index > 0:
            return lines[index - 1].lstrip().startswith("#")
    return False


class TestLocateTemplate:
    """Finding the template directory."""

    def test_explicit_directory_wins(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        assert locate_template_dir(tmp_path) == tmp_path.resolve()

    def test_environment_variable(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "thesis.tex").write_text("x", encoding="utf-8")
        monkeypatch.setenv("THESIS_TEMPLATE_DIR", str(tmp_path))
        assert locate_template_dir() == tmp_path.resolve()

    def test_falls_back_to_the_source_checkout(self, template_root: Path) -> None:
        assert locate_template_dir() == template_root.resolve()

    def test_missing_template_raises_with_a_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def only_a_missing_directory(explicit: Path | None = None) -> list[Path]:
            del explicit
            return [tmp_path / "nowhere"]

        monkeypatch.setattr(init_module, "template_candidates", only_a_missing_directory)
        with pytest.raises(ProjectLayoutError) as info:
            locate_template_dir()
        assert "THESIS_TEMPLATE_DIR" in (info.value.hint or "")


class TestInitProject:
    """Creating a project from the real template."""

    def test_copies_the_tree(self, tmp_path: Path, template_root: Path) -> None:
        destination = tmp_path / "mythesis"
        report = init_project(destination, template_dir=template_root)
        assert (destination / "thesis.tex").is_file()
        assert (destination / "thesis_report.cls").is_file()
        assert (destination / "config" / "names.tex").is_file()
        assert (destination / "chapters" / "ch1_introduction.tex").is_file()
        assert report.summary["files_written"] > 10

    def test_a_new_project_gets_a_gitignore(self, tmp_path: Path, template_root: Path) -> None:
        # Without it, the first commit of a new project picks up the .aux
        # and .log files along with the sources.
        destination = tmp_path / "mythesis"
        init_project(destination, template_dir=template_root)
        ignore = destination / ".gitignore"
        assert ignore.is_file()
        text = ignore.read_text(encoding="utf-8")
        for pattern in ("*.aux", "*.log", "build/", "*-eps-converted-to.pdf", "*.bb"):
            assert pattern in text, pattern

    def test_the_gitignore_covers_what_clean_deletes(self, template_root: Path) -> None:
        # .ps is the one exception: outside build/ it is a figure.
        lines = set((template_root / ".gitignore").read_text(encoding="utf-8").splitlines())
        for extension in DEFAULT_ARTIFACT_EXTENSIONS:
            if extension != ".ps":
                assert f"*{extension}" in lines, extension
        for pattern in (*CleanConfig().extra_patterns, "*-eps-converted-to.pdf", "build/"):
            assert pattern in lines, pattern
        assert "*.ps" not in lines

    def test_result_is_a_loadable_project(self, tmp_path: Path, template_root: Path) -> None:
        destination = tmp_path / "mythesis"
        init_project(destination, template_dir=template_root)
        config = load_config(destination, required=True)
        assert config.main_path.is_file()
        assert all(path.is_file() for path in config.bib_paths)

    def test_values_are_substituted(self, tmp_path: Path, template_root: Path) -> None:
        destination = tmp_path / "mythesis"
        init_project(
            destination,
            template_dir=template_root,
            values={"cTitle": "我的論文題目", "myCname": "某某"},
        )
        names = (destination / "config" / "names.tex").read_text(encoding="utf-8")
        assert "我的論文題目" in names
        assert "論文中文題目" not in names

    def test_remaining_placeholders_are_reported(self, tmp_path: Path, template_root: Path) -> None:
        report = init_project(tmp_path / "mythesis", template_dir=template_root)
        placeholders = [item for item in report.findings if item.kind == "placeholder"]
        assert placeholders
        # The field names are in .message, not .detail: without -v the
        # terminal output leaves the detail out.
        assert "cTitle" in placeholders[0].message

    def test_existing_files_are_kept_unless_forced(
        self, tmp_path: Path, template_root: Path
    ) -> None:
        destination = tmp_path / "mythesis"
        destination.mkdir()
        (destination / "thesis.tex").write_text("MY OWN WORK", encoding="utf-8")
        report = init_project(destination, template_dir=template_root)
        assert (destination / "thesis.tex").read_text(encoding="utf-8") == "MY OWN WORK"
        assert any(item.kind == "file-kept" for item in report.findings)

        init_project(destination, template_dir=template_root, force=True)
        assert "documentclass" in (destination / "thesis.tex").read_text(encoding="utf-8")

    def test_destination_must_not_be_a_file(self, tmp_path: Path, template_root: Path) -> None:
        target = tmp_path / "afile"
        target.write_text("x", encoding="utf-8")
        with pytest.raises(ProjectLayoutError, match="not a directory"):
            init_project(target, template_dir=template_root)


class TestTemplateCompleteness:
    """The shipped template must declare every field init can fill."""

    def test_every_name_field_has_a_placeholder_default(self, template_root: Path) -> None:
        names = (template_root / "config" / "names.tex").read_text(encoding="utf-8")
        for field in NAME_FIELDS:
            assert f"\\{field.macro}" in names, field.macro


class TestFreshProjectIsClean:
    """A brand-new project must not warn about files it just created.

    ``init`` writes a generated, commented ``thesis.toml``.  If the
    template's own copy were copied first, that file would already exist,
    the generated one would never be written, and the fresh project would
    report a file it had just created as one it declined to overwrite.
    """

    def test_no_file_is_reported_as_kept(self, tmp_path: Path) -> None:
        report = init_project(tmp_path / "fresh")
        assert report.summary["files_kept"] == 0
        assert [finding.kind for finding in report.findings if finding.kind == "file-kept"] == []

    def test_only_warning_free_findings_remain(self, tmp_path: Path) -> None:
        report = init_project(tmp_path / "fresh")
        assert report.error_count == 0
        assert report.warning_count == 0
        assert report.exit_code(strict=True) == 0

    def test_the_generated_config_is_the_one_on_disk(self, tmp_path: Path) -> None:
        destination = tmp_path / "fresh"
        init_project(destination)
        written = (destination / "thesis.toml").read_text(encoding="utf-8")
        assert written == render_config_toml()

    def test_every_other_template_file_is_still_copied(self, tmp_path: Path) -> None:
        destination = tmp_path / "fresh"
        init_project(destination)
        for relative in ("thesis.tex", "config/names.tex", "macros/figure_macros.tex"):
            assert (destination / relative).is_file(), relative
