# -*- coding: utf-8 -*-
"""Paragraph provenance for DOCX: WHERE inside a Word file does pasted / generated material sit?

The wording engine scores text. This module reads what Word itself recorded about each paragraph - the raw XML -
and reports, per paragraph, the traces that text was brought in from another source (a chat window, a web page)
instead of being typed in the document's own template. It never scores the document and never changes the engine;
`nebras_report` uses it only to decide WHICH passages the PDF shades, and how large the shaded unit is.

Evidence is split in two kinds, because they do not carry the same weight:

  direct    UI fingerprints that only copy-paste from a chat / web page leaves on the paragraph itself:
            chat-interface font, chat text colour, chat background shade, web "Autospacing".
  support   weaker context that is common in honest documents too (a quotation in another font, a block with a
            different paragraph layout, a run of typed spaces used as an indent). It can lower the wording bar a
            little, but it can never shade a paragraph on its own.

Every trace must cover most of the paragraph's characters, so a single pasted word never marks a paragraph.
Nothing here is tuned on any particular file: the font / colour lists are the documented ones from
`format_forensics`, and the weights below are fixed constants.
"""
from __future__ import annotations

import io
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from lxml import etree

import format_forensics as ffm

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
COVER = 0.5                     # a trace must sit on >= 50% of the paragraph's characters
MIN_BASE_CHARS = 400            # characters needed before a "document body font" is trusted
WEIGHTS = {"chat_color": 0.9, "chat_font": 0.9, "chat_shade": 0.6, "autospacing": 0.4}
SUPPORT_WEIGHTS = {"font_diverges": 0.35, "layout_block": 0.35, "typed_indent": 0.25}
DIRECT_CAP = 1.6                # max log-odds a paragraph's direct traces may add
SUPPORT_CAP = 0.4
STRONG_DIRECT = 0.8             # direct score at which a paragraph counts as carrying a UI fingerprint
DOC_WIDE = 0.6                  # a trace on more than this share of the body paragraphs describes the FILE, not a place


def _w(tag: str) -> str:
    return "{%s}%s" % (W_NS, tag)


@dataclass
class ParaProv:
    index: int = -1                 # source-paragraph index (same enumeration as the engine's parse_docx)
    words: int = 0
    direct: float = 0.0             # 0..DIRECT_CAP
    support: float = 0.0            # 0..SUPPORT_CAP
    sig: Optional[tuple] = None     # which direct traces fired; equal sig on neighbours = same pasted block
    rsid: str = ""                  # w:rsidR of the paragraph (the editing session that created it)
    tags: List[str] = field(default_factory=list)

    @property
    def has_ui_fingerprint(self) -> bool:
        return self.direct >= STRONG_DIRECT

    @property
    def bonus(self) -> float:
        """Log-odds to ADD to the wording log-odds of this paragraph. Never negative."""
        return min(DIRECT_CAP, self.direct) + (min(SUPPORT_CAP, self.support) if self.direct > 0 or self.support >= 0.6 else 0.0)


def _paragraph_elements(root):
    """Same enumeration order as the engine's parse_docx() / nebras_report._iter_paragraph_elements()."""
    body = root.find(_w("body"))
    out = []
    if body is None:
        return out
    for child in body:
        if child.tag == _w("p"):
            out.append(child)
        elif child.tag in (_w("tbl"), _w("sdt")):
            out.extend(child.iter(_w("p")))
    return out


def _run_text(r) -> str:
    return "".join((t.text or "") for t in r.iter(_w("t")))


def _fonts_of(rpr) -> tuple:
    if rpr is None:
        return ()
    rf = rpr.find(_w("rFonts"))
    if rf is None:
        return ()
    return tuple(rf.get(_w(k)) or "" for k in ("ascii", "hAnsi", "cs"))


def _frac(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def read(docx_bytes: bytes) -> Optional[Dict[int, ParaProv]]:
    """{source paragraph index: ParaProv}, or None when the bytes are not a readable DOCX."""
    try:
        xml = zipfile.ZipFile(io.BytesIO(docx_bytes)).read("word/document.xml")
        root = etree.fromstring(xml, etree.XMLParser(huge_tree=True, resolve_entities=False))
    except Exception:
        return None
    paras = _paragraph_elements(root)
    rows: List[ParaProv] = []
    fonts_by_para: List[Counter] = []
    for i, p in enumerate(paras):
        pp = ParaProv(index=i, rsid=p.get(_w("rsidR")) or "")
        chars = 0
        cov = Counter()
        fonts = Counter()
        ppr = p.find(_w("pPr"))
        ppr_xml = etree.tostring(ppr, encoding="unicode") if ppr is not None else ""
        for r in p.iter(_w("r")):
            t = _run_text(r)
            if not t:
                continue
            n = len(t)
            chars += n
            rpr = r.find(_w("rPr"))
            rxml = etree.tostring(rpr, encoding="unicode") if rpr is not None else ""
            if ffm._CHAT_COLOR.search(rxml):
                cov["chat_color"] += n
            if ffm._CHAT_FONT.search(rxml):
                cov["chat_font"] += n
            if ffm._CHAT_SHADE.search(rxml):
                cov["chat_shade"] += n
            fs = _fonts_of(rpr)
            if fs and fs[0]:
                fonts[fs[0]] += n
        pp.words = 0
        txt = "".join(_run_text(r) for r in p.iter(_w("r")))
        pp.words = len(txt.split())
        if ffm._CHAT_SHADE.search(ppr_xml):
            cov["chat_shade"] = max(cov["chat_shade"], chars)
        if ffm._AUTOSPACE.search(ppr_xml):
            cov["autospacing"] = chars
        fired = []
        for k, w in WEIGHTS.items():
            if _frac(cov[k], chars) >= COVER:
                pp.direct += w
                fired.append(k)
        pp.sig = tuple(sorted(fired)) if fired else None
        pp.tags = list(fired)
        if len(txt) - len(txt.lstrip(" \t ")) >= 2:
            pp.support += SUPPORT_WEIGHTS["typed_indent"]
            pp.tags.append("typed_indent")
        rows.append(pp)
        fonts_by_para.append(fonts)

    # A trace present on most body paragraphs (e.g. web Autospacing on a whole converted file) says something about the
    # document as a whole - the document score already uses it - but it cannot tell WHICH paragraphs came from where.
    body_rows = [pp for pp in rows if pp.words >= ffm.MIN_WORDS]
    if len(body_rows) >= 5:
        for k in WEIGHTS:
            if sum(1 for pp in body_rows if k in pp.tags) / len(body_rows) > DOC_WIDE:
                for pp in rows:
                    if k in pp.tags:
                        pp.tags.remove(k)
                        pp.direct -= WEIGHTS[k]
        for pp in rows:
            pp.direct = max(0.0, round(pp.direct, 6))
            pp.sig = tuple(t for t in pp.tags if t in WEIGHTS) or None

    # document body font = the font carrying most characters of the longer paragraphs
    total = Counter()
    for pp, f in zip(rows, fonts_by_para):
        if pp.words >= ffm.MIN_WORDS:
            total.update(f)
    if sum(total.values()) >= MIN_BASE_CHARS and total:
        base, n = total.most_common(1)[0]
        if n / sum(total.values()) >= 0.5:
            for pp, f in zip(rows, fonts_by_para):
                if pp.words >= ffm.MIN_WORDS and f and sum(f.values()) and f.most_common(1)[0][0] != base \
                        and f.most_common(1)[0][1] / sum(f.values()) >= COVER:
                    pp.support += SUPPORT_WEIGHTS["font_diverges"]
                    pp.tags.append("font_diverges")

    # paragraph-layout islands (>= 3 consecutive body paragraphs sharing a layout different from the dominant one)
    try:
        # layout needs resolved paragraph properties; only the direct ones are available here, so read them directly
        sigs = []
        for i, p in enumerate(paras):
            ppr = p.find(_w("pPr"))
            sp = ppr.find(_w("spacing")) if ppr is not None else None
            ind = ppr.find(_w("ind")) if ppr is not None else None
            jc = ppr.find(_w("jc")) if ppr is not None else None
            sigs.append((None if ind is None else ind.get(_w("firstLine")),
                         None if jc is None else jc.get(_w("val")),
                         tuple(None if sp is None else sp.get(_w(k)) for k in ("after", "before", "line"))))
        body = [i for i, pp in enumerate(rows) if pp.words >= ffm.MIN_WORDS]
        if len(body) >= 8:
            dom = Counter(sigs[i] for i in body).most_common(1)[0][0]
            k = 0
            while k < len(body):
                if sigs[body[k]] == dom:
                    k += 1
                    continue
                j = k
                while j + 1 < len(body) and sigs[body[j + 1]] == sigs[body[k]]:
                    j += 1
                if j - k + 1 >= ffm.MIN_RUN:
                    for q in body[k:j + 1]:
                        rows[q].support += SUPPORT_WEIGHTS["layout_block"]
                        rows[q].tags.append("layout_block")
                k = j + 1
    except Exception:
        pass
    return {pp.index: pp for pp in rows}


def to_analysed(prov: Optional[Dict[int, ParaProv]], orig_index: List[int]) -> Dict[int, ParaProv]:
    """Re-key by the analysed-paragraph index (math_guard / docx_clean may have removed source paragraphs)."""
    if not prov:
        return {}
    return {a: prov[o] for a, o in enumerate(orig_index) if o in prov}
