# minimal_thesis

The smallest document I could write that still touches what a Chinese
thesis depends on: a document class, a CJK package chosen from the engine,
Chinese and English mixed in one paragraph, and one numbered equation.

`thesis-cli doctor` compiles this as its smoke test, but it does **not**
read the copy kept here. It writes the same text from `MINIMAL_DOCUMENT` in
`thesis_cli/commands/doctor.py` into a temporary directory, so the check
works from an installed wheel and leaves nothing behind.

The copy in this folder is for reading and compiling by hand. A test
asserts that it matches `MINIMAL_DOCUMENT`; after changing the constant,
regenerate the file with `write_smoke_document()`.
