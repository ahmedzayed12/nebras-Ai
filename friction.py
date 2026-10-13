# -*- coding: utf-8 -*-
"""Human-friction rule ("a human always slips").

PRINCIPLE (product rule, not a trained model)
  A person who types thousands of words by hand always leaves slips: a double space, a space before a comma, a comma
  glued to the next word, a repeated word, a typo, a stray empty line, a paragraph whose spacing or alignment was
  changed by hand. A LONG text with (almost) none of them was not typed by a person.

WHAT COUNTS AS A HUMAN SLIP
  text   : space before punctuation, no space after punctuation, double spaces, spaces inside brackets, trailing /
           leading spaces (unless the same indent repeats mechanically), repeated punctuation, unbalanced brackets or
           quotes, sentence starting lower-case, repeated word, common typos, Arabic orthography slips
  format : (Word files) a paragraph whose font / size / alignment / spacing / indent differs from its neighbours
           SCATTERED in the body, an empty paragraph used as spacing between two body paragraphs, Shift+Enter inside a
           paragraph, a heading made by manual bold.
NOT a human slip
  - a bullet or heading without a full stop (layout), straight/curly quote mixtures and mixed digit styles (typical
    of pasted text), and formatting differences that come in large contiguous BLOCKS (pasted from another source).

RULE (v2: never on cleanliness alone)
  Cleanliness STRENGTHENS a suspicion, it never creates one. It acts only on the paragraphs the paragraph map already
  suspects, and only in proportion to (a) how far the wording alone -- without file traces and slips -- is above the
  human range (PROSE_GATE) and (b) how much of the text is suspected (SHARE_GATE). A clean, proofread human text with
  human wording therefore gets no floor; a human paragraph rewritten into an AI text leaves the suspected set or adds
  slips, and the floor drops with it.
  Only texts of MIN_WORDS words or more. The floor of the final index is a CONTINUOUS function of the slip rate
  (slips per 1000 words), so every added or removed slip moves the result -- no fixed steps:
      rate 0.0 -> 90    0.3 -> 80    0.6 -> 70    1.2 -> 50    1.6 -> 25    >= 2.0 -> no floor
  The floor only raises the index; it never lowers it. Applied to Word (.docx) files only: pasted text and PDF
  extraction collapse spaces and drop formatting, so a human text would look slip-free there. Not applied to slide
  decks (bullets are layout). By design the rule changes the score only; it writes nothing into the interface/reports.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

MIN_WORDS = 1500
SUSPECT = 55               # paragraph-map score from which a paragraph counts as suspected
MIN_SUSPECT_WORDS = 1000   # enough suspected text to judge its cleanliness
PROSE_GATE, PROSE_SPAN = 55.0, 25.0   # wording-only AI score: human Word files reach p97 50 / max 60
SHARE_GATE, SHARE_SPAN = 0.35, 0.35   # share of suspected text: no effect below 35 %, full effect from 70 %
TOP, KNEE, END = 90.0, 1.2, 2.0      # floor 90 at zero slips, 50 at KNEE slips/1000, fading to nothing at END


def floor_for(rate: float) -> float:
    """Continuous, strictly decreasing floor: no two different slip rates give the same number."""
    if rate >= END:
        return 0.0
    if rate <= KNEE:
        return TOP - (TOP - 50.0) * rate / KNEE
    return 50.0 * (END - rate) / (END - KNEE)


def strength_for(rate: float) -> str:
    return "قاطع" if rate < 0.3 else "قوي" if rate < 0.6 else "متوسط" if rate < 1.2 else "ضعيف"

TEXT_SLIPS = {"space_before_punct", "no_space_after_punct", "double_space", "bracket_space", "trailing_space",
              "leading_space", "repeat_punct", "unbalanced", "lower_start", "repeated_word", "typo_en", "ar_orth",
              "mixed_punct_script"}
FORMAT_SLIPS = {"fmt_font", "fmt_size", "fmt_align", "fmt_spacing", "fmt_indent"}
ALWAYS_HUMAN_FORMAT = {"fmt_hard_break", "fmt_fake_heading"}


@dataclass
class Friction:
    applied: bool
    words: int
    text_slips: int = 0
    format_slips: int = 0
    rate: Optional[float] = None
    floor: Optional[float] = None
    strength: str = ""
    examples: List[str] = field(default_factory=list)
    note: str = ""
    slip_paras: List[int] = field(default_factory=list)
    format_slips_unplaced: int = 0
    suspect_words: int = 0
    suspect_share: float = 0.0
    suspect_rate: Optional[float] = None
    weight: float = 0.0

    @property
    def slips(self) -> int:
        return self.text_slips + self.format_slips


def _block_index_set(forensics) -> set:
    out = set()
    for a, b, _n in (getattr(forensics, "blocks", None) or []):
        out.update(range(a, b + 1))
    return out


_ATTR = {"fmt_align": lambda p: p.align or "",
         "fmt_spacing": lambda p: (p.after or 0, p.before or 0, p.line or 0),
         "fmt_indent": lambda p: p.first or 0}
_ABBR_BEFORE = re.compile(r"\b(?:spp?|var|subsp|cv|et al|etc|cf|e\.g|i\.e|vs|approx|fig|figs|eq|eqs|no|vol|pp|ca|resp|incl)\.\s+[a-z]$", re.I)


_CAPTION = re.compile(r"^\s*(?:Table|Figure|Fig\.?|Chart|Graph|Plate|Diagram|Equation|Scheme|Box|Appendix|"
                      r"جدول|الجدول|شكل|الشكل|رسم|مخطط|صورة)\s*\(?[\dIVXivx]+(?:[-–.]\d+)*\)?", re.I)


def _is_heading_like(p) -> bool:
    """Headings and table/figure captions follow their own style; their formatting is not a typing slip."""
    t = (p.text or "").strip()
    return bool(_CAPTION.match(t)) or (len(t.split()) <= 15 and not re.search(r"[.!?؟:;]$", t))


_LABEL_TAB = re.compile(r"^\s*\S{1,14}[:.)\]]?\t")      # "RQ1:<tab>…", "(a)<tab>…": a definition/list layout


def _paste_runs(paras, min_run: int = 3) -> dict:
    """For every formatting attribute: body paragraphs whose off-dominant value belongs to a run of >= min_run
    consecutive body paragraphs (headings / empty lines in between are skipped). Such runs are pasted blocks."""
    body = [i for i, p in enumerate(paras) if getattr(p, "role", "") == "body" and (p.text or "").strip()]
    out = {}
    for kind, fn in _ATTR.items():
        vals = [(i, fn(paras[i])) for i in body]
        if not vals:
            out[kind] = set()
            continue
        dom = Counter(v for _, v in vals).most_common(1)[0][0]
        marked, k = set(), 0
        while k < len(vals):
            if vals[k][1] == dom:
                k += 1
                continue
            j = k
            while j + 1 < len(vals) and vals[j + 1][1] == vals[k][1]:
                j += 1
            if j - k + 1 >= min_run:
                marked.update(i for i, _ in vals[k:j + 1])
            k = j + 1
        out[kind] = marked
    return out


def measure(report, paras=None, forensics=None, uniform_indent: bool = False, prose_score: float = 0.0) -> Friction:
    words = int(getattr(report, "words", 0) or 0)
    fr = Friction(applied=False, words=words)
    if words < MIN_WORDS:
        fr.note = "النص أقصر من %d كلمة؛ قاعدة «الإنسان يخطئ» لا تُطبَّق على النصوص القصيرة" % MIN_WORDS
        return fr
    in_block = _block_index_set(forensics)
    runs = _paste_runs(paras) if paras else {}
    pasted = set().union(*runs.values()) if runs else set()   # inside a pasted block, every difference comes from the source
    roles = {i: getattr(p, "role", "body") for i, p in enumerate(paras or [])}
    seen_fmt = set()
    texts = list(getattr(report, "texts", []) or [])
    for f in getattr(report, "findings", []) or []:
        k = f.kind
        if f.para is None or f.para < 0:
            continue
        if k in TEXT_SLIPS:
            if k == "leading_space" and uniform_indent:
                continue
            t = texts[f.para] if 0 <= f.para < len(texts) else ""
            if k == "lower_start" and _ABBR_BEFORE.search(t[max(0, f.start - 12):f.end]):
                continue                                   # "Rhizopus spp. and …" is an abbreviation, not a slip
            if k == "double_space" and len(t[f.start:f.end]) >= 3:
                continue                                   # a long run of spaces is deliberate alignment
            if k == "double_space" and re.match(r"\s*\(\d", t[f.end:f.end + 4]):
                continue                                   # spacing before an equation number "(3-1)"
            fr.text_slips += 1
            fr.slip_paras.append(f.para)
            if len(fr.examples) < 5 and 0 <= f.para < len(texts):
                t = texts[f.para]
                fr.examples.append("%s: «…%s…»" % (k, t[max(0, f.start - 18):f.end + 18].strip()))
            continue
        if paras is None:
            continue
        if k in ALWAYS_HUMAN_FORMAT:
            if k == "fmt_fake_heading" and f.para in pasted:
                continue
            fr.format_slips += 1
            fr.slip_paras.append(f.para)
        elif k in FORMAT_SLIPS:
            if (f.para in in_block or f.para in pasted or roles.get(f.para) != "body" or (f.para, k) in seen_fmt
                    or _is_heading_like(paras[f.para])):
                continue
            if k == "fmt_indent" and _LABEL_TAB.match(paras[f.para].text or ""):
                continue
            seen_fmt.add((f.para, k))
            fr.format_slips += 1
            fr.slip_paras.append(f.para)
        elif k == "fmt_empty_para":
            prev = next((roles.get(j) for j in range(f.para - 1, -1, -1) if (paras[j].text or "").strip()), None)
            nxt = next((roles.get(j) for j in range(f.para + 1, len(paras)) if (paras[j].text or "").strip()), None)
            if prev == "body" and nxt == "body":
                fr.format_slips += 1
                fr.format_slips_unplaced += 1          # an empty line belongs to no paragraph: spread over the text
    fr.applied = True
    fr.rate = fr.slips / (words / 1000.0)
    # ---- paragraph level: only text that the paragraph map already suspects is judged by its cleanliness ----------
    per_para = Counter(fr.slip_paras)
    ps = list(getattr(report, "para_scores", []) or [])
    total = sum(n for _, _, n in ps) or 1
    sus = [(i, n) for i, sc, n in ps if sc >= SUSPECT]
    fr.suspect_words = sum(n for _, n in sus)
    fr.suspect_share = fr.suspect_words / total
    if fr.suspect_words < MIN_SUSPECT_WORDS:
        fr.note = "النص المشتبه به قليل؛ لا تُطبَّق القاعدة"
        return fr
    slips_s = sum(per_para.get(i, 0) for i, _ in sus) + fr.format_slips_unplaced * fr.suspect_share
    fr.suspect_rate = slips_s / (fr.suspect_words / 1000.0)
    f = floor_for(fr.suspect_rate)
    # ---- gates: cleanliness only STRENGTHENS a suspicion that the wording itself already raises -------------------
    w_prose = max(0.0, min(1.0, (prose_score - PROSE_GATE) / PROSE_SPAN))
    w_share = max(0.0, min(1.0, (fr.suspect_share - SHARE_GATE) / SHARE_SPAN))
    fr.weight = w_prose * w_share
    if f > 50.0 and fr.weight > 0:
        fr.floor = round(50.0 + (f - 50.0) * fr.weight, 1)
        fr.strength = strength_for(fr.suspect_rate)
    return fr


def lines(fr: Friction, before: float, after: float) -> List[str]:
    if not fr.applied:
        return []
    head = ("● قاعدة «الإنسان يخطئ»: %d هفوة بشرية فقط في %d كلمة (%.2f لكل 1000؛ نصية %d، تنسيقية متفرقة %d)."
            % (fr.slips, fr.words, fr.rate, fr.text_slips, fr.format_slips))
    if fr.floor is None:
        return [head + " عدد الهفوات طبيعي لكتابة بشرية."]
    out = [head + " نص بهذا الطول بلا هفوات تقريبًا لا يكتبه إنسان بيده — دليل %s؛ الحد الأدنى للمؤشر %.0f%%%s."
           % (fr.strength, fr.floor, (" (المؤشر %.0f ← %.0f)" % (before, after)) if after > before + 0.5 else "")]
    for e in fr.examples[:3]:
        out.append("   الهفوات الموجودة: " + e)
    return out
