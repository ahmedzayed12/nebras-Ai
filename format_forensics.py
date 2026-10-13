# -*- coding: utf-8 -*-
"""Formatting forensics for DOCX (report-only evidence; it never changes the score).

The v2.1 engine reads every formatting difference as "manual human editing" (fmt_incons, a human-side feature).
But formatting differences come in two very different shapes:

  scattered   single paragraphs that differ here and there        -> typical of a person editing by hand
  blocks      runs of >= 3 consecutive paragraphs sharing a        -> typical of text pasted in from another
              different signature (indent / alignment / spacing)     source (a chat window, another file)

This module finds the blocks and reports them, so a reviewer can see WHERE pasted material probably starts and
ends. It also reports a uniform first-line indent, but only as context: a fixed 0.5" indent (720 twips) is the
default of most thesis / APA templates and is equally present in human-written theses, so it is NOT AI evidence
by itself. Literal space characters used as an indent are reported separately.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import List, Tuple

MIN_WORDS = 25
MIN_RUN = 3


@dataclass
class FormatForensics:
    body: int = 0
    dominant_share: float = 0.0
    dominant: tuple = ()
    blocks: List[Tuple[int, int, int]] = field(default_factory=list)   # (first para index, last para index, n paras)
    block_share: float = 0.0            # share of off-dominant paragraphs that sit inside blocks
    indent_twips: int = 0
    indent_share: float = 0.0
    space_indent: int = 0               # paragraphs indented with literal spaces / tabs

    def lines(self) -> List[str]:
        out = []
        if self.body < 8:
            return out
        if self.blocks:
            spans = "، ".join("الفقرات %d–%d" % (a + 1, b + 1) for a, b, _ in self.blocks[:6])
            out.append("◇ جزر تنسيق: %d كتلة متصلة بتنسيق مختلف عن المتن (%s)، وتضم %.0f%% من الفقرات المخالفة. "
                       "الكتل المتصلة تشير إلى نص نُسخ من مصدر آخر أكثر مما تشير إلى تحرير يدوي متفرق؛ راجعها في خريطة الفقرات."
                       % (len(self.blocks), spans, 100 * self.block_share))
        if self.indent_twips and self.indent_share >= 0.6:
            out.append("◇ مسافة بادئة ثابتة %.2f بوصة في %.0f%% من الفقرات. هذا هو الإعداد الافتراضي لقوالب الرسائل الجامعية "
                       "وAPA، فلا يُحتسب دليلًا على التوليد وحده." % (self.indent_twips / 1440.0, 100 * self.indent_share))
        if self.space_indent >= 3:
            out.append("◇ %d فقرة أُزيحت بمسافات/Tab مكتوبة يدويًا بدل إعداد المسافة البادئة." % self.space_indent)
        return out


def _lead_ws(t: str) -> int:
    return len(t) - len(t.lstrip(" \t "))


def _sig(p):
    return (p.first or 0, p.align or "", (p.after or 0, p.before or 0, p.line or 0), _lead_ws(p.text) >= 2)


def analyze(paras) -> FormatForensics:
    body = [(i, p) for i, p in enumerate(paras)
            if getattr(p, "role", "body") == "body" and not getattr(p, "in_table", False)
            and len(p.text.split()) >= MIN_WORDS]
    ff = FormatForensics(body=len(body))
    if len(body) < 8:
        return ff
    sigs = [_sig(p) for _, p in body]
    dom, dn = Counter(sigs).most_common(1)[0]
    ff.dominant, ff.dominant_share = dom, dn / len(body)
    off = [k for k, s in enumerate(sigs) if s != dom]
    in_block = 0
    k = 0
    while k < len(sigs):
        if sigs[k] == dom:
            k += 1
            continue
        j = k
        while j + 1 < len(sigs) and sigs[j + 1] == sigs[k]:
            j += 1
        if j - k + 1 >= MIN_RUN:
            ff.blocks.append((body[k][0], body[j][0], j - k + 1))
            in_block += j - k + 1
        k = j + 1
    ff.block_share = in_block / len(off) if off else 0.0
    ind = Counter(p.first or 0 for _, p in body).most_common(1)[0]
    ff.indent_twips, ff.indent_share = (ind[0], ind[1] / len(body)) if ind[0] else (0, 0.0)
    ff.space_indent = sum(1 for _, p in body if _lead_ws(p.text) >= 2)
    return ff


# =============================================================================
# Paste artefacts in the raw DOCX XML (general, documented traces of text copied from a chat UI / web page)
# =============================================================================
import re
import zipfile
import io

_P = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_T = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>")
# ChatGPT web UI body text colours (#0D0D0D current, #374151 older) and its code/answer background (#F7F7F8, #444654)
_CHAT_COLOR = re.compile(r'<w:color w:val="(?:0D0D0D|374151|343541|ECECF1)"', re.I)
_CHAT_SHADE = re.compile(r'<w:shd [^>]*w:fill="(?:F7F7F8|444654|F9F9F9|343541)"', re.I)
# UI / system fonts carried over by copy-paste from a browser (never a thesis body font chosen in Word)
_CHAT_FONT = re.compile(r'w:(?:ascii|hAnsi)="(?:S[öo]hne[^"]*|ui-sans-serif|ui-serif|system-ui|-apple-system|Segoe UI|'
                        r'Google Sans[^"]*|Helvetica Neue|Inter|Roboto|Noto Sans|Arial Unicode MS|Anthropic[^"]*|'
                        r'Styrene[^"]*|Tiempos[^"]*|Copernicus)"')
_AUTOSPACE = re.compile(r'w:(?:before|after)Autospacing="(?:1|true|on)"')


@dataclass
class PasteArtifacts:
    long_paras: int = 0
    chat_color: float = 0.0
    chat_shade: float = 0.0
    chat_font: float = 0.0
    autospacing: float = 0.0
    space_indent_uniform: float = 0.0
    space_indent_n: int = 0
    paste_blocks: float = 0.0
    total: float = 0.0
    evidence: List[str] = field(default_factory=list)


WEIGHTS = {"chat_color": 1.2, "chat_shade": 0.8, "chat_font": 1.0, "autospacing": 0.5,
           "space_indent_uniform": 0.6, "paste_blocks": 0.5}
LABELS = {"chat_color": "لون نص واجهة ChatGPT (#0D0D0D / #374151)",
          "chat_shade": "تظليل خلفية منسوخ من واجهة دردشة (#F7F7F8 …)",
          "chat_font": "خط واجهة ويب/دردشة (Söhne, ui-sans-serif, Segoe UI, Inter …)",
          "autospacing": "تباعد فقرات تلقائي من صفحة ويب (Autospacing)",
          "space_indent_uniform": "إزاحة بعدد ثابت من المسافات المكتوبة قبل بداية الفقرات",
          "paste_blocks": "كتل متصلة بتنسيق مختلف (جزر لصق)"}


def paste_artifacts(content: bytes, ff: "FormatForensics" = None) -> PasteArtifacts:
    pa = PasteArtifacts()
    try:
        xml = zipfile.ZipFile(io.BytesIO(content)).read("word/document.xml").decode("utf-8", "ignore")
    except Exception:
        return pa
    rows = []
    for m in _P.finditer(xml):
        px = m.group(0)
        txt = "".join(_T.findall(px))
        if len(txt.split()) >= MIN_WORDS:
            rows.append((px, txt))
    n = len(rows)
    pa.long_paras = n
    if n < 5:
        return pa
    share = lambda rx: sum(1 for px, _ in rows if rx.search(px)) / n
    pa.chat_color, pa.chat_shade, pa.chat_font, pa.autospacing = (share(_CHAT_COLOR), share(_CHAT_SHADE),
                                                                   share(_CHAT_FONT), share(_AUTOSPACE))
    leads = [len(t) - len(t.lstrip("  ")) for _, t in rows]
    spaced = [x for x in leads if x >= 2]
    pa.space_indent_n = len(spaced)
    if len(spaced) >= 5:
        top = Counter(spaced).most_common(1)[0][1]          # same exact count of typed spaces = mechanical, not a slip
        pa.space_indent_uniform = (top / n) if top / len(spaced) >= 0.7 else 0.0
    if ff is not None and len(ff.blocks) >= 2 and ff.block_share >= 0.6:
        pa.paste_blocks = min(1.0, sum(b[2] for b in ff.blocks) / max(1, ff.body))
    tot = 0.0
    for k, w in WEIGHTS.items():
        v = getattr(pa, k)
        if v >= 0.05:                                          # at least 5% of the body paragraphs carry the trace
            z = min(1.0, v / 0.30)
            tot += w * z
            pa.evidence.append("   +%.1f  %s (%.0f%% من فقرات المتن)" % (w * z, LABELS[k], 100 * v))
    pa.total = tot
    return pa
