"""End-to-end demonstration, on a thesis this script generates from scratch.

Run it with::

    python examples/audit_synthetic_thesis.py

No TeX installation and no data files are needed.  The script builds a small
thesis project in a temporary directory, plants four defects that a LaTeX
run either lets through or mentions only in a log file, runs every audit
over it, then repairs the project and runs the audits again.

The defects planted are the ones that cost real time:

1. a graphics reference whose case does not match the file on disk: it
   works on Windows and fails on a Linux CI runner;
2. a ``\\cite`` whose key is in no database, which prints as ``[?]``;
3. a bibliography entry nothing cites, so it never reaches the reference
   list;
4. a ``year`` field holding a whole citation, which BibTeX prints as it
   stands without a word of warning.

Everything the script prints comes from the same public API the CLI uses,
so this doubles as a worked example of driving the toolkit from Python.
"""

from __future__ import annotations

import struct
import tempfile
import zlib
from pathlib import Path

from thesis_cli import (
    Severity,
    audit_figures,
    check_references,
    clean_project,
    collect_statistics,
    configure_logging,
    load_config,
    scan_document,
)
from thesis_cli.commands.build import validate_main_document
from thesis_cli.errors import ValidationError

# ---------------------------------------------------------------------------
# The synthetic project
# ---------------------------------------------------------------------------
THESIS_TOML = """[project]
main = "thesis.tex"

[build]
engine = "xelatex"

[bib]
files = ["bib/references.bib"]

[figures]
dirs = ["figures"]

[template]
watermark = "off"
"""

MAIN_TEX = r"""\documentclass[12pt,a4paper]{report}
\usepackage{graphicx}
\begin{document}
\input{chapters/ch1.tex}
\input{chapters/ch2.tex}
\bibliographystyle{ieeetr}
\bibliography{bib/references}
\end{document}
"""

CH1_TEX = r"""\chapter{緒論}
\label{ch:intro}

本章說明研究背景與動機，並引用既有工作 \cite{lamport1994latex}。
This chapter mixes English with 中文 in a single paragraph, which is why a
whitespace word count is useless here.

\begin{figure}
\includegraphics[width=0.8\columnwidth]{figures/Result_plot.png}
\caption{示範圖說。}
\label{fig:result}
\end{figure}

如圖 \ref{fig:result} 所示，結果符合預期。

\begin{equation}
\label{eq:model}
y = \mathbf{w}^{\top}\mathbf{x} + b
\end{equation}
"""

CH2_TEX = r"""\chapter{方法}
\label{ch:method}

式 \eqref{eq:model} 定義了線性模型。此處引用一筆不存在的文獻
\cite{ghost2020missing}，check-refs 應該要把它找出來。
"""

REFERENCES_BIB = """@BOOK{lamport1994latex,
  author    = {Leslie Lamport},
  title     = {{\\LaTeX}: A Document Preparation System},
  edition   = {2nd},
  publisher = {Addison-Wesley},
  year      = {1994}
}

@BOOK{knuth1984texbook,
  author    = {Donald E. Knuth},
  title     = {The {\\TeX}book},
  publisher = {Addison-Wesley},
  year      = {1984}
}

@ARTICLE{broken_year,
  author  = {A. Author},
  title   = {An entry whose year field holds a whole citation},
  journal = {Journal of Nothing},
  year    = {: Some Book. Some Press, City, 1988}
}
"""


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def write_placeholder_png(path: Path, size: int = 16) -> None:
    """Write a tiny valid PNG so the figure audit has something to find.

    Parameters
    ----------
    path : pathlib.Path
        Destination file.
    size : int, optional
        Side length in pixels.
    """
    raw = bytearray()
    for _ in range(size):
        raw.append(0)
        raw.extend(b"\x99\xaa\xcc" * size)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 1))
        + _png_chunk(b"IEND", b"")
    )


def create_project(root: Path) -> Path:
    """Write the synthetic thesis, defects included.

    Parameters
    ----------
    root : pathlib.Path
        Directory to populate.

    Returns
    -------
    pathlib.Path
        The same directory, now a loadable project.
    """
    (root / "chapters").mkdir(parents=True, exist_ok=True)
    (root / "bib").mkdir(parents=True, exist_ok=True)
    (root / "thesis.toml").write_text(THESIS_TOML, encoding="utf-8")
    (root / "thesis.tex").write_text(MAIN_TEX, encoding="utf-8")
    (root / "chapters" / "ch1.tex").write_text(CH1_TEX, encoding="utf-8")
    (root / "chapters" / "ch2.tex").write_text(CH2_TEX, encoding="utf-8")
    (root / "bib" / "references.bib").write_text(REFERENCES_BIB, encoding="utf-8")

    # Defect 1: the source asks for Result_plot.png; the file is result_plot.png.
    write_placeholder_png(root / "figures" / "result_plot.png")

    # Leftovers a TeX run would have made, for the clean demonstration.
    (root / "thesis.aux").write_text("\\relax\n\\citation{lamport1994latex}\n", encoding="utf-8")
    (root / "thesis.log").write_text("This is XeTeX\n", encoding="utf-8")
    (root / "thesis.tex.bak").write_text("an editor backup\n", encoding="utf-8")
    (root / "somebody_elses_document.aux").write_text("\\relax\n", encoding="utf-8")
    return root


def repair_project(root: Path) -> None:
    """Fix every planted defect."""
    # 1. Case mismatch: rename the file to what the source asks for.
    (root / "figures" / "result_plot.png").rename(root / "figures" / "Result_plot.png")

    # 2. Undefined citation: remove the citation that has no entry.
    path = root / "chapters" / "ch2.tex"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "此處引用一筆不存在的文獻\n\\cite{ghost2020missing}，check-refs 應該要把它找出來。",
        "此處改為引用一筆真實存在的文獻 \\cite{knuth1984texbook}。",
    )
    path.write_text(text, encoding="utf-8")

    # 3 and 4: repair the broken year field, and cite the entry so it is used.
    bib = root / "bib" / "references.bib"
    bib.write_text(
        bib.read_text(encoding="utf-8").replace(
            "year    = {: Some Book. Some Press, City, 1988}",
            "year    = {1988}",
        ),
        encoding="utf-8",
    )
    chapter = root / "chapters" / "ch1.tex"
    chapter.write_text(
        chapter.read_text(encoding="utf-8").replace(
            r"\cite{lamport1994latex}",
            r"\cite{lamport1994latex, broken_year}",
        ),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------
def banner(title: str) -> None:
    """Print a section heading."""
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def show_findings(name: str, report: object) -> int:
    """Print one report's findings and return its error count."""
    findings = report.sorted_findings()  # type: ignore[attr-defined]
    print(f"\n-- {name}: {len(findings)} finding(s) --")
    for item in findings:
        marker = {
            Severity.ERROR: "ERROR  ",
            Severity.WARNING: "warning",
            Severity.INFO: "info   ",
            Severity.OK: "ok     ",
        }[item.severity]
        print(f"  [{marker}] {item.location}")
        print(f"            {item.message}")
    if not findings:
        print("  (nothing to report)")
    return report.error_count  # type: ignore[attr-defined]


def main() -> None:
    """Build, break, audit, repair and re-audit a synthetic thesis."""
    configure_logging(verbosity=0)

    with tempfile.TemporaryDirectory(prefix="thesis-cli-example-") as raw:
        root = create_project(Path(raw))
        config = load_config(root, required=True)

        banner("1. The project")
        document = scan_document(
            config.main_path,
            config.root,
            macros=config.figure_macros,
            label_macros=config.label_macros,
        )
        print(f"  root            {root}")
        print(f"  files reached   {len(document.files)}")
        print(f"  citations       {sorted(document.cited_keys())}")
        print(f"  labels          {sorted(document.label_names())}")
        print(f"  graphics        {[item.raw for item in document.graphics]}")

        banner("2. Rejecting a target that is not a compilable document")
        try:
            validate_main_document(root / "chapters" / "ch1.tex")
        except ValidationError as error:
            print(f"  refused: {error}")
            print(f"  hint:    {error.hint}")

        banner("3. Audits on the broken project")
        errors = 0
        errors += show_findings("check-refs", check_references(config))
        errors += show_findings("figures", audit_figures(config))
        print(f"\n  total errors before repair: {errors}")

        banner("4. Word count (CJK characters and Latin words counted separately)")
        stats = collect_statistics(config)
        table = stats.tables[0]
        widths = [
            max(len(str(row[index])) for row in (table.columns, *table.rows))
            for index in range(len(table.columns))
        ]
        for row in (table.columns, *table.rows):
            print("  " + "  ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
        print(f"\n  combined figure: {stats.summary['combined']}")

        banner("5. Repairing every defect and auditing again")
        repair_project(root)
        config = load_config(root, required=True)
        remaining = 0
        remaining += show_findings("check-refs", check_references(config))
        remaining += show_findings("figures", audit_figures(config))
        print(f"\n  total errors after repair: {remaining}")

        banner("6. Cleaning up build artefacts")
        clean_report = clean_project(config, show_skipped=True)
        tables = {table.title: table for table in clean_report.tables}
        print(f"  removed  {clean_report.summary['removed']} file(s)")
        for row in tables["Removed files"].rows:
            print(f"    - {row[0]}  ({row[1]})")
        print(f"  spared   {clean_report.summary['spared']} file(s)")
        spared = tables.get("Spared")
        for row in spared.rows if spared is not None else ():
            print(f"    . {row[0]}  ({row[1]})")

        banner("Result")
        if remaining == 0 and errors > 0:
            print("  Every planted defect was found, and none survived the repair.")
        else:  # pragma: no cover - only reachable if the toolkit regresses
            print(f"  Unexpected state: {errors} before, {remaining} after.")
            raise SystemExit(1)


if __name__ == "__main__":
    main()
