# -*- coding: utf-8 -*-
"""Structure-aware cleaning for DOCX manuscripts (the Word counterpart of pdf_input.py's cleaning).

WHY: the unchanged engine reads a .docx paragraph-by-paragraph and also judges *formatting* (font, size,
alignment, spacing, Shift+Enter ...).  In a scientific manuscript many paragraphs are not the author's prose and
are formatted differently by design:

  * display equations (Cambria Math, centred, own spacing)      -> look like "manual formatting inconsistency"
  * source-code blocks (Consolas, line breaks inside a paragraph) -> look like "Shift+Enter" and typing errors
  * title / author / affiliation / e-mail block                  -> small centred text = "inconsistent formatting"
  * table cells                                                  -> short fragments = fake sentence-length variance

On the sample manuscript this produced a false "human formatting" signal of 0.92 and dropped the score to 10 %,
while the same text exported to PDF (where pdf_input already removes all of the above) scored 50 %.
This module removes those non-prose paragraphs BEFORE the engine sees them.  It changes no score, weight or threshold.
"""
from __future__ import annotations

import re
import zipfile
import io
from dataclasses import dataclass, field

_MATH_FONTS = ("cambria math", "stix two math", "stix math", "latin modern math", "asana math", "xits math")
_MONO_FONTS = ("consolas", "courier", "menlo", "monaco", "lucida console", "source code", "fira code",
               "fira mono", "dejavu sans mono", "cascadia", "liberation mono", "roboto mono", "inconsolata")
_CODE_LINE = re.compile(r"^\s*(?:def |class |import |from \S+ import |for .+:|if .+:|return\b|print\(|#\s|@\w+|\w+\s*=\s*\S)")
_AFFIL = re.compile(r"(?i)\b(?:department|university|faculty|college|institute|school of|e-?mail|correspondence|"
                    r"orcid|@\w+\.\w+)\b|@")
# Paragraphs a journal template formats differently BY DESIGN (not a sign of manual formatting):
# theorem-like environments, listings/algorithms, MSC/keywords lines, the highlighted "study scope" box.
_STRUCTURAL = re.compile(r"(?i)^\s*(?:listing|algorithm|theorem|lemma|proposition|corollary|definition|remark|proof|example|"
                         r"assumption|claim|conjecture|notation|study\s+scope|keywords?|2020\s+mathematics\s+subject|"
                         r"msc|jel\s+classification|pacs|highlights?|input|output|"
                         r"(?:figure|fig\.?|table|chart|scheme|equation|appendix|الشكل|شكل|الجدول|جدول)\s*\(?\s*[A-Z]?\d+|"
                         r"ethics\s+approval|consent\s+for|competing\s+interests?|conflicts?\s+of\s+interest|funding|data\s+availability|"
                         r"author\s+contributions?|acknowledg(?:e)?ments?|code\s+availability|availability\s+of)\b")
# A display formula typed as plain text with its number at the end: "Occurrence (%) = (a / b) x 100     (3-1)"
_EQ_LINE = re.compile(r"=.*\S\s*\(\s*\d+(?:[-.\u2013]\d+)*\s*\)\s*$")
_SENT_END = re.compile(r"[.!?؟…][\"'”’»)\]]*\s*$")
_ABSTRACT = re.compile(r"(?i)^\s*(?:abstract|summary|ملخص|المستخلص|الملخص)\s*:?\s*$")
# Many journal templates (MDPI, Springer, Elsevier pre-prints) run the heading into the paragraph: "Abstract. We ..." /
# "Abstract: ..." / "Abstract— ...". Only the standalone form was recognised, so the title / author / affiliation block
# of such papers stayed in the analysis and its small centred lines were read as "manual formatting inconsistency".
_ABSTRACT_RUNIN = re.compile(r"(?i)^\s*(?:abstract|summary|ملخص|المستخلص|الملخص)\s*[.:\-–—]\s*\S")
_GENERATORS = ("python-docx", "un-named", "unnamed", "openpyxl", "pandoc", "docx4j", "aspose", "apache poi",
               "chatgpt", "openai", "claude", "gemini", "copilot", "anthropic", "docxtemplater", "pypandoc")


@dataclass
class CleanStats:
    equations: int = 0
    code: int = 0
    front: int = 0
    table_cells: int = 0
    kept_tables: int = 0
    normalised: int = 0        # structural paragraphs whose template-driven formatting was neutralised
    trimmed: int = 0           # paragraphs whose layout padding (leading/trailing spaces, tabs) was trimmed
    fonts_unified: int = 0     # runs whose theme-font alias was resolved to the real family name
    headings_restored: int = 0  # prose paragraphs carrying a Heading style (converted / template artefact) given back as prose
    notes: list = field(default_factory=list)

    @property
    def removed(self) -> int:
        return self.equations + self.code + self.front + self.table_cells


def _share(p, fonts) -> float:
    tot = hit = 0
    for (a, b, font, *_r) in p.runs:
        seg = p.text[a:b]
        n = len(seg.replace(" ", "").replace("\t", ""))
        tot += n
        if any(str(font).lower().startswith(f) or f in str(font).lower() for f in fonts):
            hit += n
    return hit / tot if tot else 0.0


def _is_code(p) -> bool:
    t = p.text.strip()
    if not t:
        return False
    if _share(p, _MONO_FONTS) >= 0.6:
        return True
    # code pasted without a monospace font: several code-looking lines joined by hard breaks
    if p.hard_breaks >= 2 and (bool(_CODE_LINE.match(t)) or re.match(r"(?i)^(?:input|output|algorithm|step\s*\d)\b", t)):
        return True
    return False


def _is_equation(p) -> bool:
    from math_guard import is_display_math_line
    return bool(p.text.strip()) and _share(p, _MATH_FONTS) >= 0.5 and is_display_math_line(p.text)


def _front_matter_ids(paras) -> set:
    """Title / authors / affiliations / e-mails: everything before the Abstract heading that is not real prose. Without an
    Abstract (a thesis chapter), the title lines and the spacing paragraphs that precede the first real paragraph.
    Blank paragraphs in this region are cover-page layout: they say nothing about who wrote the prose."""
    ab = next((i for i, p in enumerate(paras) if _ABSTRACT.match(p.text.strip())), None)
    if ab is None:
        ab = next((i for i, p in enumerate(paras[:40]) if _ABSTRACT_RUNIN.match(p.text)), None)
    chapter = ab is None
    if chapter:
        ab = next((i for i, p in enumerate(paras) if not p.in_table and len(p.text.split()) >= 25), None)
        if ab is None:
            return set()
    out = set()
    for i in range(ab):
        p = paras[i]
        t = p.text.strip()
        if p.has_drawing or p.in_table:
            continue
        if not t:
            out.add(i)
            continue
        words = len(t.split())
        if chapter:
            if words < 25 and not p.is_heading and not _SENT_END.search(t):
                out.add(i)       # an unfinished short line before the first paragraph is a title line, not a sentence
            continue
        if _AFFIL.search(t) or words < 25 or p.is_heading:
            out.add(i)
    return out


def _table_cell_ids(paras) -> tuple:
    """Short unfinished table cells are labels/numbers, not prose. Full sentences inside a table stay."""
    drop, keep = set(), 0
    for i, p in enumerate(paras):
        if not p.in_table:
            continue
        t = p.text.strip()
        if not t:
            continue
        if len(t.split()) <= 12 and not _SENT_END.search(t):
            drop.add(i)
        else:
            keep += 1
    return drop, keep


def _normalise_structural(paras) -> int:
    """Give theorem/listing/keyword/scope paragraphs the dominant body formatting (text is kept: a proof IS prose)."""
    from collections import Counter
    body = [p for p in paras if not p.in_table and len(p.text.strip()) >= 150 and not _STRUCTURAL.match(p.text)]
    if len(body) < 8:
        return 0
    dom = lambda f: Counter(f(p) for p in body).most_common(1)[0][0]
    d_align, d_sp, d_first = dom(lambda p: p.align), dom(lambda p: (p.after, p.before, p.line)), dom(lambda p: p.first)
    sizes = Counter()
    for p in body:
        for (a, b, font, size, *_r) in p.runs:
            sizes[size] += len(p.text[a:b].strip())
    d_size = sizes.most_common(1)[0][0] if sizes else None
    n = 0
    for p in paras:
        if p.in_table or not _STRUCTURAL.match(p.text):
            continue
        p.align = d_align
        p.after, p.before, p.line = d_sp
        p.first = d_first
        if d_size:
            p.runs = [(a, b, font, d_size, bold, it) for (a, b, font, size, bold, it) in p.runs]
        n += 1
    return n


_PAD = " \t\u00a0\u3000\u2002\u2003\u2009\u200b\ufeff"


def _trim_layout_padding(paras, mm=None) -> int:
    """Remove spaces / tabs / NBSP typed at the START or END of a paragraph.

    WHY: the engine counts them as human typing slips ("leading_space" 0.8, "trailing_space" 0.5 per hit) and they
    feed `err_density`, the strongest human-side feature.  In real manuscripts they are *layout padding* (a first-line
    indent made with the space bar, a trailing space left by copy/paste or by pressing Space before Enter) and say
    nothing about who wrote the sentences.  On the Chapter-Two test file 281 + 74 such hits produced 11.9 "errors" per
    1000 words and pushed a text whose paragraph map was 88 % AI down to 7 %.  Only the padding is removed; the words,
    their order and every other check (double spaces INSIDE a sentence, missing spaces after commas, typos...) are
    untouched.  `mm.pos_maps` is composed so the PDF report still shades the ORIGINAL characters.
    """
    n = 0
    for k, p in enumerate(paras):
        t = p.text
        if not t or getattr(p, "is_list", False) or not t.strip():
            continue
        lead = len(t) - len(t.lstrip(_PAD))
        end = len(t.rstrip(_PAD))
        if lead == 0 and end == len(t):
            continue
        new = t[lead:end]
        if mm is not None and k < len(mm.pos_maps):
            old = mm.pos_maps[k]
            mm.pos_maps[k] = list(range(lead, end)) if old is None else old[lead:end]
        p.runs = [(max(a, lead) - lead, min(b, end) - lead, *rest) for (a, b, *rest) in p.runs
                  if min(b, end) > max(a, lead)]
        p.text = new
        n += 1
    return n


_THEME_FONT = re.compile(r"theme:(major|minor)(Bidi|HAnsi|Ascii|EastAsia)", re.I)


def theme_font_table(content: bytes) -> dict:
    """{'theme:majorBidi': 'Times New Roman', ...} read from word/theme/theme1.xml (empty when unavailable)."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            name = next((n for n in z.namelist() if re.match(r"word/theme/theme\d*\.xml$", n)), None)
            xml = z.read(name).decode("utf-8", "ignore") if name else ""
    except Exception:
        return {}
    out = {}
    for grp in ("major", "minor"):
        m = re.search(r"<a:%sFont>(.*?)</a:%sFont>" % (grp, grp), xml, re.S)
        if not m:
            continue
        body = m.group(1)
        def face(tag):
            mm_ = re.search(r'<a:%s typeface="([^"]*)"' % tag, body)
            return (mm_.group(1) if mm_ else "").strip()
        arab = re.search(r'<a:font script="Arab" typeface="([^"]*)"', body)
        latin, ea, cs = face("latin"), face("ea"), face("cs") or (arab.group(1) if arab else "")
        for key, val in (("Bidi", cs or latin), ("HAnsi", latin), ("Ascii", latin), ("EastAsia", ea or latin)):
            if val:
                out["theme:%s%s" % (grp, key)] = val
    return {k.lower(): v for k, v in out.items()}


def unify_theme_fonts(paras, table: dict) -> int:
    """A font written as theme:majorBidi in one run and as «Times New Roman» in the next is ONE font to the reader.
    The engine compares the raw strings, so it reported 385 'different font' findings (fmt_font = 1.0) on a document
    that is set entirely in Times New Roman.  Resolve the alias through the document's own theme."""
    if not table:
        return 0
    n = 0
    for p in paras:
        new_runs = []
        for r in p.runs:
            a, b, font, *rest = r
            f = table.get(str(font).lower()) if _THEME_FONT.fullmatch(str(font)) else None
            if f:
                n += 1
                r = (a, b, f, *rest)
            new_runs.append(r)
        p.runs = new_runs
    return n


_PROSE_END = re.compile(r"[.!?؟][\"'”’)\]]*\s*$")


def _restore_prose_headings(paras) -> int:
    """A paragraph of >= 25 words that ends like a sentence is prose, whatever style Word gives it. Files converted from
    other formats (or built on a template that styles every opening paragraph) carry such 'Heading 1' paragraphs; the
    engine would skip them as headings, so a fifth of a chapter could go unanalysed."""
    n = 0
    for p in paras:
        t = (p.text or "").strip()
        if getattr(p, "is_heading", False) and len(t.split()) >= 25 and _PROSE_END.search(t):
            p.is_heading = False
            n += 1
    return n


def clean_docx_paras(paras, mm=None, theme_fonts=None):
    """Return (clean_paras, stats).  When `mm` (math_guard.MathMap) is given, its index maps are filtered in step so the
    report can still highlight the ORIGINAL paragraphs."""
    stats = CleanStats()
    stats.headings_restored = _restore_prose_headings(paras)
    drop = set()
    for i, p in enumerate(paras):
        if _is_equation(p) or (not p.in_table and len(p.text.split()) <= 40 and _EQ_LINE.search(p.text.strip())):
            drop.add(i); stats.equations += 1
        elif _is_code(p):
            drop.add(i); stats.code += 1
    front = {i for i in _front_matter_ids(paras) if i not in drop}
    drop |= front; stats.front = len(front)
    cells, stats.kept_tables = _table_cell_ids(paras)
    cells = {i for i in cells if i not in drop}
    drop |= cells; stats.table_cells = len(cells)
    stats.normalised = _normalise_structural(paras)
    eq_idx = {i for i, p in enumerate(paras) if _is_equation(p) or (not p.in_table and len(p.text.split()) <= 40 and _EQ_LINE.search(p.text.strip()))}
    if drop:
        keep = [i for i in range(len(paras)) if i not in drop]
        before_eq = {keep.index(i - 1) for i in eq_idx if (i - 1) in keep}
        paras = [paras[i] for i in keep]
        if mm is not None:
            mm.orig_index = [mm.orig_index[i] for i in keep]
            mm.pos_maps = [mm.pos_maps[i] for i in keep]
        close_sentences_before_dropped_equations(paras, before_eq, mm)
    stats.fonts_unified = unify_theme_fonts(paras, theme_fonts or {})
    stats.trimmed = _trim_layout_padding(paras, mm)
    return paras, stats


def generator_from_description(content: bytes) -> str | None:
    """core.xml <dc:description> / <cp:keywords> often says 'generated by python-docx'; the engine only checks creator."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            core = z.read("docProps/core.xml").decode("utf-8", "ignore")
    except Exception:
        return None
    m = re.search(r"<dc:description[^>]*>([^<]*)</dc:description>", core)
    desc = (m.group(1).strip() if m else "")
    low = desc.lower()
    if desc and any(g in low for g in _GENERATORS):
        return desc
    return None


# ---------------------------------------------------------------------------------------------------------------
# Reference lists numbered by Word itself
# ---------------------------------------------------------------------------------------------------------------
# WHY: when the bibliography is a Word numbered list, the numbers "1. 2. 3." are drawn by Word and are NOT characters
# of the paragraph. The engine matches numbered references ("[12] ...", "12. ...") against in-text citations "[12]";
# with the numbers invisible it falls back to author-year matching, finds nothing, and reports EVERY reference as
# "never cited in the text" (cite_gap = 1.0, weight +2.0 towards AI) although the text cites them as [1]...[24].
# The fix writes back the number Word displays. Only decimal lists, only inside the References section, only when the
# entry does not already start with its own number.
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_REFHEAD = re.compile(r"(?i)^\s*(?:\d+[.)]?\s*)?(?:references?|bibliography|works\s+cited|literature\s+cited|sources|"
                      r"المراجع|المصادر|قائمة\s+المراجع|المراجع\s+والمصادر|مراجع|المصادر\s+والمراجع)\s*:?\s*$")
_HAS_OWN_NUMBER = re.compile(r"^\s*[\[(]?\d{1,3}[\]).:]?\s")


def _numbering_rows(content: bytes):
    """Per paragraph in the engine's order: (numId, ilvl) or None; plus {(numId, ilvl): (numFmt, start)}."""
    try:
        from lxml import etree
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            names = set(z.namelist())
            doc = etree.fromstring(z.read("word/document.xml"), etree.XMLParser(huge_tree=True, resolve_entities=False))
            num = etree.fromstring(z.read("word/numbering.xml")) if "word/numbering.xml" in names else None
            sty = etree.fromstring(z.read("word/styles.xml")) if "word/styles.xml" in names else None
    except Exception:
        return None, {}
    style_num = {}
    if sty is not None:
        for st in sty.iter(_W + "style"):
            np_ = st.find(_W + "pPr/" + _W + "numPr")
            if np_ is not None and np_.find(_W + "numId") is not None:
                il = np_.find(_W + "ilvl")
                style_num[st.get(_W + "styleId")] = (np_.find(_W + "numId").get(_W + "val"),
                                                     int(il.get(_W + "val")) if il is not None else 0)
    fmts = {}
    if num is not None:
        absn = {}
        for a in num.iter(_W + "abstractNum"):
            lv = {}
            for l in a.iter(_W + "lvl"):
                f = l.find(_W + "numFmt"); st_ = l.find(_W + "start")
                lv[int(l.get(_W + "ilvl"))] = (f.get(_W + "val") if f is not None else "decimal",
                                               int(st_.get(_W + "val")) if st_ is not None else 1)
            absn[a.get(_W + "abstractNumId")] = lv
        for n in num.iter(_W + "num"):
            aid = n.find(_W + "abstractNumId")
            lv = dict(absn.get(aid.get(_W + "val") if aid is not None else None, {}))
            for o in n.iter(_W + "lvlOverride"):
                so = o.find(_W + "startOverride")
                il = int(o.get(_W + "ilvl"))
                if so is not None and il in lv:
                    lv[il] = (lv[il][0], int(so.get(_W + "val")))
            for il, v in lv.items():
                fmts[(n.get(_W + "numId"), il)] = v
    rows = []

    def one(par):
        ppr = par.find(_W + "pPr")
        key = None
        if ppr is not None:
            np_ = ppr.find(_W + "numPr")
            if np_ is not None and np_.find(_W + "numId") is not None:
                il = np_.find(_W + "ilvl")
                key = (np_.find(_W + "numId").get(_W + "val"), int(il.get(_W + "val")) if il is not None else 0)
            else:
                ps = ppr.find(_W + "pStyle")
                if ps is not None:
                    key = style_num.get(ps.get(_W + "val"))
        rows.append(None if key is None or key[0] == "0" else key)

    body = doc.find(_W + "body")
    if body is not None:
        for child in body:
            if child.tag == _W + "p":
                one(child)
            elif child.tag in (_W + "tbl", _W + "sdt"):
                for par in child.iter(_W + "p"):
                    one(par)
    return rows, fmts


def restore_reference_numbers(paras, mm, content: bytes, n_orig: int) -> int:
    rows, fmts = _numbering_rows(content)
    if rows is None or len(rows) != n_orig or mm is None:
        return 0
    head = None
    for k, p in enumerate(paras):
        if _REFHEAD.match(p.text or "") and len((p.text or "").strip()) < 40:
            head = k
    if head is None:
        return 0
    plan, counters = [], {}
    for k in range(head + 1, len(paras)):
        key = rows[mm.orig_index[k]]
        if key is None:
            continue
        fmt, start = fmts.get(key, ("decimal", 1))
        if fmt != "decimal":
            continue
        counters[key] = counters.get(key, start - 1) + 1
        if paras[k].text.strip() and not _HAS_OWN_NUMBER.match(paras[k].text):
            plan.append((k, counters[key]))
    if len(plan) < 3:
        return 0
    for k, n in plan:
        p = paras[k]
        pre = "[%d] " % n
        old = mm.pos_maps[k]
        base = old if old is not None else list(range(len(p.text))) + [len(p.text)]
        mm.pos_maps[k] = [base[0] if base else 0] * len(pre) + list(base)
        p.runs = [(a + len(pre), b + len(pre), *rest) for (a, b, *rest) in p.runs]
        p.text = pre + p.text
    return len(plan)


def close_sentences_before_dropped_equations(paras, dropped_after: set, mm=None) -> int:
    """docx_clean drops paragraphs typed in a math font (Cambria Math ...). As in math_guard, a sentence that runs on
    into such a displayed formula ("... we obtain") gets the formula placeholder instead of being reported as a
    paragraph without final punctuation."""
    import math_guard
    n = 0
    for k in sorted(dropped_after):
        if 0 <= k < len(paras) and math_guard.ends_open(paras[k].text):
            old = mm.pos_maps[k] if mm is not None and k < len(mm.pos_maps) else None
            paras[k].text, new_map = math_guard.close_before_display(paras[k].text, old)
            if mm is not None and k < len(mm.pos_maps):
                mm.pos_maps[k] = new_map
            n += 1
    return n
