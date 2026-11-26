"""One item a line: how the CLI and the stdio MCP server print a text that
anyone who can write a memory may have put line breaks in.

The rule is the one of Korely's server (its MCP tools and the context block):
every line of a text after its first begins with CONTINUED, so no line of a
text is at the margin, where an item begins, and the item's id is at the end
of its first line. A text can then never print a line that reads as another
result: "it would be simple to craft a string that contains our delimiters"
(Hines et al. 2024, "Defending Against Indirect Prompt Injection Attacks With
Spotlighting", arXiv:2403.14720, section III-B); the mark goes on the data
itself (datamarking, section III-C).
"""
from __future__ import annotations

#: What every line of a text after its first starts with.
CONTINUED = "    "


def in_its_line(text: str) -> str:
    """`text` with every line after its first under CONTINUED. A line break
    is whatever `str.splitlines` breaks on (LF, CR, CR LF, VT, FF, FS, GS,
    RS, NEL, U+2028, U+2029). A text with none is itself."""
    return ("\n" + CONTINUED).join(text.splitlines())


def with_its_id(head: str, text: str, item_id: str) -> str:
    """One result: `head`, the text kept in its line, and `  (item_id)` at
    the end of the text's first line. A first line that ends with another
    id is followed by its own on the same line, so the item's id is always
    the last one there."""
    first, _, rest = in_its_line(text).partition("\n")
    return f"{head}{first}  ({item_id})" + (f"\n{rest}" if rest else "")
