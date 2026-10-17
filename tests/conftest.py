"""Shared fixtures.

Every fixture builds its project from the strings in this file.  No test
reads a real thesis, bibliography or image, so the suite behaves the same on
every machine and a failure message cannot carry anybody's data.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from thesis_cli.config import ThesisConfig, load_config

MAIN_TEX = r"""\documentclass[12pt,a4paper]{report}
\usepackage{graphicx}
\begin{document}
\input{chapters/ch1.tex}
\input{chapters/ch2.tex}
\bibliographystyle{ieeetr}
\bibliography{bib/refs}
\end{document}
"""

CH1_TEX = r"""\chapter{緒論}
\label{ch:intro}

本章說明研究背景，引用文獻 \cite{alpha} 與 \cite{beta}。
This paragraph mixes English with 中文 on purpose.

% a comment mentioning \cite{ghost} and \includegraphics{ghost.png}
50\% of the text is prose.

\begin{figure}
\includegraphics[width=0.8\columnwidth]{figures/plot.png}
\caption{A caption with \textbf{bold} and {nested} braces}
\label{fig:plot}
\end{figure}

See figure \ref{fig:plot} and section \ref{sec:method}.

\begin{equation}
\label{eq:one}
E = mc^{2}
\end{equation}

\begin{lstlisting}
this % is not a comment and \cite{ignored} is not a citation
\end{lstlisting}
"""

CH2_TEX = r"""\chapter{方法}
\section{做法}
\label{sec:method}

式 \eqref{eq:one} 已在前一章定義。
\fig{0.6}{figures/diagram}{A figure made by a macro}{fig:diagram}

\begin{table}
\caption{A table}
\label{tab:one}
\begin{tabular}{ll} a & b \\ \end{tabular}
\end{table}

再引用一次 \cite{alpha}，並參照表 \ref{tab:one} 與圖 \ref{fig:diagram}。
"""

REFS_BIB = """@ARTICLE{alpha,
  author  = {A. Author and B. Second},
  title   = {A Title},
  journal = {A Journal},
  year    = {2001}
}

@INPROCEEDINGS{beta,
  author    = {C. Third},
  title     = {Another Title},
  booktitle = {Proceedings of Something},
  year      = {2005}
}
"""

THESIS_TOML = """[project]
main = "thesis.tex"

[build]
engine = "xelatex"
output_dir = "build"

[bib]
files = ["bib/refs.bib"]

[figures]
dirs = ["figures"]

[template]
watermark = "off"

[stats]
chapter_dir = "chapters"
content_dir = "content"
"""


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def write_tiny_png(path: Path, *, width: int = 4, height: int = 4) -> Path:
    """Write a minimal valid PNG.

    Parameters
    ----------
    path : pathlib.Path
        Destination.
    width, height : int, optional
        Image size in pixels.

    Returns
    -------
    pathlib.Path
        The file that was written.
    """
    raw = bytearray()
    for _ in range(height):
        raw.append(0)
        raw.extend(b"\xff\xff\xff" * width)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 1))
        + _png_chunk(b"IEND", b"")
    )
    return path


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Create a small but complete synthetic thesis project.

    Returns
    -------
    pathlib.Path
        The project root, containing ``thesis.toml``, a two-chapter
        document, one bibliography and two graphics.
    """
    (tmp_path / "chapters").mkdir()
    (tmp_path / "bib").mkdir()
    (tmp_path / "figures").mkdir()
    (tmp_path / "thesis.tex").write_text(MAIN_TEX, encoding="utf-8")
    (tmp_path / "chapters" / "ch1.tex").write_text(CH1_TEX, encoding="utf-8")
    (tmp_path / "chapters" / "ch2.tex").write_text(CH2_TEX, encoding="utf-8")
    (tmp_path / "bib" / "refs.bib").write_text(REFS_BIB, encoding="utf-8")
    (tmp_path / "thesis.toml").write_text(THESIS_TOML, encoding="utf-8")
    write_tiny_png(tmp_path / "figures" / "plot.png")
    write_tiny_png(tmp_path / "figures" / "diagram.png")
    return tmp_path


@pytest.fixture()
def config(project: Path) -> ThesisConfig:
    """Load the configuration of the synthetic project."""
    return load_config(project, required=True)


@pytest.fixture()
def template_root() -> Path:
    """Path to the LaTeX template shipped with this repository.

    Skips the test when the template is not present, so the suite still
    runs against an installed wheel that carries only the Python package.
    """
    candidate = Path(__file__).resolve().parents[1] / "template"
    if not (candidate / "thesis.tex").is_file():  # pragma: no cover - source checkout
        pytest.skip("template directory is not available")
    return candidate
