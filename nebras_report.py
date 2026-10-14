# -*- coding: utf-8 -*-
"""Nebras professional PDF report (Turnitin-style) built on top of the unchanged v2.1 engine.

This module NEVER calculates or alters an engine score. It only
  1. keeps the original engine's document-wide score as the headline result;
     shades direct-evidence sentences strongly and adds a *lighter, explicitly illustrative*
     shade where required to show the document score on the same visual scale;
  2. applies the shading to a COPY of the uploaded DOCX (run-level background only: text, fonts,
     spacing and layout are untouched, and any highlight that already existed is removed first);
  3. renders that copy to PDF with Microsoft Word or LibreOffice, so the pages look exactly like the
     original document, and adds a cover page, a header band (program name + SHA-256) and a footer
     band (page numbers) OUTSIDE the original page area, so nothing can be covered.
"""
from __future__ import annotations

import copy
import datetime as _dt
import difflib
import unicodedata
import hashlib
import html as _html
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

import backend_adapter as ba

ROOT = Path(__file__).resolve().parent
FONT_DIR = ROOT / "static" / "fonts" / "pdf"
_FONT_FILES = ("NebrasPlex-Regular.ttf", "NebrasPlex-Bold.ttf")
_SYSTEM_FALLBACK = (
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
)
_font_dir_cache = None


def _font_dir() -> Path:
    """Folder holding NebrasPlex-Regular.ttf / NebrasPlex-Bold.ttf, resolved lazily.

    1) the intended static/fonts/pdf;
    2) anywhere else inside the repository (e.g. fonts uploaded to the wrong folder);
    3) a system font (DejaVu Sans, covers Arabic + Latin) copied under the same file names,
       so a missing/misplaced font folder never blocks report generation.
    This only chooses the typeface of the PDF cover/details pages; no score is involved.
    """
    global _font_dir_cache
    if _font_dir_cache is not None:
        return _font_dir_cache
    if all((FONT_DIR / n).is_file() for n in _FONT_FILES):
        _font_dir_cache = FONT_DIR
        return _font_dir_cache
    try:
        for hit in sorted(ROOT.rglob(_FONT_FILES[0])):
            if all((hit.parent / n).is_file() for n in _FONT_FILES):
                _font_dir_cache = hit.parent
                return _font_dir_cache
    except OSError:
        pass
    for reg, bold in _SYSTEM_FALLBACK:
        if Path(reg).is_file() and Path(bold).is_file():
            tmp = Path(tempfile.gettempdir()) / "nebras_fallback_fonts"
            tmp.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(reg, tmp / _FONT_FILES[0])
            shutil.copyfile(bold, tmp / _FONT_FILES[1])
            _font_dir_cache = tmp
            return _font_dir_cache
    raise RuntimeError(
        "ملفات خط التقرير غير موجودة: ارفع NebrasPlex-Regular.ttf و NebrasPlex-Bold.ttf "
        "إلى المجلد static/fonts/pdf في المستودع، أو ثبّت الخط fonts-dejavu-core."
    )
PROGRAM_NAME = "Nebras Ai detector"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _w(tag: str) -> str:
    return "{%s}%s" % (W_NS, tag)


# ---------------------------------------------------------------- shading policy
HIGH = 55            # engine's own "high suspicion" paragraph band (same cut-off the UI uses)
MID = 30             # engine's "medium" band
STRONG = 1.2         # direct evidence needed inside one sentence (weights = engine's _PARA_W)
MIN_SENT_WORDS = 4   # a 1-3 word fragment is never shaded by paragraph membership alone
MIN_WORDS = 40       # engine: below 40 words it refuses to judge
HIGHLIGHT_HEX = "8EE3F5"     # strong: directly flagged by sentence / paragraph heuristic
SOFT_HIGHLIGHT_HEX = "DDF5FA"  # supplementary, visibly paler cyan (illustrative only)
HUMAN_TRACE = {"repeated_word", "typo_en", "double_space", "space_before_punct", "no_space_after_punct",
               "repeat_punct", "lower_start", "mixed_punct_script", "ar_orth"}


@dataclass
class Plan:
    flags: dict = field(default_factory=dict)       # para index -> [(start, end)] merged character ranges
    qualifying_words: int = 0
    flagged_words: int = 0
    qualifying_sentences: int = 0
    flagged_sentences: int = 0
    per_para: dict = field(default_factory=dict)    # para index -> (words, flagged_words)
    sufficient: bool = True
    soft_flags: dict = field(default_factory=dict)  # paragraph -> paler illustrative ranges
    soft_words: int = 0
    soft_sentences: int = 0
    engine_index: float | None = None             # unchanged document-wide engine score
    target_words: int = 0

    @property
    def percent(self) -> float:
        """Direct-evidence (strong) shaded percentage; not the engine index."""
        return 100.0 * self.flagged_words / self.qualifying_words if self.qualifying_words else 0.0

    @property
    def total_words(self) -> int:
        return self.flagged_words + self.soft_words

    @property
    def total_percent(self) -> float:
        return 100.0 * self.total_words / self.qualifying_words if self.qualifying_words else 0.0


def percent_label(plan: Plan) -> str:
    """The headline ALWAYS comes from the engine, never from highlight coverage."""
    if not plan.sufficient:
        return "—"
    p = plan.engine_index if plan.engine_index is not None else plan.percent
    return "%d%%" % int(max(0, min(100, p)) + 0.5)


# ---------------------------------------------------------------- source -> paragraphs
def _text_lines(text: str):
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    return lines


def _paras_for(rep, filename, data, text):
    """Rebuilds the paragraphs exactly as they were analysed (equations removed by math_guard) and
    returns them with the MathMap that links them back to the ORIGINAL document paragraphs."""
    import math_guard
    eng = ba.get_engine()
    flags = None
    if text is not None:
        orig = _text_lines(text)
        clean, mm = math_guard.sanitize_lines(orig)
        paras = [eng.Para(text=l) for l in clean]
        kind = "text"
    else:
        ext = Path(filename).suffix.lower()
        with tempfile.TemporaryDirectory(prefix="nebras_src_") as tmp:
            path = Path(tmp) / ba.filename_only(filename)
            path.write_bytes(data)
            if ext == ".docx":
                paras, _meta, mm, _st, _gen = ba.prepare_docx(str(path), data)   # same preparation as the analysis
                kind = "docx"
            elif ext == ".pdf":
                from pdf_input import extract_pdf_text
                orig = _text_lines(extract_pdf_text(data))
                clean, mm = math_guard.sanitize_lines(orig)
                paras = [eng.Para(text=l) for l in clean]
                kind = "pdf"
            else:
                orig = _text_lines(eng._read_txt(str(path)))
                clean, mm = math_guard.sanitize_lines(orig)
                paras = [eng.Para(text=l) for l in clean]
                kind = "text"
    eng.assign_roles(paras, rep.is_docx)
    if [p.text for p in paras] != list(rep.texts):
        raise ValueError("المصدر المرفق لا يطابق نتيجة الفحص. أعد تحليل الملف ثم أنشئ التقرير.")
    return paras, kind, mm


def _plan_to_original(plan: "Plan", mm) -> "Plan":
    """Move plan ranges computed on the analysed paragraphs onto the SOURCE paragraphs.

    `mm` (a math_guard.MathMap) links analysed paragraphs to the source document; its `orig_index`
    also reflects the non-prose paragraphs docx_clean removed (front matter, table cells, code), so
    the mapping is valid whenever any paragraph was removed - not only when equations were present.
    """
    if mm is None:
        return plan
    def conv(d):
        out = {}
        for i, ranges in d.items():
            for (a, b) in ranges:
                j, a2, b2 = mm.to_original(i, a, b)
                out.setdefault(j, []).append((a2, b2))
        return out
    import copy
    p2 = copy.copy(plan)
    p2.flags, p2.soft_flags = conv(plan.flags), conv(plan.soft_flags)
    p2.per_para = {mm.orig_index[i]: v for i, v in plan.per_para.items() if i < len(mm.orig_index)}
    return p2


def _source_shading(rep, paras, plan: "Plan", mm):
    """Return (source_texts, shade) expressed on the paragraphs the report is actually rendered from.

    The analysed paragraphs (`paras`) can be shorter than the source: math_guard drops equation
    paragraphs and docx_clean drops the title/author block, table cells and code blocks *before* the
    engine runs. `mm` maps analysed -> source; it must be applied whenever ANY paragraph was removed,
    not only when equations were present. Keying this on `mm.active` (equations only) made a Word file
    with front matter or table cells but no equations raise
    "تعذّر مطابقة فقرات Word مع نتيجة التحليل" because the plan stayed indexed on the shortened list
    while the report builder walked the full original Word XML.
    """
    removed = mm is not None and (len(mm.orig_index) != len(mm.orig_texts)
                                  or any(pm is not None for pm in mm.pos_maps))
    if removed:
        return list(mm.orig_texts), _plan_to_original(plan, mm)
    return list(rep.texts), plan


# ---------------------------------------------------------------- what to shade
def plan_highlights(rep, paras) -> Plan:
    """Sentence-level shading, gated by the engine's own paragraph verdicts.

    * paragraph score >= 55  -> the paragraph is judged AI as a unit; its sentences are shaded
      (a sentence that carries human traces such as typos/double spaces and no direct AI marker is not);
    * paragraph score 30-54 or unscored short paragraph -> only sentences that carry direct engine
      evidence (weights from the engine's own _PARA_W) are shaded;
    * below that nothing is shaded.
    """
    eng = ba.get_engine()
    W = eng._PARA_W
    pscore = {i: sc for i, sc, _n in rep.para_scores}
    ev, trace = defaultdict(list), defaultdict(list)
    for f in rep.findings:
        if f.para < 0:
            continue
        if f.kind in W:
            weight = W[f.kind] * (0.25 if "ضعيفة" in (f.note or "") else 1.0)
            ev[f.para].append((f.start, weight))
        elif f.kind in HUMAN_TRACE:
            trace[f.para].append(f.start)

    plan = Plan()
    for i, p in enumerate(paras):
        if p.role not in ("body", "list"):
            continue
        spans = eng.split_sentences(p.text)
        if not spans:
            continue
        sc = pscore.get(i)
        hits = []                       # (sentence index, start, end, words)
        pw = fw = 0
        for k, (a, b) in enumerate(spans):
            n = len(eng.WORD_RE.findall(p.text[a:b]))
            if n == 0:
                continue
            pw += n
            plan.qualifying_sentences += 1
            e = sum(w for s, w in ev[i] if a <= s < b)
            has_trace = any(a <= s < b for s in trace[i])
            hit = False
            if sc is not None and sc >= HIGH and n >= MIN_SENT_WORDS and not (has_trace and e < STRONG):
                hit = True
            elif e >= STRONG and (sc is None or sc >= MID):
                hit = True
            if hit:
                hits.append((k, a, b, n))
                fw += n
        plan.qualifying_words += pw
        plan.flagged_words += fw
        plan.flagged_sentences += len(hits)
        plan.per_para[i] = (pw, fw)
        merged = []
        for k, a, b, _n in hits:
            if merged and merged[-1][2] == k - 1:
                merged[-1][1] = b
                merged[-1][2] = k
            else:
                merged.append([a, b, k])
        if merged:
            plan.flags[i] = [(a, b) for a, b, _ in merged]
    if plan.qualifying_words < MIN_WORDS:
        plan.sufficient = False
        plan.flags = {}
        plan.flagged_words = plan.flagged_sentences = 0
        plan.per_para = {i: (w, 0) for i, (w, _f) in plan.per_para.items()}
    return plan


def _merge_ranges(ranges):
    """Coalesce neighbouring character ranges without touching the original text."""
    out = []
    for a, b in sorted(ranges):
        if a >= b:
            continue
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def balance_to_engine_index(rep, paras, plan: Plan) -> Plan:
    """Prepare *visualization* so total shaded words approximate the original engine index.

    The engine's 0-100 document score is NOT a word-level probability.  Strong tint
    denotes the existing evidence-based selection.  Supplementary tint is a separate
    visual allocation (ranked by the existing paragraph scores/evidence), NOT an
    independently detected AI passage.  Never alter score, verdict or engine data.

    If more words were strongly flagged than the document index permits, keep the
    highest-signal strongly flagged sentences first; if fewer, retain all strong
    shades and extend with lower-confidence sentences in the lighter tint.
    An endpoint can fall mid-sentence to avoid reporting an inaccurate ratio.
    """
    plan.engine_index = max(0.0, min(100.0, float(rep.score))) if plan.sufficient else None
    if not plan.sufficient:
        return plan
    plan.target_words = max(0, min(plan.qualifying_words,
                                  int(plan.qualifying_words * plan.engine_index / 100.0 + 0.5)))
    eng = ba.get_engine()
    W = eng._PARA_W
    pscore = {i: sc for i, sc, _n in rep.para_scores}
    evidence, human = defaultdict(list), defaultdict(list)
    for f in rep.findings:
        if f.para < 0:
            continue
        if f.kind in W:
            evidence[f.para].append((f.start, W[f.kind] * (0.25 if "ضعيفة" in (f.note or "") else 1.0)))
        elif f.kind in HUMAN_TRACE:
            human[f.para].append(f.start)

    candidates = []
    for i, para in enumerate(paras):
        if para.role not in ("body", "list"):
            continue
        marked = plan.flags.get(i, ())
        for a, b in eng.split_sentences(para.text):
            tokens = list(eng.WORD_RE.finditer(para.text, a, b))
            if not tokens:
                continue
            strong = any(start <= a and b <= end for start, end in marked)
            ev = sum(weight for off, weight in evidence[i] if a <= off < b)
            trace = any(a <= off < b for off in human[i])
            priority = float(pscore.get(i, 0)) + 8 * ev - (20 if trace else 0)
            if len(tokens) < MIN_SENT_WORDS:
                priority -= 15
            candidates.append((strong, priority, i, a, b, tokens))

    # Strong direct-evidence passages are kept ahead of any supplementary shading.
    # Among each group prefer more suspicious sentences, with stable source order.
    candidates.sort(key=lambda item: (-int(item[0]), -item[1], item[2], item[3]))
    strong_ranges, soft_ranges = defaultdict(list), defaultdict(list)
    strong_counts, soft_counts = defaultdict(int), defaultdict(int)
    strong_sents = soft_sents = 0
    left = plan.target_words
    for is_strong, _priority, i, a, b, tokens in candidates:
        if left <= 0:
            break
        take = min(len(tokens), left)
        # A whole selected sentence retains punctuation; a partial one ends at a word boundary.
        end = b if take == len(tokens) else tokens[take - 1].end()
        dest = strong_ranges if is_strong else soft_ranges
        dest[i].append((a, end))
        if is_strong:
            strong_counts[i] += take
            strong_sents += 1
        else:
            soft_counts[i] += take
            soft_sents += 1
        left -= take

    plan.flags = {i: _merge_ranges(v) for i, v in strong_ranges.items()}
    plan.soft_flags = {i: _merge_ranges(v) for i, v in soft_ranges.items()}
    plan.flagged_words = sum(strong_counts.values())
    plan.soft_words = sum(soft_counts.values())
    plan.flagged_sentences = strong_sents
    plan.soft_sentences = soft_sents
    plan.per_para = {i: (w, strong_counts[i]) for i, (w, _old) in plan.per_para.items()}
    return plan


# ---------------------------------------------------------------- DOCX: strip + shade
_RPR_ORDER_AFTER_SHD = ("fitText", "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath")


def _set_shading(rpr, fill: str):
    for old in rpr.findall(_w("shd")):
        rpr.remove(old)
    shd = etree.Element(_w("shd"))
    shd.set(_w("val"), "clear")
    shd.set(_w("color"), "auto")
    shd.set(_w("fill"), fill)
    anchor = None
    for ch in rpr:
        if etree.QName(ch).localname in _RPR_ORDER_AFTER_SHD:
            anchor = ch
            break
    if anchor is not None:
        anchor.addprevious(shd)
    else:
        rpr.append(shd)


def strip_existing_highlights(root) -> int:
    """Remove w:highlight and run-level w:shd. Paragraph/table shading and everything else stay."""
    n = 0
    for el in list(root.iter(_w("highlight"))):
        el.getparent().remove(el)
        n += 1
    for el in list(root.iter(_w("shd"))):
        parent = el.getparent()
        if parent is not None and parent.tag == _w("rPr"):
            parent.remove(el)
            n += 1
    return n


def _iter_paragraph_elements(root):
    """Same enumeration order as the engine's parse_docx()."""
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


def _run_atoms(run):
    """[(element, text_for_engine)]. Mirrors the engine's run-text rules character for character."""
    atoms = []
    for ch in run:
        tg = ch.tag
        if tg == _w("rPr"):
            continue
        if tg == _w("t"):
            atoms.append((ch, ch.text or ""))
        elif tg == _w("tab"):
            atoms.append((ch, "\t"))
        elif tg in (_w("br"), _w("cr")):
            atoms.append((ch, "" if ch.get(_w("type")) in ("page", "column") else " "))
        elif tg == _w("noBreakHyphen"):
            atoms.append((ch, "-"))
        else:
            atoms.append((ch, ""))
    return atoms


def _paragraph_runs(p):
    """[(run, start, end, atoms)] using the engine's offsets."""
    out, pos = [], 0
    for r in p.iter(_w("r")):
        atoms = _run_atoms(r)
        s = "".join(t for _e, t in atoms)
        if not s:
            continue
        out.append((r, pos, pos + len(s), atoms))
        pos += len(s)
    return out


def _paragraph_text(p) -> str:
    return "".join("".join(t for _e, t in atoms) for _r, _a, _b, atoms in _paragraph_runs(p))


def _split_run(run, atoms, cuts, segment_tint, strong_fill, soft_fill):
    """Replace a run with identically formatted spans, with two optional tint levels."""
    total = sum(len(t) for _e, t in atoms)
    bounds = [0] + sorted(set(c for c in cuts if 0 < c < total)) + [total]
    segs = list(zip(bounds[:-1], bounds[1:]))
    rpr = run.find(_w("rPr"))
    new_runs = []
    for (x, y) in segs:
        nr = etree.Element(run.tag, attrib=dict(run.attrib))
        if rpr is not None:
            nr.append(copy.deepcopy(rpr))
        tint = segment_tint(x, y)
        if tint:
            target = nr.find(_w("rPr"))
            if target is None:
                target = etree.Element(_w("rPr"))
                nr.insert(0, target)
            _set_shading(target, strong_fill if tint == "strong" else soft_fill)
        new_runs.append(nr)

    def seg_index(off):
        for k, (x, y) in enumerate(segs):
            if x <= off < y:
                return k
        return len(segs) - 1

    off = 0
    for el, txt in atoms:
        if el.tag == _w("t") and len(txt) > 0:
            pos = 0
            while pos < len(txt):
                k = seg_index(off + pos)
                take = min(len(txt) - pos, segs[k][1] - (off + pos))
                t = etree.SubElement(new_runs[k], _w("t"))
                t.text = txt[pos:pos + take]
                t.set("{%s}space" % XML_NS, "preserve")
                pos += take
            off += len(txt)
        elif len(txt) == 1:
            new_runs[seg_index(off)].append(copy.deepcopy(el))
            off += 1
        else:
            new_runs[seg_index(off) if off < total else len(segs) - 1].append(copy.deepcopy(el))
    parent = run.getparent()
    idx = parent.index(run)
    for k, nr in enumerate(new_runs):
        if any(c.tag != _w("rPr") for c in nr):
            parent.insert(idx + k, nr)
    parent.remove(run)


def apply_highlights(docx_bytes: bytes, plan: Plan, expected_texts,
                     fill=HIGHLIGHT_HEX, soft_fill=SOFT_HIGHLIGHT_HEX):
    """Returns (new_docx_bytes, number_of_pre_existing_highlight_marks_removed)."""
    zin = zipfile.ZipFile(io.BytesIO(docx_bytes))
    removed = 0
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            name = info.filename
            if name.startswith("word/") and name.endswith(".xml") and "/_rels/" not in name:
                try:
                    root = etree.fromstring(data, etree.XMLParser(huge_tree=True, resolve_entities=False))
                except etree.XMLSyntaxError:
                    zout.writestr(info, data)
                    continue
                removed += strip_existing_highlights(root)
                changed = True
                if name == "word/document.xml":
                    paras = _iter_paragraph_elements(root)
                    texts = [_paragraph_text(p) for p in paras]
                    exp = list(expected_texts)
                    # build_plain_docx replaces XML-illegal control characters (e.g. form
                    # feeds coming from PDF/OCR extraction) with a single space, one-for-one,
                    # so character offsets stay valid. Compare with that same normalization
                    # instead of failing on a control character that cannot live in XML.
                    if len(texts) != len(exp) or \
                            [_BAD_XML.sub(" ", t) for t in texts] != [_BAD_XML.sub(" ", t) for t in exp]:
                        raise ValueError("تعذّر مطابقة فقرات Word مع نتيجة التحليل؛ لم يُنشأ التقرير حفاظًا على الدقة.")
                    for i in sorted(set(plan.flags) | set(plan.soft_flags)):
                        runs = _paragraph_runs(paras[i])
                        strong_ranges = plan.flags.get(i, ())
                        soft_ranges = plan.soft_flags.get(i, ())
                        for (r, rs, re_, atoms) in runs:
                            strong_hit = [(max(a, rs) - rs, min(b, re_) - rs)
                                          for a, b in strong_ranges if a < re_ and b > rs]
                            soft_hit = [(max(a, rs) - rs, min(b, re_) - rs)
                                        for a, b in soft_ranges if a < re_ and b > rs]
                            if not strong_hit and not soft_hit:
                                continue
                            cuts = [c for ab in strong_hit + soft_hit for c in ab]

                            def tint(x, y, strong=strong_hit, soft=soft_hit):
                                if any(a <= x and y <= b for a, b in strong):
                                    return "strong"
                                if any(a <= x and y <= b for a, b in soft):
                                    return "soft"
                                return None

                            _split_run(r, atoms, cuts, tint, fill, soft_fill)
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            zout.writestr(info, data)
    zin.close()
    return out.getvalue(), removed


# ---------------------------------------------------------------- plain text -> DOCX
_AR_RE = re.compile("[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_BAD_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def build_plain_docx(lines) -> bytes:
    """A clean A4 document for pasted text / TXT / MD: one paragraph per line, text untouched."""
    paras = []
    for line in lines:
        line = _BAD_XML.sub(" ", line)
        arabic = len(_AR_RE.findall(line))
        latin = len(re.findall(r"[A-Za-z]", line))
        rtl = arabic > latin
        ppr = '<w:pPr>%s<w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr>' % ("<w:bidi/>" if rtl else "")
        rpr = ('<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/><w:sz w:val="23"/><w:szCs w:val="23"/>%s</w:rPr>'
               % ("<w:rtl/>" if rtl else ""))
        runs = []
        for k, piece in enumerate(line.split("\t")):
            if k:
                runs.append("<w:r>%s<w:tab/></w:r>" % rpr)
            if piece:
                runs.append('<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r>' % (rpr, _html.escape(piece, quote=False)))
        paras.append("<w:p>%s%s</w:p>" % (ppr, "".join(runs)))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="%s"><w:body>%s<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1440" w:right="1304" w:bottom="1440" w:left="1304" w:header="708" w:footer="708" w:gutter="0"/>'
                '</w:sectPr></w:body></w:document>' % (W_NS, "".join(paras)))
    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '</Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


# ---------------------------------------------------------------- DOCX -> PDF
def _find_soffice():
    env = os.environ.get("NEBRAS_SOFFICE")
    if env and Path(env).exists():
        return env
    for name in ("soffice", "libreoffice", "soffice.exe"):
        p = shutil.which(name)
        if p:
            return p
    for p in (r"C:\Program Files\LibreOffice\program\soffice.exe",
              r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
              "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if Path(p).exists():
            return p
    return None


def _word_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import docx2pdf  # noqa: F401
    except Exception:
        return False
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application"))
        return True
    except Exception:
        return False


def converter_status() -> dict:
    return {"word": _word_available(), "libreoffice": _find_soffice()}


def _convert_with_word(src: Path, dst: Path):
    # A dedicated, invisible Word instance (DispatchEx) is used instead of docx2pdf's
    # Dispatch(): Dispatch attaches to whichever Word process is already running (stale
    # COM instances accumulate on this machine), which locks the input .docx and raises
    # WinError 32. DispatchEx always spawns a fresh instance, the document is opened
    # read-only, and the instance is always quit afterwards. Word 2010 automation can
    # ignore Quit, so the exact instance we created is force-closed to avoid new zombies.
    code = (
        "import sys, time, subprocess as sp, pythoncom, win32com.client\n"
        "def _word_pids():\n"
        "    try:\n"
        "        out = sp.run(['tasklist', '/FI', 'IMAGENAME eq WINWORD.EXE', '/FO', 'CSV', '/NH'],\n"
        "                     capture_output=True, text=True, creationflags=0x08000000).stdout\n"
        "    except Exception:\n"
        "        return set()\n"
        "    ids = set()\n"
        "    for line in out.splitlines():\n"
        "        cells = [c.strip().strip(chr(34)) for c in line.split(',')]\n"
        "        if cells and cells[0].upper() == 'WINWORD.EXE':\n"
        "            try:\n"
        "                ids.add(int(cells[1]))\n"
        "            except Exception:\n"
        "                pass\n"
        "    return ids\n"
        "src, dst = sys.argv[1], sys.argv[2]\n"
        "before = _word_pids()\n"
        "pythoncom.CoInitialize()\n"
        "word = win32com.client.DispatchEx('Word.Application')\n"
        "try:\n"
        "    word.Visible = False\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        "    word.DisplayAlerts = 0\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        "    doc = word.Documents.Open(src, ReadOnly=True, AddToRecentFiles=False)\n"
        "    try:\n"
        "        doc.SaveAs(dst, FileFormat=17)\n"
        "    finally:\n"
        "        doc.Close(False)\n"
        "finally:\n"
        "    try:\n"
        "        word.Quit()\n"
        "    except Exception:\n"
        "        pass\n"
        "try:\n"
        "    pythoncom.CoUninitialize()\n"
        "except Exception:\n"
        "    pass\n"
        "time.sleep(1.0)\n"
        "for pid in (_word_pids() - before):\n"
        "    sp.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)\n"
    )
    r = subprocess.run([sys.executable, "-c", code, str(src), str(dst)],
                       capture_output=True, text=True, timeout=240)
    if r.returncode != 0 or not dst.exists():
        raise RuntimeError("Word: " + (r.stderr or r.stdout or "").strip()[-300:])


def _convert_with_lo(src: Path, outdir: Path):
    soffice = _find_soffice()
    if not soffice:
        raise RuntimeError("LibreOffice غير موجود")
    profile = Path(tempfile.mkdtemp(prefix="nebras_lo_"))
    try:
        cmd = [soffice, "--headless", "--norestore", "--nologo", "--nodefault",
               "-env:UserInstallation=" + profile.as_uri(),
               "--convert-to", "pdf:writer_pdf_Export", "--outdir", str(outdir), str(src)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        out = outdir / (src.stem + ".pdf")
        if not out.exists():
            raise RuntimeError("LibreOffice: " + (r.stderr or r.stdout or "").strip()[-300:])
        return out
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def docx_to_pdf(docx_bytes: bytes) -> bytes:
    pref = os.environ.get("NEBRAS_CONVERTER", "auto").lower()
    errors = []
    with tempfile.TemporaryDirectory(prefix="nebras_pdf_") as tmp:
        tmp = Path(tmp)
        src = tmp / "document.docx"
        src.write_bytes(docx_bytes)
        order = {"word": ["word"], "libreoffice": ["lo"]}.get(pref, ["word", "lo"])
        for method in order:
            try:
                if method == "word":
                    if not _word_available():
                        continue
                    dst = tmp / "document_word.pdf"
                    _convert_with_word(src, dst)
                    return dst.read_bytes()
                out = _convert_with_lo(src, tmp)
                return out.read_bytes()
            except Exception as exc:  # try the next converter
                errors.append(str(exc))
    raise RuntimeError(
        "تعذّر تحويل المستند إلى PDF بنفس شكله الأصلي. ثبّت Microsoft Word (مع حزمة docx2pdf) أو "
        "LibreOffice المجاني من libreoffice.org ثم أعد المحاولة. " + " | ".join(errors))


# ---------------------------------------------------------------- PDF composition
TOP_BAND = 34.0       # header / footer live INSIDE the original page margins, so every page keeps the
BOTTOM_BAND = 26.0    # exact A4 size and layout of the uploaded file
INK = (0.106, 0.165, 0.255)
MUTED = (0.42, 0.46, 0.52)
LINE = (0.82, 0.85, 0.89)
CYAN = tuple(int(HIGHLIGHT_HEX[i:i + 2], 16) / 255 for i in (0, 2, 4))

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<path d="M26 6h12M32 6v6" stroke="#1B2A41" stroke-width="3" stroke-linecap="round" fill="none"/>
<path d="M20 16h24l3 6v22l-5 8H22l-5-8V22z" fill="#FFF3D6" stroke="#1B2A41" stroke-width="2.8" stroke-linejoin="round"/>
<path d="M32 24c5 6 7 9 7 13a7 7 0 0 1-14 0c0-4 2-7 7-13z" fill="#FF9F1C"/>
<path d="M22 52h20" stroke="#1B2A41" stroke-width="3" stroke-linecap="round"/></svg>"""


def _logo_pdf():
    import pymupdf
    svg = pymupdf.open(stream=LOGO_SVG.encode("utf-8"), filetype="svg")
    return pymupdf.open("pdf", svg.convert_to_pdf())


def _clean_ui_text(s: str) -> str:
    return re.sub("[\u2190-\u2BFF\U0001F000-\U0001FFFF\uFE0F]", "", s).strip()


def _dir(s: str) -> str:
    a = len(_AR_RE.findall(s))
    l = len(re.findall(r"[A-Za-z]", s))
    return "rtl" if a > l else "ltr"


def _esc(s) -> str:
    return _html.escape(str(s), quote=True)


BASE_CSS = """
@font-face{font-family:NP;src:url(NebrasPlex-Regular.ttf);}
@font-face{font-family:NP;font-weight:bold;src:url(NebrasPlex-Bold.ttf);}
body{font-family:NP;color:#1B2A41;font-size:9.5pt;line-height:1.35;}
b{font-weight:bold;}
.mono{font-family:monospace;font-size:7.6pt;}
.mut{color:#6B7585;}
.sm{font-size:8pt;}
.lbl{color:#6B7585;font-size:8pt;}
"""


def _box(page, rect, html_body, css=""):
    import pymupdf
    arch = pymupdf.Archive(str(_font_dir()))
    return page.insert_htmlbox(rect, html_body, css=BASE_CSS + css, archive=arch)


def _flow(page, x0, y, x1, ymax, html_body, css="", gap=10.0):
    """Draw a block at y and return the y where the next block can start (no dead gaps)."""
    import pymupdf
    spare, _scale = _box(page, pymupdf.Rect(x0, y, x1, ymax), html_body, css)
    return ymax - max(spare, 0) + gap


def _verdict_color(p):
    return (0.12, 0.62, 0.48) if p < 20 else (0.85, 0.55, 0.0) if p < 50 else (0.84, 0.19, 0.42)


def _cover(doc, rep, plan, meta, page_total):
    import pymupdf
    page = doc.new_page(width=595.28, height=841.89)
    Wd, H = page.rect.width, page.rect.height
    m = 42.0
    # header band
    page.draw_rect(pymupdf.Rect(0, 0, Wd, 128), color=None, fill=(0.106, 0.165, 0.255))
    page.show_pdf_page(pymupdf.Rect(m, 34, m + 46, 80), _logo_pdf(), 0)
    _box(page, pymupdf.Rect(m + 58, 30, Wd - m, 74),
         '<div style="color:#FFFFFF;font-size:21pt;font-weight:bold">%s</div>'
         '<div style="color:#FFC247;font-size:10pt">AI Writing Detection Report</div>' % PROGRAM_NAME)
    _box(page, pymupdf.Rect(m, 88, Wd - m, 122),
         '<div dir="rtl" style="color:#DDE6F3;font-size:11pt">'
         'تقرير كشف الكتابة بالذكاء الاصطناعي</div>')

    # The headline is ALWAYS the untouched engine index (not the strong-highlight share).
    top = 148.0
    card = pymupdf.Rect(m, top, Wd - m, top + 162)
    page.draw_rect(card, color=LINE, fill=(0.975, 0.98, 0.99), width=0.8, radius=0.04)
    pct = plan.engine_index if plan.sufficient else 0.0
    strong_pct, soft_pct = plan.percent, 100.0 * plan.soft_words / max(1, plan.qualifying_words)
    col = _verdict_color(pct) if plan.sufficient else MUTED
    _box(page, pymupdf.Rect(m + 18, top + 12, m + 201, top + 93),
         '<div style="font-size:46pt;font-weight:bold;color:#%02X%02X%02X;line-height:1">%s</div>'
         '<div class="lbl">Overall AI style index (engine)</div>'
         % (int(col[0] * 255), int(col[1] * 255), int(col[2] * 255), _esc(percent_label(plan))))
    if plan.sufficient and getattr(rep, "segmentation", None) is not None:
        ar = "التظليل يطابق النسبة؛ كل فقرة مظللة نسبها المحرك إلى النص المولَّد."
        stat = ("<b>%d</b> of <b>%d</b> eligible words shaded"
                % (plan.flagged_words, plan.qualifying_words))
    elif plan.sufficient:
        ar = "النتيجة النهائية من مؤشر المحرك العام."
        stat = ("<b>%d</b> of <b>%d</b> eligible words carry strong evidence"
                % (plan.flagged_words, plan.qualifying_words))
    else:
        ar = "النص المؤهَّل أقصر من 40 كلمة؛ لا يحكم المحرك على نص بهذا القِصَر"
        stat = "Fewer than 40 qualifying words: no score is reported."
    _box(page, pymupdf.Rect(m + 215, top + 13, Wd - m - 16, top + 103),
         '<div style="font-size:9.7pt">%s</div><div dir="rtl" style="font-size:9.3pt;margin-top:6px">%s</div>'
         % (stat, _esc(ar)))
    # Two tonal segments: strong evidence and light illustrative continuation.
    bx0, bx1, by = m + 18, Wd - m - 18, top + 112
    length = bx1 - bx0
    page.draw_rect(pymupdf.Rect(bx0, by, bx1, by + 12), color=None,
                   fill=(0.88, 0.9, 0.93), radius=0.5)
    if plan.sufficient and plan.total_words:
        page.draw_rect(pymupdf.Rect(bx0, by, bx0 + length * plan.total_percent / 100, by + 12),
                       color=None, fill=(0.83, 0.95, 0.975), radius=0.5)
    if plan.sufficient and plan.flagged_words:
        page.draw_rect(pymupdf.Rect(bx0, by, bx0 + length * strong_pct / 100, by + 12),
                       color=None, fill=CYAN, radius=0.5)
    page.draw_rect(pymupdf.Rect(bx0, by + 21, bx0 + 9, by + 30), color=None, fill=CYAN)
    _box(page, pymupdf.Rect(bx0 + 14, by + 16, bx0 + 250, by + 37),
         '<div dir="rtl" class="sm">تظليل أساسي: <b dir="ltr">%.0f%%</b></div>' % strong_pct)

    # document facts
    y = top + 176
    rows = [
        ("Document / المستند", _esc(meta["name"]), _dir(meta["name"])),
        ("File SHA-256", '<span class="mono">%s</span>' % meta["sha"], "ltr"),
        ("Report date", meta["date"], "ltr"),
        ("Pages (source)", str(meta["src_pages"]), "ltr"),
        ("Words (all / qualifying)", "%d / %d" % (rep.words, plan.qualifying_words), "ltr"),
        ("Engine", "AI Text Inspector v%s &nbsp;·&nbsp; <span class=\"mono\">%s</span>" % (meta["engine_version"], meta["engine_sha"]), "ltr"),
    ]
    html_rows = "".join(
        '<tr><td style="width:128px;padding:3px 0;color:#6B7585;font-size:8.5pt;vertical-align:top">%s</td>'
        '<td dir="%s" style="padding:3px 0;font-size:9pt">%s</td></tr>' % (_esc(k), d, v) for k, v, d in rows)
    _box(page, pymupdf.Rect(m, y, Wd - m, y + 150), '<table style="width:100%%">%s</table>' % html_rows)
    page.draw_line((m, y - 6), (Wd - m, y - 6), color=LINE, width=0.8)

    # Score confirmation from the untouched engine (no verdict sentence, no caution block).
    y2 = y + 160
    page.draw_line((m, y2 - 6), (Wd - m, y2 - 6), color=LINE, width=0.8)
    _box(page, pymupdf.Rect(m, y2, Wd - m, y2 + 92),
         '<div class="lbl">Original engine result / نتيجة المحرك الأصلية</div>'
         '<div style="font-size:15pt;font-weight:bold">%.0f / 100 &nbsp;<span style="font-size:9pt;font-weight:normal" class="mut">confidence %.0f%%</span></div>'
         % (rep.score, rep.confidence * 100))
    return page


def _details(doc, rep, plan):
    page = doc.new_page(width=595.28, height=841.89)
    Wd, H = page.rect.width, page.rect.height
    m, ymax = 42.0, H - 50
    y = _flow(page, m, TOP_BAND + 16, Wd - m, ymax,
              '<div style="font-size:16pt;font-weight:bold">Detection details</div>'
              '<div dir="rtl" style="font-size:10pt" class="mut">تفاصيل الفحص</div>', gap=8)
    how = ("<b>Reading the highlights.</b> Cyan marks passages selected by existing engine evidence "
           "and paragraph heuristics. This index is not a measured percentage of AI-authored words; "
           "shading does not prove that any particular sentence is AI-generated. Headers, references, "
           "captions and tables are excluded from the visual allocation.")
    how_ar = ("<b>قراءة التظليل:</b> يُظلَّل النص المختار بقرائن المحرك وقواعد الفقرات، "
              "وقد تُعطى الأولوية للمقاطع الأقوى. المؤشر ليس قياسًا حقيقيًا لنسبة الكلمات "
              "المكتوبة آليًا، والتظليل ليس إثباتًا لمصدر جملة بعينها. "
              "تُستثنى المراجع والعناوين والجداول.")
    y = _flow(page, m, y, Wd - m, ymax,
              '<div class="sm">%s</div><div dir="rtl" class="sm" style="margin-top:5px">%s</div>' % (how, how_ar), gap=16)
    reasons = [_clean_ui_text(x) for x in rep.reasons if _clean_ui_text(x)][:7]
    li = "".join('<div style="margin-bottom:3px">%s &nbsp;&#8226;</div>' % _esc(x) for x in reasons) or "<div>-</div>"
    y = _flow(page, m, y, Wd - m, ymax,
              '<div style="font-weight:bold;font-size:10.5pt">Engine reasons / أهم أسباب الحكم</div>'
              '<div dir="rtl" class="sm">%s</div>' % li, gap=14)
    rows = []
    for i, sc, n in rep.para_scores:
        w, f = plan.per_para.get(i, (n, 0))
        soft = sum(len(ba.get_engine().WORD_RE.findall(rep.texts[i][a:b]))
                   for a, b in plan.soft_flags.get(i, ()))
        share = "%d%%" % int(100.0 * (f + soft) / w + 0.5) if w else "-"
        snippet = _esc(re.sub(r"\s+", " ", rep.texts[i]).strip()[:70])
        rows.append('<tr><td>%d</td><td>%.0f</td><td>%d</td><td>%s</td><td dir="%s">%s</td></tr>'
                    % (i + 1, sc, n, share, _dir(rep.texts[i]), snippet))
    more = ""
    if len(rows) > 17:
        more = '<div class="sm mut">... +%d more paragraphs not listed</div>' % (len(rows) - 17)
        rows = rows[:17]
    if rows:
        table = ('<div style="font-weight:bold;font-size:10.5pt;margin-bottom:3px">Paragraph map / خريطة الفقرات</div>'
                 '<table style="width:100%%;font-size:8pt"><tr style="color:#6B7585"><td>#</td><td>Score</td><td>Words</td>'
                 '<td>Total tint</td><td>Opening</td></tr>%s</table>%s' % ("".join(rows), more))
    else:
        table = '<div class="sm mut">No paragraph reached the engine minimum length (25 words), so no paragraph map is available.</div>'
    _flow(page, m, y, Wd - m, ymax, table)
    return page


_TOKEN_RE = re.compile(r"[^\W_]+", re.U)


def _tok(s: str) -> str:
    return unicodedata.normalize("NFKC", s).casefold()


def _pdf_word_stream(body_pdf, skip_pages=()):
    """Every selectable word of the source PDF in reading order, plus per-line geometry.

    Returns (words, line_box, tokens) where
      words    = [(page, x0, y0, x1, y1, line_key)]
      line_box = {line_key: (y0, y1)}   one uniform height per text line (no jagged rectangles)
      tokens   = [(normalised token, word index)]
    """
    words, tokens, acc = [], [], {}
    for pno in range(len(body_pdf)):
        if pno + 1 in skip_pages:
            continue
        for x0, y0, x1, y1, txt, blk, ln, _wn in body_pdf[pno].get_text("words"):
            key = (pno, blk, ln)
            words.append((pno, x0, y0, x1, y1, key))
            acc.setdefault(key, []).append((y0, y1))
            for m in _TOKEN_RE.finditer(_tok(txt)):
                tokens.append((m.group(), len(words) - 1))
    line_box = {}
    for key, ys in acc.items():
        tallest = max(b - a for a, b in ys)
        solid = [(a, b) for a, b in ys if (b - a) >= 0.6 * tallest]
        line_box[key] = (min(a for a, _ in solid), max(b for _, b in solid))
    return words, line_box, tokens


def _align_paragraphs(texts, pdf_tokens):
    """Map every paragraph token to the PDF word it came from, by global sequence alignment.

    The analysed text and the PDF's own text differ slightly (hyphenation, removed headers, equation
    glyphs), so searching phrases page by page misses many lines. Aligning the two token streams once
    is tolerant to all of that and lets us shade whole sentences contiguously.
    Returns {paragraph: [(char_start, word_index_or_None), ...]}.
    """
    doc_tokens = []                                   # (token, para, char_start)
    for i, t in enumerate(texts):
        for m in _TOKEN_RE.finditer(t):
            doc_tokens.append((_tok(m.group()), i, m.start()))
    sm = difflib.SequenceMatcher(None, [t for t, _, _ in doc_tokens], [t for t, _ in pdf_tokens], autojunk=False)
    mapped = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            mapped[a + k] = pdf_tokens[b + k][1]
    out = defaultdict(list)
    for k, (_t, i, start) in enumerate(doc_tokens):
        out[i].append((start, mapped.get(k)))
    return out


def _shade_pdf_pages(body_pdf, dest_page, offsets, texts, plan: Plan, ocr_pages=()):
    """Draw contiguous, line-by-line highlights on copies of the ORIGINAL pages (nothing is re-typeset)."""
    import pymupdf
    words, line_box, pdf_tokens = _pdf_word_stream(body_pdf, ocr_pages)
    if not words:
        return 0, 0
    aligned = _align_paragraphs(texts, pdf_tokens)
    tint = {}                                         # word index -> "strong" | "soft"
    total = matched = 0
    for ranges, level in ((plan.soft_flags, "soft"), (plan.flags, "strong")):   # strong wins
        for i, spans in ranges.items():
            for a, b in spans:
                ids = []
                for start, wid in aligned.get(i, ()):
                    if a <= start < b:
                        total += 1
                        if wid is not None:
                            matched += 1
                            ids.append(wid)
                ids = sorted(set(ids))
                if not ids:
                    continue
                filled = set(ids)
                for p, q in zip(ids, ids[1:]):       # bridge inline maths / stray glyphs inside a sentence
                    if 1 < q - p <= 14 and words[p][0] == words[q][0]:
                        filled.update(range(p + 1, q))
                for wid in filled:
                    tint[wid] = level
    colours = {"strong": ((0.23, 0.79, 0.96), 0.38), "soft": ((0.50, 0.84, 0.94), 0.30)}
    runs = []                                         # merge neighbouring words of one line & one tint
    for wid in sorted(tint):
        pno, x0, _y0, x1, _y1, key = words[wid]
        if runs and runs[-1]["key"] == key and runs[-1]["level"] == tint[wid] and runs[-1]["last"] == wid - 1:
            runs[-1]["x1"] = x1
            runs[-1]["last"] = wid
        else:
            runs.append({"page": pno, "key": key, "level": tint[wid], "x0": x0, "x1": x1, "last": wid})
    for r in runs:
        y0, y1 = line_box[r["key"]]
        colour, opacity = colours[r["level"]]
        sc, dx, dy = offsets[r["page"]]
        dest_page(r["page"]).draw_rect(
            pymupdf.Rect((r["x0"] - 1.0) * sc + dx, (y0 - 0.6) * sc + dy, (r["x1"] + 1.0) * sc + dx, (y1 + 0.6) * sc + dy),
            color=None, fill=colour, fill_opacity=opacity, overlay=True)
    return matched, total


def _content_box(page):
    """Bounding box of everything printed on a source page (text, drawings, pictures)."""
    import pymupdf
    box = pymupdf.Rect()
    for b in page.get_text("blocks"):
        if str(b[4]).strip() or b[6] == 1:
            box |= pymupdf.Rect(b[:4])
    try:
        for d in page.get_drawings():
            r = d.get("rect")
            if r is not None and r.width < page.rect.width * 0.98 and r.height < page.rect.height * 0.98:
                box |= r
    except Exception:
        pass
    return box


def plan_from_segmentation(rep, paras) -> Plan | None:
    """v2.4: shading = the document index, attributed paragraph by paragraph by the engines.

    The index is the estimated share of generated words (segment_signals). Each prose unit carries its own
    probability of being generated, computed from its own wording, its own slips / paste traces and its
    neighbours. Whole paragraphs are shaded in order of that probability until the shaded words equal the index;
    the last paragraph is completed sentence by sentence (whole sentences only) to land as close as possible.
    No supplementary / illustrative tint: every shaded word is a paragraph the engines attribute to generation."""
    seg = getattr(rep, "segmentation", None)
    if seg is None or not seg.units:
        return None
    eng = ba.get_engine()
    plan = Plan()
    units = []
    for k, u in enumerate(seg.units):
        idx = [i for i in u.idx if 0 <= i < len(paras) and paras[i].text.strip()]
        if not idx:
            continue
        w = 0
        for i in idx:
            n = len(eng.WORD_RE.findall(paras[i].text))
            plan.per_para[i] = (n, 0)
            w += n
        units.append((u.post, k, idx, w))
    plan.qualifying_words = sum(w for *_x, w in units)
    plan.qualifying_sentences = sum(len(eng.split_sentences(paras[i].text)) for *_x, idx, _w in units for i in idx)
    if plan.qualifying_words < MIN_WORDS:
        plan.sufficient = False
        return plan
    plan.engine_index = max(0.0, min(100.0, float(rep.score)))
    plan.target_words = int(plan.qualifying_words * plan.engine_index / 100.0 + 0.5)
    left = plan.target_words
    counts = defaultdict(int)
    flags = defaultdict(list)
    sents = 0
    for post, _k, idx, w in sorted(units, key=lambda x: (-x[0], x[1])):
        if left <= 0:
            break
        for i in idx:
            if left <= 0:
                break
            text = paras[i].text
            spans = [(a, b, len(eng.WORD_RE.findall(text[a:b]))) for a, b in eng.split_sentences(text)]
            spans = [x for x in spans if x[2]]
            n = sum(x[2] for x in spans)
            if n <= left:                                  # the whole paragraph
                if spans:
                    flags[i].append((spans[0][0], spans[-1][1]))
                counts[i] += n
                sents += len(spans)
                left -= n
                continue
            for a, b, m in spans:                          # last paragraph: whole sentences, nearest to the index
                if left <= 0 or m - left > left:
                    break
                flags[i].append((a, b))
                counts[i] += m
                sents += 1
                left -= m
            left = 0
    plan.flags = {i: _merge_ranges(v) for i, v in flags.items()}
    plan.flagged_words = sum(counts.values())
    plan.flagged_sentences = sents
    plan.per_para = {i: (w, counts.get(i, 0)) for i, (w, _f) in plan.per_para.items()}
    return plan


def make_plan(rep, paras) -> Plan:
    plan = plan_from_segmentation(rep, paras)
    if plan is not None:
        return plan
    return balance_to_engine_index(rep, paras, plan_highlights(rep, paras))


def compute_plan(rep, *, filename=None, data=None, text=None) -> Plan:
    """Same plan the PDF uses, without any conversion; lets the UI show the identical percentage."""
    paras, _kind, _mm = _paras_for(rep, filename, data, text)
    return make_plan(rep, paras)


def build_report_pdf(rep, *, filename=None, data=None, text=None, color=HIGHLIGHT_HEX) -> bytes:
    """Main entry. Exactly one of (filename+data) or text must describe the analysed source."""
    import pymupdf
    if text is None and (filename is None or data is None):
        raise ValueError("يلزم الملف الأصلي أو النص الأصلي لإنشاء التقرير.")
    paras, kind, mm = _paras_for(rep, filename, data, text)
    plan = make_plan(rep, paras)
    orig_texts, shade = _source_shading(rep, paras, plan, mm)   # ranges expressed on the SOURCE paragraphs

    if text is not None:
        raw = text.encode("utf-8")
        name = rep.source or "نص ملصق"
    else:
        raw = data
        name = ba.filename_only(filename)
    sha = hashlib.sha256(raw).hexdigest()

    if kind == "pdf":
        # Keep the ORIGINAL PDF pages intact (layout, tables, shapes, images) and
        # overlay highlights by locating the flagged phrases on the real pages,
        # instead of regenerating a plain-text document.
        from pdf_input import extract_pdf
        pdf_extraction = extract_pdf(data)
        para_pages = pdf_extraction.para_pages
        ocr_pages = pdf_extraction.ocr_pages
        body_pdf = pymupdf.open(stream=data, filetype="pdf")
        removed = 0
    else:
        if kind == "docx":
            base_docx = data
        else:
            base_docx = build_plain_docx(orig_texts)
        shaded_docx, removed = apply_highlights(base_docx, shade, orig_texts, color)
        body_pdf = pymupdf.open("pdf", docx_to_pdf(shaded_docx))

    eng = ba.get_engine()
    meta = {"name": name, "sha": sha, "src_pages": len(body_pdf),
            "date": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "engine_version": eng.VERSION, "engine_sha": ba.ENGINE_SHA256, "removed_highlights": removed}

    out = pymupdf.open()
    _cover(out, rep, plan, meta, None)
    offsets = {}
    for i in range(len(body_pdf)):
        src = body_pdf[i]
        w, h = src.rect.width, src.rect.height
        pg = out.new_page(width=w, height=h)            # identical page size: a true copy of the source page
        box = _content_box(src)
        crowded = (not box.is_empty) and (box.y0 < TOP_BAND + 2 or box.y1 > h - BOTTOM_BAND - 2)
        if crowded:                                     # only when a page prints right up to its edge
            sc = (h - TOP_BAND - BOTTOM_BAND) / h
            dx, dy = (w - w * sc) / 2, TOP_BAND
        else:
            sc, dx, dy = 1.0, 0.0, 0.0
        pg.show_pdf_page(pymupdf.Rect(dx, dy, dx + w * sc, dy + h * sc), body_pdf, i)
        offsets[i] = (sc, dx, dy)
    if kind == "pdf":
        _shade_pdf_pages(body_pdf, lambda n: out[n + 1], offsets, orig_texts, shade, ocr_pages)
    total = len(out)

    reg = str(_font_dir() / "NebrasPlex-Regular.ttf")
    bold = str(_font_dir() / "NebrasPlex-Bold.ttf")
    fR = pymupdf.Font(fontfile=reg)
    logo = _logo_pdf()
    for n in range(total):
        pg = out[n]
        w, h = pg.rect.width, pg.rect.height
        pg.insert_font(fontname="NPR", fontfile=reg)
        pg.insert_font(fontname="NPB", fontfile=bold)
        footer_y = h - BOTTOM_BAND
        if n >= 1:     # cover page carries its own header; every other page gets the header band
            pg.draw_rect(pymupdf.Rect(0, 0, w, TOP_BAND), color=None, fill=(0.965, 0.972, 0.985))
            pg.draw_line((0, TOP_BAND), (w, TOP_BAND), color=LINE, width=0.7)
            pg.show_pdf_page(pymupdf.Rect(14, 5, 14 + 24, 5 + 24), logo, 0)
            pg.insert_text((44, 15), PROGRAM_NAME, fontname="NPB", fontsize=9.5, color=INK)
            pg.insert_text((44, 26), "SHA-256  " + sha, fontname="helv", fontsize=5.8, color=MUTED)
            lab = "AI Writing Detection Report"
            tw = fR.text_length(lab, fontsize=8)
            pg.insert_text((w - 14 - tw, 20), lab, fontname="NPR", fontsize=8, color=MUTED)
            pg.draw_line((0, footer_y), (w, footer_y), color=LINE, width=0.7)
        else:
            pg.draw_line((42, h - 36), (w - 42, h - 36), color=LINE, width=0.7)
        label = "Page %d of %d" % (n + 1, total)
        tw = fR.text_length(label, fontsize=9)
        pg.insert_text(((w - tw) / 2, h - 14 if n == 0 else footer_y + 16), label, fontname="NPR", fontsize=9, color=INK)
        if n >= 1:
            pg.insert_text((14, footer_y + 16), "ID " + sha[:12].upper(), fontname="helv", fontsize=6.5, color=MUTED)
            pg.insert_text((w - 14 - fR.text_length(PROGRAM_NAME, fontsize=7.5), footer_y + 16), PROGRAM_NAME, fontname="NPR", fontsize=7.5, color=MUTED)
    out.set_metadata({"title": "%s — %s" % (PROGRAM_NAME, name), "author": PROGRAM_NAME,
                      "subject": "AI Writing Detection Report", "keywords": "SHA-256 " + sha})
    buf = out.tobytes(deflate=True, garbage=3)
    out.close()
    body_pdf.close()
    return buf
