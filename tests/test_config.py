"""Tests for ``thesis.toml`` loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from thesis_cli.config import (
    DEFAULT_FIGURE_MACROS,
    DEFAULT_LABEL_MACROS,
    Engine,
    ThesisConfig,
    TocStyle,
    WatermarkMode,
    find_config_file,
    load_config,
    resolve_in_root,
)
from thesis_cli.errors import ConfigError


class TestDefaults:
    """The built-in configuration."""

    def test_default_engine_and_style(self, tmp_path: Path) -> None:
        config = ThesisConfig.default(tmp_path)
        assert config.build.engine is Engine.XELATEX
        assert config.template.toc_style is TocStyle.CJK_FULL
        assert config.template.watermark is WatermarkMode.ALL

    def test_derived_paths_are_absolute_and_inside_the_root(self, tmp_path: Path) -> None:
        config = ThesisConfig.default(tmp_path)
        assert config.main_path.is_absolute()
        assert config.main_path.is_relative_to(tmp_path)
        assert config.output_dir.is_relative_to(tmp_path)

    def test_macro_tables_merge_defaults(self, tmp_path: Path) -> None:
        config = ThesisConfig.default(tmp_path)
        assert config.figure_macros == dict(DEFAULT_FIGURE_MACROS)
        assert config.label_macros == dict(DEFAULT_LABEL_MACROS)

    def test_engine_properties(self) -> None:
        assert Engine.XELATEX.is_unicode_engine is True
        assert Engine.PDFLATEX.is_unicode_engine is False
        assert Engine.LATEX_DVIPDFMX.tex_program == "latex"

    def test_required_packages_depend_on_the_engine(self, tmp_path: Path) -> None:
        unicode_engine = ThesisConfig.default(tmp_path)
        assert "xeCJK" in unicode_engine.required_packages
        assert "CJKutf8" not in unicode_engine.required_packages

        pdflatex = unicode_engine.with_overrides(
            build=type(unicode_engine.build)(engine=Engine.PDFLATEX)
        )
        assert "CJKutf8" in pdflatex.required_packages
        assert "xeCJK" not in pdflatex.required_packages

    def test_toc_style_macro_names(self) -> None:
        assert TocStyle.ARABIC.macro == "ThesisTocStyleArabic"
        assert TocStyle.CJK.macro == "ThesisTocStyleCJK"
        assert TocStyle.CJK_FULL.macro == "ThesisTocStyleCJKFull"


class TestPathResolution:
    """Absolute and escaping paths are refused."""

    def test_relative_path_resolves(self, tmp_path: Path) -> None:
        assert resolve_in_root(tmp_path, "figures", what="x") == (tmp_path / "figures").resolve()

    def test_absolute_path_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="relative to the project root"):
            resolve_in_root(tmp_path, str(tmp_path / "figures"), what="figures.dirs")

    def test_escaping_path_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="outside the project root"):
            resolve_in_root(tmp_path, "../elsewhere", what="figures.dirs")

    def test_empty_path_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="must not be empty"):
            resolve_in_root(tmp_path, "   ", what="main")


class TestLoading:
    """Reading the file from disk."""

    def test_loads_the_synthetic_project(self, project: Path) -> None:
        config = load_config(project, required=True)
        assert config.root == project.resolve()
        assert config.main == "thesis.tex"
        assert config.template.watermark is WatermarkMode.OFF
        assert config.source_file is not None

    def test_search_walks_up_to_the_parent(self, project: Path) -> None:
        found = find_config_file(project / "chapters")
        assert found == project / "thesis.toml"

    def test_missing_file_falls_back_to_defaults(self, tmp_path: Path) -> None:
        config = load_config(tmp_path)
        assert config.source_file is None
        assert config.build.engine is Engine.XELATEX

    def test_missing_file_can_be_required(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match=r"No thesis\.toml found"):
            load_config(tmp_path, required=True)

    def test_unknown_top_level_section_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text("[nonsense]\nx = 1\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="Unknown key"):
            load_config(tmp_path, required=True)

    def test_unknown_key_inside_a_section_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text('[build]\nengin = "xelatex"\n', encoding="utf-8")
        with pytest.raises(ConfigError, match=r"Unknown key\(s\) in \[build\]"):
            load_config(tmp_path, required=True)

    def test_unknown_engine_lists_the_valid_values(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text('[build]\nengine = "tex4ht"\n', encoding="utf-8")
        with pytest.raises(ConfigError) as info:
            load_config(tmp_path, required=True)
        assert "xelatex" in (info.value.hint or "")

    def test_lualatex_is_not_an_engine_choice(self, tmp_path: Path) -> None:
        # config/cjk.tex sets up Chinese with xeCJK, which stops under LuaTeX.
        (tmp_path / "thesis.toml").write_text('[build]\nengine = "lualatex"\n', encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(tmp_path, required=True)

    def test_wrong_type_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text('[build]\nmax_passes = "four"\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="must be an integer"):
            load_config(tmp_path, required=True)

    def test_out_of_range_value_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text("[build]\nmax_passes = 0\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="must be >= 1"):
            load_config(tmp_path, required=True)

    def test_malformed_toml_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text("[build\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="not valid TOML"):
            load_config(tmp_path, required=True)

    def test_absolute_path_in_the_file_is_refused(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text('[figures]\ndirs = ["/etc"]\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="relative to the project root"):
            load_config(tmp_path, required=True)

    def test_extensions_are_normalised(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text(
            '[figures]\nextensions = ["PNG", ".Jpg"]\n', encoding="utf-8"
        )
        config = load_config(tmp_path, required=True)
        assert config.figures.extensions == (".png", ".jpg")

    def test_custom_macro_tables_are_merged(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text(
            "[figures.macros]\nmyfig = 3\n\n[figures.label_macros]\nmyfig = 5\n",
            encoding="utf-8",
        )
        config = load_config(tmp_path, required=True)
        assert config.figure_macros["myfig"] == 3
        assert config.figure_macros["includegraphics"] == 1
        assert config.label_macros["myfig"] == 5

    def test_macro_position_must_be_a_positive_integer(self, tmp_path: Path) -> None:
        (tmp_path / "thesis.toml").write_text("[figures.macros]\nmyfig = 0\n", encoding="utf-8")
        with pytest.raises(ConfigError, match="1-based argument index"):
            load_config(tmp_path, required=True)


class TestImmutability:
    """The configuration objects are frozen."""

    def test_cannot_be_mutated(self, tmp_path: Path) -> None:
        config = ThesisConfig.default(tmp_path)
        with pytest.raises((AttributeError, TypeError)):
            config.main = "other.tex"  # type: ignore[misc]

    def test_with_overrides_returns_a_new_object(self, tmp_path: Path) -> None:
        config = ThesisConfig.default(tmp_path)
        other = config.with_overrides(main="other.tex")
        assert config.main == "thesis.tex"
        assert other.main == "other.tex"
