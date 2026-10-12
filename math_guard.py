# -*- coding: utf-8 -*-
"""Math guard: keeps equations from fooling the AI Text Inspector engine.

WHY THIS EXISTS
  The v2.1 engine only understands prose. LaTeX / equation text inflates the "human" evidence:
    * tokens such as \\frac, \\partial, x_{i}, $$ ... $$ are read as typos, double spaces,
      unbalanced brackets, "space before punctuation", lower-case sentence starts ...
    * equation lines create fake sentence/paragraph length variance (looks "bursty" = human);
    * Word equations typed with a Cambria-Math / Courier font look like "format inconsistency";
    * a paragraph holding only an OMML equation becomes an empty paragraph (fake manual spacing).
  Measured on the same manuscript: 44 / 100 without equations  ->  5 / 100 with equations.

WHAT IT DOES
  It removes mathematical material from the text BEFORE the unchanged engine sees it, and keeps an
  exact offset map so the PDF report can still shade the original paragraphs.

  * display math  ($$..$$, \\[..\\], \\begin{equation|align|...}, math-only lines)  -> paragraph dropped
  * inline math   ($..$, \\(..\\), bare LaTeX commands, plain-text formulas)         -> removed, spaces fixed
  * Word OMML     (<m:oMath>, <m:oMathPara>)                                         -> same rules

The engine file is never modified (its SHA-256 guard stays valid).
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

def _cls(*ranges) -> str:
    """Regex character class from (first, last) code points. Built from numbers on purpose: escape sequences written
    in source are easy to get wrong (see _MATH_UNI)."""
    parts = []
    for r in ranges:
        a, b = (r, r) if isinstance(r, int) else r
        parts.append(re.escape(chr(a)) if a == b else re.escape(chr(a)) + "-" + re.escape(chr(b)))
    return "[" + "".join(parts) + "]"


# ----------------------------------------------------------------------------- patterns
_ENVS = (r"equation|equation\*|align|align\*|alignat|alignat\*|aligned|gather|gather\*|multline|multline\*|"
         r"eqnarray|eqnarray\*|displaymath|math|split|cases|array|matrix|pmatrix|bmatrix|vmatrix|Vmatrix|"
         r"smallmatrix|flalign|flalign\*")

# Order matters: longest / most specific first.
_DISPLAY_RX = [
    re.compile(r"\$\$.+?\$\$", re.S),
    re.compile(r"\\\[.+?\\\]", re.S),
    re.compile(r"\\begin\{(%s)\}.+?\\end\{\1\}" % _ENVS, re.S),
]
_INLINE_RX = [
    re.compile(r"\\\(.+?\\\)", re.S),
    # $...$ — not currency: no digit-space-digit pattern, content must contain a math cue or be short.
    re.compile(r"(?<![\\$\w])\$(?!\s)([^$\n]{1,300}?)(?<!\s)\$(?![\w$])"),
]
# Residual LaTeX control sequences with arguments, e.g. \frac{a}{b}, \mathbf{A}, \sqrt[3]{x}
_CMD_ARGS = re.compile(r"\\[A-Za-z]+\*?(?:\s*\[[^\]\n]*\])?(?:\s*\{[^{}\n]*(?:\{[^{}\n]*\}[^{}\n]*)*\})+")
_CMD_BARE = re.compile(r"\\(?:[A-Za-z]{2,}|[,;:!{}|#%&_^ ])")
_SUPSUB = re.compile(r"(?<=[A-Za-z0-9)\]}])[_^](?:\{[^{}\n]*\}|[A-Za-z0-9+\-]{1,6})")

_MATH_CHARS = set("=+−-×÷·*/^_<>≤≥≠≈≡∑∏∫∂∇√∞∈∉⊂⊆∪∩∧∨¬→←↔⇒⇔∀∃±∓°′″|{}[]()\\$%")
_MATH_UNI = re.compile(_cls((0x2200, 0x22FF), (0x2A00, 0x2AFF), (0x27C0, 0x27EF), (0x2190, 0x21FF), (0x0391, 0x03C9),
                            (0x1D400, 0x1D7FF)) [:-1] + re.escape("∑∏∫∂∇√∞") + "]")
# FIX: the old pattern was written as a raw string containing a five-hex-digit escape for U+1D400. Python reads only
# four hex digits, so the class silently became "U+1D40, then the range '0' .. U+1D7F, then 'F'": every ASCII digit and
# letter counted as a 'math symbol' in is_display_math_line. The class is now built from code points.
_WORDISH = re.compile(r"[^\W\d_]{3,}", re.U)           # a real word: >= 3 letters
_EQ_NUMBER = re.compile(r"\s*[\(\[]\s*\d+(?:\.\d+)*[a-z]?\s*[\)\]]\s*$")
_PLAIN_FORMULA = re.compile(
    r"(?<![\w])[A-Za-z]\w{0,2}(?:_\{?[A-Za-z0-9,+\-]+\}?|\^\{?[A-Za-z0-9+\-]+\}?)"
    r"(?:\s*(?:=|≈|≠|≤|≥|<|>)\s*[-+(]?[A-Za-z0-9\\.]+(?:\s*[-+*/^×·]\s*[A-Za-z0-9\\.()_{}]+)*)?")


# Formulas typed as ordinary Unicode text (no LaTeX, no Word equation object), e.g.
#   g<sub>i</sub>(w<sub>i</sub>,J<sub>i</sub> df<sub>i</sub>)=0,  F=(f1^h)^2(f2^v)^2,  a,b IN R,  E^H,E^V,  n >= 4,  W=0
# written with sub/superscript characters, Greek letters and math operators. Common in manuscripts written with
# Unicode math input or pasted from a chat window. Before this rule every "w_i,J_i" counted as "no space after comma"
# and every "a(n_1-1)F" as "word glued to a bracket": fake human typing slips.
_STRONG_MATH = re.compile(_cls(
    (0x2080, 0x209C), (0x1D62, 0x1D6A),                                   # subscript digits / letters
    (0x02B0, 0x02B8), (0x02E0, 0x02E4), (0x1D2C, 0x1D61), (0x1D9B, 0x1DBF),  # modifier (superscript) letters
    0x2071, 0x207F, 0x2C7C, 0x2C7D,
    (0x2200, 0x22FF), (0x2A00, 0x2AFF), (0x27C0, 0x27EF), (0x2190, 0x21FF), (0x2100, 0x214F),  # operators, arrows, R N Z
    0x2212, (0x1D400, 0x1D7FF)))                                          # minus sign, math alphanumerics
_SUP_DIGIT = re.compile(_cls(0x00B2, 0x00B3, 0x00B9, 0x2070, (0x2074, 0x207E)))
_GREEK_CLS = _cls((0x0370, 0x03FF), (0x1F00, 0x1FFF))
_GREEK = re.compile(_GREEK_CLS)
_ASCII_REL = re.compile(r"[A-Za-z0-9)\]][=<>]|[=<>][A-Za-z0-9(\[\-]")
_UNIT_TOKEN = re.compile(r"^[(\[]?(?:[kcmnd]?m|" + _cls(0x00B5, 0x03BC)[:-1] + r"]m|s|g|kg|mg|m?[lL]|ha|ft|in|mol|K|h|min)"
                         + _cls(0x00B2, 0x00B3, 0x00B9, 0x2070, (0x2074, 0x207E), 0x00B7, 0x2F, (0x41, 0x5A), (0x61, 0x7A))
                         + r"*[)\]]?[.,;:]?$")
_MICRO_UNIT = re.compile("^" + _cls(0x00B5, 0x03BC) + r"(?:g|m|M|L|l|mol|s|W|A|V)\b")
_STAT_TOKEN = re.compile(r"^\(?[pPnNrRtFz]\s*[=<>]\s*[-\d.]+\)?[.,;:]?$")
_TERM_GREEK = re.compile(r"(?:[A-Za-z0-9]-" + _GREEK_CLS + "|" + _GREEK_CLS + r"-[A-Za-z0-9])")   # TNF-alpha, beta-actin
_GREEK_WORD = re.compile("^" + _GREEK_CLS + "{4,}$")
_EDGE_PUNCT = ".,;:!?\"'" + chr(0x201C) + chr(0x201D) + chr(0x2018) + chr(0x2019) + chr(0x00AB) + chr(0x00BB)
_CLOSE_Q = ".,;:!?\"'" + chr(0x201D) + chr(0x2019) + chr(0x00BB)
_OPEN_Q = "\"'" + chr(0x201C) + chr(0x2018) + chr(0x00AB)
_PAIR = {")": "(", "]": "[", "}": "{", "(": ")", "[": "]", "{": "}"}


def _is_unicode_math_token(tok: str) -> bool:
    if "://" in tok or "www." in tok or "@" in tok:
        return False
    core = tok.strip(_EDGE_PUNCT)
    if not core:
        return False
    if _STRONG_MATH.search(core):
        return True
    if _ASCII_REL.search(core):
        return not _STAT_TOKEN.match(core)                 # "p<0.05" / "n=10" stay prose
    if _SUP_DIGIT.search(core):
        return not _UNIT_TOKEN.match(core)                 # "m2" with a superscript is a unit, "x2" is maths
    if _GREEK.search(core):
        latin = len(re.findall(r"[A-Za-z]", core))
        term = _TERM_GREEK.search(core) or ("-" in core and latin >= 2)       # TNF-alpha, IL-1beta, beta-actin
        return not (term or _GREEK_WORD.match(core) or _MICRO_UNIT.match(core))
    return False


def _unicode_math_spans(text: str) -> List[Tuple[int, int]]:
    """Spans of Unicode formulas. Neighbouring formula tokens ("R1=a f1^2 G1") fuse into one span; sentence
    punctuation and unmatched brackets at the edges stay in the prose."""
    out: List[List[int]] = []
    prev_math = False
    for m in re.finditer(r"\S+", text):
        a, b = m.start(), m.end()
        if not _is_unicode_math_token(text[a:b]):
            prev_math = False
            continue
        if prev_math and out:
            out[-1][1] = b
        else:
            out.append([a, b])
        prev_math = True
    res = []
    for a, b in out:
        changed = True
        while changed and b > a:
            changed = False
            c = text[b - 1]
            if c in _CLOSE_Q:
                b -= 1; changed = True
            elif c in ")]}" and text[a:b].count(c) > text[a:b].count(_PAIR[c]):
                b -= 1; changed = True
        changed = True
        while changed and a < b:
            changed = False
            c = text[a]
            if c in _OPEN_Q:
                a += 1; changed = True
            elif c in "([{" and text[a:b].count(c) > text[a:b].count(_PAIR[c]):
                a += 1; changed = True
        if b > a:
            res.append((a, b))
    return res


PLACEHOLDER = chr(0x00A7)   # the section sign stands for ONE formula: to the engine it is not a word, not an error,
                            # not a capital, not a number and not one of its punctuation marks.


# ----------------------------------------------------------------------------- data
@dataclass
class MathStats:
    display: int = 0           # display-math paragraphs removed
    inline: int = 0            # inline spans removed
    chars_removed: int = 0
    chars_total: int = 0
    omml: int = 0              # Word equation objects found

    @property
    def found(self) -> int:
        return self.display + self.inline + self.omml

    @property
    def share(self) -> float:
        return self.chars_removed / self.chars_total if self.chars_total else 0.0

    def merge(self, other: "MathStats") -> None:
        self.display += other.display
        self.inline += other.inline
        self.chars_removed += other.chars_removed
        self.chars_total += other.chars_total
        self.omml += other.omml


@dataclass
class MathMap:
    """Links the sanitised paragraphs the engine saw to the ORIGINAL paragraphs."""
    orig_texts: List[str] = field(default_factory=list)
    orig_index: List[int] = field(default_factory=list)       # sanitised i -> original index
    pos_maps: List[Optional[List[int]]] = field(default_factory=list)   # sanitised i -> new pos -> old pos
    stats: MathStats = field(default_factory=MathStats)

    @property
    def active(self) -> bool:
        return self.stats.found > 0

    def to_original(self, i: int, a: int, b: int) -> Tuple[int, int, int]:
        """(sanitised para, start, end) -> (original para, start, end)."""
        pm = self.pos_maps[i]
        j = self.orig_index[i]
        if pm is None:
            return j, a, b
        a2 = pm[min(a, len(pm) - 1)]
        b2 = pm[min(max(b, 1) - 1, len(pm) - 1)] + 1 if b > a else a2
        return j, a2, max(b2, a2)


# ----------------------------------------------------------------------------- core helpers
def _spans(text: str) -> List[Tuple[int, int]]:
    """Character spans of inline math / LaTeX debris, merged and sorted."""
    found: List[Tuple[int, int]] = []
    for rx in _INLINE_RX:
        for m in rx.finditer(text):
            if rx is _INLINE_RX[1]:
                inner = m.group(1)
                # currency guard: "$5 and $10" style -> skip when inner is plain words/numbers only
                if not re.search(r"[\\^_{}=<>+\-*/]|[A-Za-z]\s*\d|\d\s*[A-Za-z]|[\u0370-\u03FF]", inner) \
                        and len(_WORDISH.findall(inner)) >= 2:
                    continue
                if re.fullmatch(r"[\d.,\s]+", inner):
                    continue
            found.append((m.start(), m.end()))
    for rx in (_CMD_ARGS, _CMD_BARE, _SUPSUB, _PLAIN_FORMULA):
        for m in rx.finditer(text):
            found.append((m.start(), m.end()))
    found.extend(_unicode_math_spans(text))
    if not found:
        return []
    found.sort()
    merged = [list(found[0])]
    for a, b in found[1:]:
        if a <= merged[-1][1] + 1:      # touching spans (e.g. "$a$,$b$") fuse
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _real_words(s: str) -> int:
    """Words left after deleting every math span (LaTeX commands are not words)."""
    t = s
    for a, b in reversed(_spans(t)):
        t = t[:a] + " " + t[b:]
    t = re.sub(r"\\[A-Za-z]+", " ", t)
    return len(_WORDISH.findall(t))


def is_display_math_line(line: str) -> bool:
    """True when a whole paragraph is a formula (so it carries no authorship signal)."""
    s = line.strip()
    if not s:
        return False
    core = _EQ_NUMBER.sub("", s).strip()
    for rx in _DISPLAY_RX:
        if rx.fullmatch(core):
            return True
    if core in ("$$", r"\[", r"\]") or re.fullmatch(r"\\(?:begin|end)\{(?:%s)\}.*" % _ENVS, core, re.S):
        return True
    if len(core) > 400 or _real_words(core) >= 3:      # three real words = a sentence, never "just a formula"
        return False
    stripped = re.sub(r"\s", "", core)
    sym = sum(1 for c in stripped if c in _MATH_CHARS or _MATH_UNI.match(c))
    cues = re.search(r"[=<>≤≥≈≡∑∏∫∂∈→⇒]|\\[A-Za-z]+|[_^]\{?", core)
    return bool(stripped and cues and (sym / len(stripped) >= 0.2 or "\\" in core))


def _tidy(text: str, cuts: Sequence[Tuple[int, int]]) -> Tuple[str, List[int]]:
    """Delete `cuts`, repair the spacing they leave and return (new_text, new_pos -> old_pos)."""
    dead = bytearray(len(text))
    for a, b in cuts:
        for k in range(a, b):
            dead[k] = 1
    res: List[Tuple[str, int]] = []
    for i, c in enumerate(text):
        if dead[i]:
            continue
        gap = bool(res) and res[-1][1] + 1 != i            # something was cut right before this char
        if c.isspace():
            if not res or res[-1][0].isspace():
                continue                                    # no leading space / no double space
        elif c in ",.;:!?؟،؛)]}" and res and res[-1][0].isspace() and gap:
            res.pop()                                       # "of  , then" -> "of, then"
        res.append((c, i))
    while res and res[-1][0].isspace():
        res.pop()
    chars = [c for c, _ in res]
    # a sentence that began with maths now starts lower-case: restore the capital the author wrote
    prev_cut = bool(cuts) and cuts[0][0] == 0
    for k, (c, i) in enumerate(res):
        if not c.isalpha():
            if c.isspace() or c in "\"'“«([":
                continue
            break
        before = "".join(chars[:k]).rstrip()
        gap = k > 0 and res[k - 1][1] + 1 != i
        if c.isascii() and c.islower() and ((k == 0 and prev_cut) or (gap and (not before or before[-1] in ".!?؟"))):
            chars[k] = c.upper()
        break
    pos = [i for _, i in res]
    pos.append((pos[-1] + 1) if pos else len(text))
    return "".join(chars), pos


def _rewrite(text: str, cuts: Sequence[Tuple[int, int]], inserts: Sequence[int] = ()) -> Tuple[str, List[int]]:
    """Replace every cut span by ONE placeholder and put a placeholder at every insert point (a Word equation object,
    which has no characters at all in the engine text). Nothing else is touched: the author's own spaces and punctuation
    stay exactly as written, so a genuine double space is still seen and no artificial ",," / " ." / "by  yields" is made.

    WHY NOT DELETE (the old _tidy): deleting N1 and N2 from "Let N1, N2 be" left "Let,, be"; deleting a Word equation
    from "Multiplication by <eq> yields" left a double space; "For <eq>, let" became "For , let". The engine counted
    each such hole as a human typing slip, and err_density is its strongest human-side feature (weight -2.4).
    _tidy also collapsed EVERY double space of a paragraph that contained maths, erasing genuine slips."""
    marks = sorted({min(max(k, 0), len(text)) for k in inserts if not any(a < k < b for a, b in cuts)})
    events = sorted([(a, b) for a, b in cuts] + [(k, k) for k in marks])
    out: List[str] = []
    pos: List[int] = []
    i = 0
    for a, b in events:
        if a < i:                                           # already covered by a previous span
            continue
        for k in range(i, a):
            out.append(text[k]); pos.append(k)
        if not (out and out[-1] == PLACEHOLDER and pos and a == i):   # adjacent formulas -> one placeholder
            out.append(PLACEHOLDER)
            pos.append(min(a, len(text) - 1) if text else 0)
        i = max(i, b)
    for k in range(i, len(text)):
        out.append(text[k]); pos.append(k)
    pos.append((pos[-1] + 1) if pos else len(text))
    return "".join(out), pos


def sanitize_paragraph(text: str, extra_cuts: Sequence[Tuple[int, int]] = (),
                       inserts: Sequence[int] = ()) -> Tuple[Optional[str], Optional[List[int]], MathStats]:
    """Returns (clean_text | None if the paragraph must be dropped, new->old map | None, stats).

    extra_cuts: spans known to be maths from elsewhere (runs set in Cambria Math ...).
    inserts:    offsets where a Word equation object sits between the engine's text runs."""
    st = MathStats(chars_total=len(text))
    if not text.strip():
        return text, None, st
    if is_display_math_line(text):
        st.display, st.chars_removed = 1, len(text)
        return None, None, st
    cuts: List[Tuple[int, int]] = []
    for rx in _DISPLAY_RX:                                  # display math embedded inside prose
        for m in rx.finditer(text):
            cuts.append((m.start(), m.end()))
            st.display += 1
    for a, b in list(_spans(text)) + [tuple(x) for x in extra_cuts]:
        if not any(ca <= a and b <= cb for ca, cb in cuts):
            cuts.append((a, b))
            st.inline += 1
    if not cuts and not inserts:
        return text, None, st
    cuts.sort()
    merged: List[List[int]] = []
    for a, b in cuts:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    cuts = [(a, b) for a, b in merged]
    st.chars_removed = sum(b - a for a, b in cuts)
    new, pmap = _rewrite(text, cuts, inserts)
    if len(_WORDISH.findall(new)) < 3:                      # only a stub is left: the paragraph was maths
        return None, None, MathStats(display=1, chars_removed=len(text), chars_total=len(text))
    return new, pmap, st


_TERMINAL_CH = set(".!?:;,-" + chr(0x061F) + chr(0x2026) + chr(0x060C) + chr(0x2013) + chr(0x2014) + PLACEHOLDER)
_TRAIL_CLOSE = set("\"')]" + chr(0x201D) + chr(0x2019) + chr(0x00BB))


def ends_open(text: str) -> bool:
    """A prose paragraph that stops mid-sentence ("... such that") because the sentence continues in a display
    equation on the next line."""
    t = text.rstrip()
    if not t:
        return False
    k = len(t) - 1
    while k > 0 and t[k] in _TRAIL_CLOSE:
        k -= 1
    return t[k] not in _TERMINAL_CH and (t[-1].isalnum() or t[-1] in ")]}")


def close_before_display(text: str, pmap: Optional[List[int]]) -> Tuple[str, List[int]]:
    """Append the placeholder that stands for the display equation completing the sentence. Without it the engine
    reported 'paragraph without final punctuation' (no_final_punct) for every "... we obtain" / "... such that"
    paragraph that introduces a displayed formula."""
    base = pmap if pmap is not None else list(range(len(text))) + [len(text)]
    body = base[:len(text)]
    last = body[-1] if body else 0
    return text + " " + PLACEHOLDER, body + [last, last, last + 1]


# ----------------------------------------------------------------------------- text-level API
_ENV_BEGIN = re.compile(r"\\begin\{(%s)\}" % _ENVS)
_ENV_END = re.compile(r"\\end\{(%s)\}" % _ENVS)


def _close_prev(clean: List[str], mm: "MathMap", j: int) -> None:
    """The display block at original line j directly follows a kept line that stops mid-sentence."""
    if clean and mm.orig_index and mm.orig_index[-1] == j - 1 and ends_open(clean[-1]):
        clean[-1], mm.pos_maps[-1] = close_before_display(clean[-1], mm.pos_maps[-1])


def sanitize_lines(lines: Sequence[str]) -> Tuple[List[str], MathMap]:
    """Sanitise paragraphs (one engine "line" each). Handles multi-line $$ and \\begin..\\end blocks."""
    mm = MathMap(orig_texts=list(lines))
    clean: List[str] = []
    in_env = in_dollars = in_brackets = False
    removed_gap = False        # a maths line was just removed: do not let its two blank neighbours stack up
    for j, line in enumerate(lines):
        s = line.strip()
        if not s and removed_gap and clean and not clean[-1].strip():
            mm.stats.chars_total += len(line)
            continue
        if in_env or in_dollars or in_brackets:
            removed_gap = True
            mm.stats.chars_total += len(line)
            mm.stats.chars_removed += len(line)
            if in_env and _ENV_END.search(line):
                in_env = False
            if in_dollars and s.endswith("$$"):
                in_dollars = False
            if in_brackets and s.endswith("\\]"):
                in_brackets = False
            continue
        opened = False
        if _ENV_BEGIN.search(line) and not _ENV_END.search(line):
            in_env = opened = True
        elif s.startswith("$$") and s.count("$$") == 1:
            in_dollars = opened = True
        elif s.startswith("\\[") and "\\]" not in s:
            in_brackets = opened = True
        if opened:
            removed_gap = True
            mm.stats.display += 1
            mm.stats.chars_total += len(line)
            mm.stats.chars_removed += len(line)
            _close_prev(clean, mm, j)
            continue
        new, pmap, st = sanitize_paragraph(line)
        mm.stats.merge(st)
        if new is None:
            removed_gap = True
            if s:
                _close_prev(clean, mm, j)
            continue
        removed_gap = False
        clean.append(new)
        mm.orig_index.append(j)
        mm.pos_maps.append(pmap)
    # the engine drops blank lines at both ends of a pasted text; do it here so indices stay aligned
    while clean and not clean[-1].strip():
        clean.pop(); mm.orig_index.pop(); mm.pos_maps.pop()
    while clean and not clean[0].strip():
        clean.pop(0); mm.orig_index.pop(0); mm.pos_maps.pop(0)
    return clean, mm


def sanitize_text(text: str) -> Tuple[str, MathMap]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    clean, mm = sanitize_lines(lines)
    return "\n".join(clean), mm


# ----------------------------------------------------------------------------- DOCX / OMML
_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


_MATH_FONTS = ("cambria math", "stix two math", "stix math", "latin modern math", "asana math", "xits math")


def omml_flags(docx_bytes: bytes, expected: int) -> Optional[List[Tuple[int, bool]]]:
    """Per paragraph, in the SAME order the engine's parse_docx yields them:
    (number of Word equation objects in it, has_visible_text_outside_equations).
    Returns None if the traversal does not line up with the engine (then OMML handling is skipped)."""
    from lxml import etree
    try:
        zf = zipfile.ZipFile(io.BytesIO(docx_bytes))
        root = etree.fromstring(zf.read("word/document.xml"), etree.XMLParser(huge_tree=True, resolve_entities=False))
    except Exception:
        return None
    ns = {"m": _M_NS, "w": _W_NS}
    body = root.find("{%s}body" % _W_NS)
    out: List[Tuple[int, bool]] = []

    def one(p):
        n = len(p.xpath(".//m:oMath[not(ancestor::m:oMath)]", namespaces=ns))
        txt = "".join(t.text or "" for t in p.xpath(".//w:t[not(ancestor::m:oMath)]", namespaces=ns)).strip()
        out.append((n, bool(txt)))

    if body is not None:
        for child in body:
            if child.tag == "{%s}p" % _W_NS:
                one(child)
            elif child.tag in ("{%s}tbl" % _W_NS, "{%s}sdt" % _W_NS):
                for p in child.iter("{%s}p" % _W_NS):
                    one(p)
    return out if len(out) == expected else None


def omml_layout(docx_bytes: bytes, engine_texts: Sequence[str]) -> Optional[List[Tuple[int, bool, Optional[List[int]]]]]:
    """Like omml_flags, plus the character offset of every Word equation inside the text the engine built for that
    paragraph (the engine reads only w:r runs, so an equation leaves no character at all, just a seam between runs).
    The offsets are trusted only when this walk rebuilds the engine text exactly; otherwise that paragraph gets None
    and the older gap-repair fallback is used for it."""
    from lxml import etree
    try:
        zf = zipfile.ZipFile(io.BytesIO(docx_bytes))
        root = etree.fromstring(zf.read("word/document.xml"), etree.XMLParser(huge_tree=True, resolve_entities=False))
    except Exception:
        return None
    W, M = "{%s}" % _W_NS, "{%s}" % _M_NS
    ns = {"m": _M_NS, "w": _W_NS}
    body = root.find(W + "body")
    out: List[Tuple[int, bool, Optional[List[int]]]] = []

    def one(p):
        n = len(p.xpath(".//m:oMath[not(ancestor::m:oMath)]", namespaces=ns))
        txt = "".join(t.text or "" for t in p.xpath(".//w:t[not(ancestor::m:oMath)]", namespaces=ns)).strip()
        pos, chunks, marks = 0, [], []
        for el in p.iter():
            if el.tag == M + "oMath":
                anc = el.getparent()
                nested = False
                while anc is not None and anc is not p:
                    if anc.tag == M + "oMath":
                        nested = True
                        break
                    anc = anc.getparent()
                if not nested:
                    marks.append(pos)
            elif el.tag == W + "r":                       # same extraction rules as the engine's parse_docx
                parts = []
                for ch in el:
                    if ch.tag == W + "t":
                        parts.append(ch.text or "")
                    elif ch.tag == W + "tab":
                        parts.append("\t")
                    elif ch.tag in (W + "br", W + "cr"):
                        if ch.get(W + "type") in ("page", "column"):
                            continue
                        parts.append(" ")
                    elif ch.tag == W + "noBreakHyphen":
                        parts.append("-")
                seg = "".join(parts)
                chunks.append(seg)
                pos += len(seg)
        out.append((n, bool(txt), marks, "".join(chunks)))

    if body is not None:
        for child in body:
            if child.tag == W + "p":
                one(child)
            elif child.tag in (W + "tbl", W + "sdt"):
                for p in child.iter(W + "p"):
                    one(p)
    if len(out) != len(engine_texts):
        return None
    return [(n, has, (marks if rebuilt == et else None)) for (n, has, marks, rebuilt), et in zip(out, engine_texts)]


_SPACE_RUN = re.compile(r"[ \t\u00a0]{2,}")
_SPACE_PUNCT = re.compile(r"[ \t\u00a0]+(?=[,.;:!?؟،؛)\]])")


def sanitize_docx_paras(paras, flags=None) -> Tuple[list, MathMap]:
    """Sanitise engine Para objects from parse_docx; run offsets (font/size evidence) are remapped.

    `flags`: omml_layout() rows (n_equations, has_text, offsets|None) or legacy omml_flags() rows (n, has_text).

    * paragraph = only Word equations / only formula text -> dropped;
    * Word equation inside a sentence -> a placeholder at its exact position ("Let <eq>, <eq> be" -> "Let §, § be");
    * LaTeX / Unicode formulas typed as text, and runs set in a math font (Cambria Math ...) -> one placeholder each;
    * a sentence that runs on into a dropped display equation ("... such that") is closed by a placeholder.
    """
    mm = MathMap(orig_texts=[p.text for p in paras])
    out = []
    for j, p in enumerate(paras):
        row = flags[j] if flags is not None else (0, True)
        n_omml, has_text = row[0], row[1]
        marks = row[2] if len(row) > 2 else None
        mm.stats.omml += n_omml
        text = p.text
        if n_omml and not has_text and not text.strip():
            mm.stats.display += 1                       # a paragraph that was nothing but an equation
            _close_prev_para(out, mm, j)
            continue
        font_cuts = [(a, b) for (a, b, font, *_r) in p.runs
                     if str(font).lower() in _MATH_FONTS and b > a and b <= len(text) and text[a:b].strip()]
        if n_omml and marks is None:
            new, pmap, st = _legacy_omml(text, font_cuts)
        else:
            new, pmap, st = sanitize_paragraph(text, extra_cuts=font_cuts, inserts=marks or ())
        mm.stats.merge(st)
        if new is None:
            if text.strip():
                _close_prev_para(out, mm, j)
            continue
        if pmap is not None:
            p.runs = _remap_runs(p.runs, pmap, new)
            p.text = new
        out.append(p)
        mm.orig_index.append(j)
        mm.pos_maps.append(pmap)
    return out, mm


def _remap_runs(runs, pmap, new_text):
    def conv(x, _pm=pmap):
        lo, hi = 0, len(_pm) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if _pm[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        return lo
    out = []
    for (a, b, *rest) in runs:
        a2, b2 = conv(a), conv(b)
        if b2 > a2:
            if rest and str(rest[0]).lower() in _MATH_FONTS and new_text[a2:b2].strip() in ("", PLACEHOLDER):
                continue                                # a Cambria Math run reduced to its placeholder: no font evidence
            out.append((a2, b2, *rest))
    return out


def _close_prev_para(out, mm: "MathMap", j: int) -> None:
    if out and mm.orig_index and mm.orig_index[-1] == j - 1 and ends_open(out[-1].text):
        p = out[-1]
        p.text, mm.pos_maps[-1] = close_before_display(p.text, mm.pos_maps[-1])


def _legacy_omml(text, font_cuts):
    """Fallback when the equation offsets could not be located: the old seam repair (collapse the double space an
    equation leaves, drop the space before punctuation)."""
    new, pmap, st = sanitize_paragraph(text, extra_cuts=font_cuts)
    if new is None:
        return new, pmap, st
    base = new
    extra = [(m.start() + 1, m.end()) for m in _SPACE_RUN.finditer(base)] + \
            [(m.start(), m.end()) for m in _SPACE_PUNCT.finditer(base)]
    if not extra:
        return new, pmap, st
    keep = bytearray(len(base))
    for a, b in extra:
        for k in range(a, b):
            keep[k] = 1
    base_map = pmap if pmap is not None else list(range(len(text))) + [len(text)]
    chars = [c for k, c in enumerate(base) if not keep[k]]
    pos = [base_map[k] for k in range(len(base)) if not keep[k]]
    pos.append((pos[-1] + 1) if pos else len(text))
    st.chars_removed += len(base) - len(chars)
    return "".join(chars), pos, st


def has_math_markup(text: str) -> bool:
    return bool(_spans(text)) or any(rx.search(text) for rx in _DISPLAY_RX)
