"""Generate the copy-and-paste snippets and the placeholder images.

Run it from the project root::

    python make_snippets.py

This is a build helper for the template itself, not part of the installed
package.  The eleven snippet files and the two placeholder PNGs all come
from this file: when a macro's arguments change, I edit the entry here and
rerun the script instead of touching the snippets by hand.

The PNG writer needs only the standard library.  The two placeholders are
about 1 KB and 2.4 KB of zlib-compressed scanlines, and because this script
draws them, anyone can regenerate the PNGs in ``template/figures/`` and
check that they carry no institutional marks.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SNIPPETS: dict[str, str] = {
    "single_figure.tex": r"""% A single floating figure.
% \fig{width}{file}{caption}{label}
%   width is a fraction of the text width (0.8 = 80%).
\fig{0.8}{figures/example_fig}{Caption goes here.}{fig:label-here}
""",
    "single_figure_trim.tex": r"""% A single figure with its margins trimmed away.
% \figtr{width}{file}{caption}{label}{trim}
%   trim is "left bottom right top"; unitless values are big points (bp).
%   Read the values off a PDF viewer's crop tool; the macro clips the trimmed margins.
\figtr{0.8}{figures/example_fig}{Caption goes here.}{fig:label-here}{10 20 10 20}
""",
    "single_figure_w_addtl_text.tex": r"""% A figure with an explanatory paragraph under the caption.
% \figt{width}{file}{caption}{label}{extra text}
\figt{0.8}{figures/example_fig}{Caption goes here.}{fig:label-here}{%
  Additional text below the caption: a legend, a note about units, or the
  parameter values used to produce the plot.}
""",
    "figure_here.tex": r"""% A figure placed exactly where it is written, with no float.
% Use this sparingly: it stops LaTeX from balancing the page.
% \hfig{width}{file}{caption}{label}
\hfig{0.8}{figures/example_fig}{Caption goes here.}{fig:label-here}
""",
    "multi_fig.tex": r"""% Two figures side by side, each with its own caption and label.
% The panels go inside one figure environment; \hfill spreads them.
\begin{figure}[htbp]
  \centering
  \mpfig{0.45}{figures/example_fig}{Left caption.}{fig:left}%
  \hfill
  \mpfig{0.45}{figures/example_fig}{Right caption.}{fig:right}
\end{figure}
""",
    "multi_fig_align.tex": r"""% Side-by-side figures with explicit vertical alignment.
% \mpfiga{width}{alignment}{file}{caption}{label}
%   alignment: b (bottom), c (centre), t (top)
\begin{figure}[htbp]
  \centering
  \mpfiga{0.45}{t}{figures/example_fig}{Top-aligned.}{fig:top}%
  \hfill
  \mpfiga{0.45}{b}{figures/example_fig}{Bottom-aligned.}{fig:bottom}
\end{figure}
""",
    "multi_fig_trim.tex": r"""% Side-by-side figures, both trimmed.
% \mpfigtr{width}{file}{caption}{label}{trim}
\begin{figure}[htbp]
  \centering
  \mpfigtr{0.45}{figures/example_fig}{Left caption.}{fig:left}{10 20 10 20}%
  \hfill
  \mpfigtr{0.45}{figures/example_fig}{Right caption.}{fig:right}{10 20 10 20}
\end{figure}
""",
    "multi_fig_trim_align.tex": r"""% Side-by-side figures, trimmed and explicitly aligned.
% \mpfigtra{width}{alignment}{file}{caption}{label}{trim}
\begin{figure}[htbp]
  \centering
  \mpfigtra{0.45}{t}{figures/example_fig}{Left caption.}{fig:left}{10 20 10 20}%
  \hfill
  \mpfigtra{0.45}{t}{figures/example_fig}{Right caption.}{fig:right}{10 20 10 20}
\end{figure}
""",
    "multi_fig_w_addtl_text.tex": r"""% Side-by-side figures, each with text under its caption.
% \mpfigt{width}{file}{caption}{label}{extra text}
\begin{figure}[htbp]
  \centering
  \mpfigt{0.45}{figures/example_fig}{Left caption.}{fig:left}{Note about the left panel.}%
  \hfill
  \mpfigt{0.45}{figures/example_fig}{Right caption.}{fig:right}{Note about the right panel.}
\end{figure}
""",
    "multi_subfig.tex": r"""% Three panels sharing ONE caption, labelled (a) (b) (c).
% \mpfigabc{width}{file} draws a panel with no caption of its own.
\begin{figure}[htbp]
  \centering
  \mpfigabc{0.3}{figures/example_fig}\hfill
  \mpfigabc{0.3}{figures/example_fig}\hfill
  \mpfigabc{0.3}{figures/example_fig}
  \\[0.5em]
  \makebox[0.3\columnwidth]{(a)}\hfill
  \makebox[0.3\columnwidth]{(b)}\hfill
  \makebox[0.3\columnwidth]{(c)}
  \caption{One caption describing all three panels: (a) first, (b) second,
           (c) third.}
  \label{fig:abc}
\end{figure}
""",
    "table.tex": r"""% A table. The caption goes ABOVE the tabular, which is the usual
% convention for tables and the opposite of the one for figures.
\begin{table}[htbp]
  \centering
  \caption{Caption goes here.}
  \label{tab:label-here}
  \begin{tabular}{lrr}
    \hline
    Condition & Metric A & Metric B \\
    \hline
    Baseline  & 0.00     & 0.00     \\
    Proposed  & 0.00     & 0.00     \\
    \hline
  \end{tabular}
\end{table}
""",
}


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    """Build one PNG chunk: length, tag, payload, CRC."""
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def write_png(path: Path, rows: list[list[tuple[int, int, int]]]) -> None:
    """Write an 8-bit RGB PNG without any third-party dependency.

    Parameters
    ----------
    path : pathlib.Path
        Destination file.
    rows : list of list of tuple
        One list per scanline, each holding ``(r, g, b)`` triples.
    """
    height = len(rows)
    width = len(rows[0])
    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter type 0: None
        for red, green, blue in row:
            raw.extend((red, green, blue))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def example_figure(size: int = 320) -> list[list[tuple[int, int, int]]]:
    """Build a neutral placeholder plot: axes and a diagonal guide line."""
    rows: list[list[tuple[int, int, int]]] = []
    margin = size // 8
    for y in range(size):
        row: list[tuple[int, int, int]] = []
        for x in range(size):
            on_axis = x == margin or y == size - margin - 1
            on_line = (
                abs((size - margin - 1 - y) - (x - margin)) <= 1 and margin <= x < size - margin
            )
            in_grid = (x - margin) % (size // 8) == 0 or (y - margin) % (size // 8) == 0
            if on_axis:
                row.append((40, 40, 40))
            elif on_line:
                row.append((70, 110, 190))
            elif in_grid and margin < x < size - margin and margin < y < size - margin:
                row.append((225, 225, 225))
            else:
                row.append((255, 255, 255))
        rows.append(row)
    return rows


def watermark(size: int = 200) -> list[list[tuple[int, int, int]]]:
    """Build a neutral watermark: a pale ring at 200x200 px, the usual size of a university mark."""
    rows: list[list[tuple[int, int, int]]] = []
    centre = (size - 1) / 2.0
    outer = size * 0.45
    inner = size * 0.34
    for y in range(size):
        row: list[tuple[int, int, int]] = []
        for x in range(size):
            distance = ((x - centre) ** 2 + (y - centre) ** 2) ** 0.5
            if inner <= distance <= outer:
                row.append((205, 205, 205))
            else:
                row.append((255, 255, 255))
        rows.append(row)
    return rows


def main() -> None:
    """Write every snippet and placeholder image under ``template/``."""
    template = Path(__file__).resolve().parent / "template"
    snippet_dir = template / "snippets"
    snippet_dir.mkdir(parents=True, exist_ok=True)
    for name, body in SNIPPETS.items():
        (snippet_dir / name).write_text(body, encoding="utf-8")

    figures = template / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    write_png(figures / "example_fig.png", example_figure())
    write_png(figures / "watermark_placeholder.png", watermark())

    print(f"Wrote {len(SNIPPETS)} snippet(s) to {snippet_dir}")
    print(f"Wrote 2 placeholder image(s) to {figures}")


if __name__ == "__main__":
    main()
