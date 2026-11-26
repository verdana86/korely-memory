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

import unicodedata

#: What every line of a text after its first starts with.
CONTINUED = "    "

# The bidirectional classes of the characters that change the direction of
# what follows them (UAX #9, Unicode Bidirectional Algorithm, table 4: the
# explicit embeddings, overrides and isolates and their terminators), and of
# the marks with a strong direction that are formats (LRM, RLM, ALM).
_STEERS = frozenset({"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI",
                     "L", "R", "AL"})


def _hidden(ch: str) -> bool:
    """A character a terminal acts on instead of printing, by its Unicode
    properties: a control (category Cc: ESC, which begins an ANSI/CSI
    sequence, BACKSPACE, the C1 controls U+0080 to U+009F) that is not white
    space, or a format character (Cf) that steers the direction of the text
    (U+202E RIGHT-TO-LEFT OVERRIDE and the rest, CVE-2021-42574, "Trojan
    Source"). The joiners (bidirectional class BN) are not: an emoji needs
    them."""
    cat = unicodedata.category(ch)
    if cat == "Cc":
        return not ch.isspace()
    return cat == "Cf" and unicodedata.bidirectional(ch) in _STEERS


def visible(text: str) -> str:
    """`text` with every character `_hidden` says a terminal acts on written
    as its escape (`\\x1b`, `\\u202e`), so what a memory holds is read, never
    run: a memory could clear the screen, move the cursor over a line or
    reverse the order of the letters after it."""
    if not any(_hidden(ch) for ch in text):
        return text
    out = []
    for ch in text:
        if _hidden(ch):
            cp = ord(ch)
            out.append(f"\\x{cp:02x}" if cp < 0x100 else
                       f"\\u{cp:04x}" if cp < 0x10000 else f"\\U{cp:08x}")
        else:
            out.append(ch)
    return "".join(out)


def in_its_line(text: str) -> str:
    """`text` with every line after its first under CONTINUED, and each line
    `visible`. A line break is whatever `str.splitlines` breaks on (LF, CR,
    CR LF, VT, FF, FS, GS, RS, NEL, U+2028, U+2029). A text with neither is
    itself."""
    return ("\n" + CONTINUED).join(visible(line) for line in text.splitlines())


def shown(text: str) -> str:
    """A text printed alone, its lines at the margin as written, each line
    `visible` (`korely get`)."""
    return "\n".join(visible(line) for line in text.splitlines())


def with_its_id(head: str, text: str, item_id: str) -> str:
    """One result: `head`, the text kept in its line, and `  (item_id)` at
    the end of the text's first line. A first line that ends with another
    id is followed by its own on the same line, so the item's id is always
    the last one there."""
    first, _, rest = in_its_line(text).partition("\n")
    return f"{head}{first}  ({item_id})" + (f"\n{rest}" if rest else "")
