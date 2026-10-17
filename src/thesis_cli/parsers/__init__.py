"""Parsers for the file formats a LaTeX thesis is made of.

None of these modules runs a program or touches the network.  The parsing
works on strings, which is how the tests drive it, and the same sources
always give the same audit.

===================  ====================================================
Module               Reads
===================  ====================================================
``tex_scanner``      ``.tex`` sources: comments, math, inputs, references
``aux_parser``       ``.aux`` bookkeeping written between LaTeX passes
``bib_parser``       ``.bib`` databases, with a data-quality audit
``cjk``              mixed Chinese/English prose, for word counting
===================  ====================================================
"""

from __future__ import annotations

__all__ = ["aux_parser", "bib_parser", "cjk", "tex_scanner"]
