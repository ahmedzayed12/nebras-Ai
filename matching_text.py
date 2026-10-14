"""Whitespace-insensitive matching with exact offsets back to the source.

The original document is retained for quality findings, rendering and reports.
Only horizontal spacing is folded; words, punctuation and line breaks remain.
"""
import re

HSPACE = re.compile(r"[ \t\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]+")


def matching_text(text: str) -> str:
    return HSPACE.sub(" ", text)


def matching_text_map(text: str):
    chars, positions = [], []
    cursor = 0
    for match in HSPACE.finditer(text):
        chars.append(text[cursor:match.start()])
        positions.extend(range(cursor, match.start()))
        chars.append(" ")
        positions.append(match.start())
        cursor = match.end()
    chars.append(text[cursor:])
    positions.extend(range(cursor, len(text)))
    positions.append(len(text))
    return "".join(chars), positions
