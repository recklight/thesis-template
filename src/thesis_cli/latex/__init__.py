"""The parts of the toolkit that know about the TeX toolchain itself.

===============  ========================================================
Module           Responsibility
===============  ========================================================
``engine``       Locating and invoking latexmk, the engines and bibtex
``log_parser``   Turning a ``.log`` file into classified messages
``artifacts``    Deciding which files a build produced, and removing them
===============  ========================================================
"""

from __future__ import annotations

__all__ = ["artifacts", "engine", "log_parser"]
