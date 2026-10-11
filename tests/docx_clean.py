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
                         r"(?:figure|fig\.|table|chart|scheme|equation|appendix|الشكل|شكل|الجدول|جدول)\s*[A-Z]?\d+|"
                         r"ethics\s+approval|consent\s+for|competing\s+interests?|conflicts?\s+of\s+interest|funding|data\s+availability|"
                         r"author\s+contributions?|acknowledg(?:e)?ments?|code\s+availability|availability\s+of)\b")
_SENT_END = re.compile(r"[.!?؟…][\"'”’»)\]]*\s*$")
_ABSTRACT = re.compile(r"(?i)^\s*(?:abstract|summary|ملخص|المستخلص|الملخص)\s*:?\s*$")
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
    return bool(p.text.strip()) and _share(p, _MATH_FONTS) >= 0.5


def _front_matter_ids(paras) -> set:
    """Title / authors / affiliations / e-mails: everything before the Abstract heading that is not real prose."""
    ab = next((i for i, p in enumerate(paras) if _ABSTRACT.match(p.text.strip())), None)
    if ab is None:
        return set()
    out = set()
    for i in range(ab):
        p = paras[i]
        t = p.text.strip()
        if not t or p.has_drawing:
            continue
        words = len(t.split())
        if p.in_table:
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


def clean_docx_paras(paras, mm=None):
    """Return (clean_paras, stats).  When `mm` (math_guard.MathMap) is given, its index maps are filtered in step so the
    report can still highlight the ORIGINAL paragraphs."""
    stats = CleanStats()
    drop = set()
    for i, p in enumerate(paras):
        if _is_equation(p):
            drop.add(i); stats.equations += 1
        elif _is_code(p):
            drop.add(i); stats.code += 1
    front = {i for i in _front_matter_ids(paras) if i not in drop}
    drop |= front; stats.front = len(front)
    cells, stats.kept_tables = _table_cell_ids(paras)
    cells = {i for i in cells if i not in drop}
    drop |= cells; stats.table_cells = len(cells)
    stats.normalised = _normalise_structural(paras)
    if not drop:
        return paras, stats
    keep = [i for i in range(len(paras)) if i not in drop]
    out = [paras[i] for i in keep]
    if mm is not None:
        mm.orig_index = [mm.orig_index[i] for i in keep]
        mm.pos_maps = [mm.pos_maps[i] for i in keep]
    return out, stats


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
