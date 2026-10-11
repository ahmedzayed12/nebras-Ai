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
_MATH_UNI = re.compile(r"[\u2200-\u22FF\u2A00-\u2AFF\u27C0-\u27EF\u2190-\u21FF\u0391-\u03C9\u1D400-\u1D7FF∑∏∫∂∇√∞]")
_WORDISH = re.compile(r"[^\W\d_]{3,}", re.U)           # a real word: >= 3 letters
_EQ_NUMBER = re.compile(r"\s*[\(\[]\s*\d+(?:\.\d+)*[a-z]?\s*[\)\]]\s*$")
_PLAIN_FORMULA = re.compile(
    r"(?<![\w])[A-Za-z]\w{0,2}(?:_\{?[A-Za-z0-9,+\-]+\}?|\^\{?[A-Za-z0-9+\-]+\}?)"
    r"(?:\s*(?:=|≈|≠|≤|≥|<|>)\s*[-+(]?[A-Za-z0-9\\.]+(?:\s*[-+*/^×·]\s*[A-Za-z0-9\\.()_{}]+)*)?")


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


def sanitize_paragraph(text: str) -> Tuple[Optional[str], Optional[List[int]], MathStats]:
    """Returns (clean_text | None if the paragraph must be dropped, new->old map | None, stats)."""
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
    for a, b in _spans(text):
        if not any(ca <= a and b <= cb for ca, cb in cuts):
            cuts.append((a, b))
            st.inline += 1
    if not cuts:
        return text, None, st
    cuts.sort()
    merged = [list(cuts[0])]
    for a, b in cuts[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    cuts = [(a, b) for a, b in merged]
    st.chars_removed = sum(b - a for a, b in cuts)
    new, pmap = _tidy(text, cuts)
    if len(_WORDISH.findall(new)) < 3:                      # only a stub is left: the paragraph was maths
        return None, None, MathStats(display=1, chars_removed=len(text), chars_total=len(text))
    return new, pmap, st


# ----------------------------------------------------------------------------- text-level API
_ENV_BEGIN = re.compile(r"\\begin\{(%s)\}" % _ENVS)
_ENV_END = re.compile(r"\\end\{(%s)\}" % _ENVS)


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
            continue
        new, pmap, st = sanitize_paragraph(line)
        mm.stats.merge(st)
        if new is None:
            removed_gap = True
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


_SPACE_RUN = re.compile(r"[ \t\u00a0]{2,}")
_SPACE_PUNCT = re.compile(r"[ \t\u00a0]+(?=[,.;:!?؟،؛)\]])")


def sanitize_docx_paras(paras, flags: Optional[Sequence[Tuple[int, bool]]] = None) -> Tuple[list, MathMap]:
    """Sanitise engine Para objects from parse_docx; run offsets (font/size evidence) are remapped.

    * paragraph = only Word equations / only formula text -> dropped;
    * Word equation inside a sentence -> the gap it leaves ("where  is") is repaired;
    * LaTeX typed as text, and runs set in a math font (Cambria Math ...) -> removed.
    """
    mm = MathMap(orig_texts=[p.text for p in paras])
    out = []
    for j, p in enumerate(paras):
        n_omml, has_text = flags[j] if flags is not None else (0, True)
        mm.stats.omml += n_omml
        text = p.text
        if n_omml and not has_text and not text.strip():
            mm.stats.display += 1                       # a paragraph that was nothing but an equation
            continue
        new, pmap, st = sanitize_paragraph(text)
        extra: List[Tuple[int, int]] = []
        if new is not None and pmap is None:            # nothing cut by the text rules yet
            base, base_map = text, None
        else:
            base, base_map = new, pmap
        if new is not None:
            for (a, b, font, *_r) in p.runs:            # text set in a math font = formula
                if str(font).lower() in _MATH_FONTS and pmap is None and b > a and b <= len(text):
                    extra.append((a, b))
            if n_omml and base is not None:
                for rx in (_SPACE_RUN,):
                    for m in rx.finditer(base):
                        extra.append((m.start() + 1, m.end()))
                for m in _SPACE_PUNCT.finditer(base):
                    extra.append((m.start(), m.end()))
            if extra and base_map is None:
                extra = sorted(set(extra))
                new2, pmap2 = _tidy(text, extra)
                if len(_WORDISH.findall(new2)) >= 3:
                    st.inline += len([1 for _ in extra if True])
                    st.chars_removed += len(text) - len(new2)
                    new, pmap = new2, pmap2
        mm.stats.merge(st)
        if new is None:
            continue
        if pmap is not None:
            def conv(x, _pm=pmap):
                lo, hi = 0, len(_pm) - 1
                while lo < hi:
                    mid = (lo + hi) // 2
                    if _pm[mid] < x:
                        lo = mid + 1
                    else:
                        hi = mid
                return lo
            runs = []
            for (a, b, *rest) in p.runs:
                a2, b2 = conv(a), conv(b)
                if b2 > a2:
                    runs.append((a2, b2, *rest))
            p.runs = runs
            p.text = new
        out.append(p)
        mm.orig_index.append(j)
        mm.pos_maps.append(pmap)
    return out, mm


def has_math_markup(text: str) -> bool:
    return bool(_spans(text)) or any(rx.search(text) for rx in _DISPLAY_RX)
