# -*- coding: utf-8 -*-
"""Presentation (slide-deck) mode for PDF input.

WHY
  The engine is a prose detector: sentence lengths, paragraph rhythm, punctuation. A slide deck has none of that --
  short titles, cards and bullets -- so the engine sees ~0 scorable paragraphs and, worse, reads every bullet without a
  final full stop as a *human punctuation slip* (one 13-slide deck: -1.9 logits, verdict "human 1.5%").

WHAT THIS MODULE DOES
  1. Recognises a deck  -- PDF metadata (Creator / Producer: PowerPoint, Keynote, Impress, Google Slides, Canva ...)
                          plus the shape of the pages (landscape, short text boxes, almost no long paragraphs).
                          A Word-produced PDF is never treated as a deck.
  2. Reads each page as ONE slide with its text boxes (position + text), independently of the prose extractor.
  3. Measures template regularity ACROSS slides -- the signature of generated decks (ChatGPT / Gamma / Genspark ...):
        kicker      a small numbered or all-caps label above the title ("02 · METHODS")
        runin       a "Label: full sentence." summary line ("Key takeaway: ...", "Bottom line: ...")
        cards       the same number of text cards on every content slide
        desc_cv     card descriptions of almost identical length (low coefficient of variation)
        sentences   descriptions written as complete, polished sentences (generated) instead of fragments (human)
        lexicon     LLM excess vocabulary and balanced triads in the slide text (same lists as academic_signals)
  4. In deck mode the engine's "human error" credit for missing full stops / blank lines is removed, because in a
     slide those are layout, not handwriting.

  ONE-SIDED like the other layers: it can only raise the score, and only when >= 3 habits converge.
  Anchors are deliberately conservative; re-calibrate with tools/calibrate_slides.py on your own human / AI decks.
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

DECK_TOOLS = re.compile(r"powerpoint|keynote|impress|google\s*slides|canva|gamma|beautiful\.ai|pitch\.com|prezi|"
                        r"genspark|tome\b|slidesgo|wps\s*presentation|presentation", re.I)
PROSE_TOOLS = re.compile(r"microsoft.{0,4}word|writer|pages|latex|tex\b|pdftex|xetex|luatex|google\s*docs|overleaf", re.I)

_W = re.compile(r"[A-Za-z؀-ۿ][A-Za-z؀-ۿ'\-]*")
_KICKER = re.compile(r"^\s*(?:\d{1,2}\s*[·•|\-–—.:]\s*\S|[A-Z0-9&/·•\-–—,.' ]{4,60}$)")
_RUNIN = re.compile(r"^\s*[A-Z][A-Za-z\-/& ]{2,28}:\s+[A-Z؀-ۿ]")
_SENT_END = re.compile(r"[.!?؟]\s*$")
_PAGE_NO = re.compile(r"^\s*(?:\d{1,3}|page\s*\d+|\d+\s*/\s*\d+|slide\s*\d+)\s*$", re.I)


@dataclass
class Block:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def words(self) -> int:
        return len(_W.findall(self.text))


@dataclass
class Slide:
    page: int
    width: float
    height: float
    blocks: List[Block]
    score: Optional[float] = None
    habits: List[str] = field(default_factory=list)

    @property
    def words(self) -> int:
        return sum(b.words for b in self.blocks)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


@dataclass
class DeckSignal:
    key: str
    label: str
    weight: float
    value: float
    z: float


@dataclass
class DeckLayer:
    is_deck: bool
    reason: str
    tool: str
    slides: List[Slide]
    signals: List[DeckSignal] = field(default_factory=list)
    score: Optional[float] = None
    confidence: float = 0.0
    fired: int = 0
    suspect_share: Optional[float] = None


# ----------------------------------------------------------------------------------------------- reading
def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").replace("­", "")).strip()


def read_pdf(data: bytes):
    """Returns (metadata dict, [Slide]). PyMuPDF in the app; poppler's pdftotext as a fallback for tooling/tests."""
    try:
        import pymupdf
    except Exception:
        pymupdf = None
    if pymupdf is not None:
        doc = pymupdf.open(stream=data, filetype="pdf")
        try:
            meta = {k: (v or "") for k, v in (doc.metadata or {}).items()}
            slides = []
            for n, page in enumerate(doc):
                r = page.rect
                blocks = [Block(b[0], b[1], b[2], b[3], _clean(b[4])) for b in page.get_text("blocks") if len(b) > 6 and b[6] == 0]
                slides.append(Slide(n, r.width, r.height, [b for b in blocks if b.text]))
            return meta, slides
        finally:
            doc.close()
    return _read_with_poppler(data)


def _read_with_poppler(data: bytes):
    if not shutil.which("pdftotext"):
        raise RuntimeError("PyMuPDF (or poppler pdftotext) is required to read slide PDFs")
    import html
    with tempfile.NamedTemporaryFile(suffix=".pdf") as fh:
        fh.write(data)
        fh.flush()
        out = subprocess.run(["pdftotext", "-bbox-layout", fh.name, "-"], capture_output=True, timeout=120).stdout.decode("utf-8", "ignore")
        info = subprocess.run(["pdfinfo", fh.name], capture_output=True, timeout=60).stdout.decode("utf-8", "ignore") \
            if shutil.which("pdfinfo") else ""
    meta = {}
    for line in info.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    slides = []
    for n, pm in enumerate(re.finditer(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', out, re.S)):
        blocks = []
        for bm in re.finditer(r'<block xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</block>', pm.group(3), re.S):
            words = re.findall(r"<word[^>]*>(.*?)</word>", bm.group(5))
            t = _clean(html.unescape(" ".join(words)))
            if t:
                blocks.append(Block(float(bm.group(1)), float(bm.group(2)), float(bm.group(3)), float(bm.group(4)), t))
        slides.append(Slide(n, float(pm.group(1)), float(pm.group(2)), blocks))
    return meta, slides


# ----------------------------------------------------------------------------------------------- detection
def detect(meta: dict, slides: List[Slide]):
    """(is_deck, reason, tool). Metadata is a first hint; page shape must agree (or be unambiguous on its own)."""
    tool = " ".join(str(meta.get(k, "")) for k in ("creator", "producer", "Creator", "Producer")).strip()
    pages = [s for s in slides if s.words > 0]
    if not pages:
        return False, "no text", tool
    landscape = sum(1 for s in pages if s.width > s.height * 1.15) / len(pages)
    blocks = [b for s in pages for b in s.blocks if b.words]
    words = sum(b.words for b in blocks) or 1
    long_share = sum(b.words for b in blocks if b.words >= 40) / words
    med = sorted(b.words for b in blocks)[len(blocks) // 2] if blocks else 0
    per_page = words / len(pages)
    shape = landscape >= 0.7 and long_share <= 0.25 and med <= 14 and per_page <= 220
    by_tool = bool(DECK_TOOLS.search(tool)) and not PROSE_TOOLS.search(tool)
    if PROSE_TOOLS.search(tool) and not DECK_TOOLS.search(tool):
        return False, "PDF أنتجه برنامج نصوص (%s)" % tool, tool
    if by_tool and (shape or (landscape >= 0.7 and long_share <= 0.4)):
        return True, "بيانات الملف (%s) + شكل الصفحات" % tool, tool
    if shape and len(pages) >= 4:
        return True, "شكل الصفحات: %.0f%% عرضية، فقرات طويلة %.0f%%، وسيط الصندوق %d كلمات" % (100 * landscape, 100 * long_share, med), tool
    return False, "ليس عرضًا تقديميًا", tool


# ----------------------------------------------------------------------------------------------- analysis
def _cv(v):
    if len(v) < 3:
        return None
    m = sum(v) / len(v)
    return (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5 / m if m else None


def _content_blocks(s: Slide):
    return [b for b in s.blocks if b.words and not _PAGE_NO.match(b.text)]


def _slide_habits(s: Slide, mode_cards: int, lex_rx, tri_rx):
    blocks = _content_blocks(s)
    top = sorted(blocks, key=lambda b: b.y0)[:2]
    h = []
    if any(b.words <= 8 and _KICKER.match(b.text) for b in top):
        h.append("kicker")
    if any(b.words >= 8 and _RUNIN.match(b.text) for b in blocks):
        h.append("runin")
    desc = [b for b in blocks if 8 <= b.words <= 45]
    if mode_cards >= 2 and len(desc) == mode_cards:
        h.append("cards")
    if len(desc) >= 2 and sum(1 for b in desc if _SENT_END.search(b.text)) / len(desc) >= 0.8:
        h.append("sentences")
    txt = s.text
    n = max(1, len(_W.findall(txt)))
    if (len(lex_rx.findall(txt)) + len(tri_rx.findall(txt))) / n * 100 >= 2.0:
        h.append("lexicon")
    return h


SPEC = {   # key: (label, weight, z=0 level, z=1 level)
    "kicker":    ("عنوان صغير مرقّم/بأحرف كبيرة فوق كل شريحة («02 · METHODS»)", 0.8, 0.35, 0.80),
    "runin":     ("سطر خلاصة بصيغة «تسمية: جملة» في الشرائح (Key takeaway: …)", 1.0, 0.25, 0.70),
    "cards":     ("عدد بطاقات متطابق في الشرائح", 0.7, 0.40, 0.80),
    "desc_cv":   ("أطوال أوصاف البطاقات متقاربة جدًا (تباين منخفض)", 0.8, 0.0, 1.0),
    "sentences": ("أوصاف مكتوبة كجمل تامة مصقولة لا كنقاط مختصرة", 0.6, 0.45, 0.85),
    "lexicon":   ("مفردات وثلاثيات مولَّدة في نص الشرائح", 1.0, 0.25, 0.70),
}
DESC_CV_HUMAN, DESC_CV_AI = 0.65, 0.30     # descriptions: human decks vary a lot, generated cards are near-uniform
MIN_CONTENT_SLIDES = 5
SCORE_OFFSET, SCORE_SCALE = 1.2, 1.6


def analyze(meta: dict, slides: List[Slide]) -> DeckLayer:
    import academic_signals as acad
    is_deck, reason, tool = detect(meta, slides)
    layer = DeckLayer(is_deck, reason, tool, slides)
    if not is_deck:
        return layer
    content = [s for s in slides if len(_content_blocks(s)) >= 3 and s.words >= 15]
    if len(content) < MIN_CONTENT_SLIDES:
        layer.reason += " — شرائح المحتوى أقل من %d، فلا تُقاس البنية" % MIN_CONTENT_SLIDES
        return layer
    card_counts = [len([b for b in _content_blocks(s) if 8 <= b.words <= 45]) for s in content]
    mode_cards = Counter(c for c in card_counts if c >= 2).most_common(1)
    mode_cards = mode_cards[0][0] if mode_cards else 0
    for s in content:
        s.habits = _slide_habits(s, mode_cards, acad._LEX, acad._TRI)
    share = {k: sum(1 for s in content if k in s.habits) / len(content) for k in ("kicker", "runin", "cards", "sentences", "lexicon")}
    desc_lens = [b.words for s in content for b in _content_blocks(s) if 8 <= b.words <= 45]
    cv = _cv(desc_lens) if len(desc_lens) >= 6 else None
    share["desc_cv"] = 0.0 if cv is None else max(0.0, min(1.0, (DESC_CV_HUMAN - cv) / (DESC_CV_HUMAN - DESC_CV_AI)))
    total, fired = 0.0, 0
    for key, (label, w, z0, z1) in SPEC.items():
        v = share[key]
        z = v if key == "desc_cv" else (max(0.0, min(1.0, (v - z0) / (z1 - z0))) if v > z0 else 0.0)
        if key == "desc_cv" and cv is None:
            z = 0.0
        if z >= 0.25:
            fired += 1
        layer.signals.append(DeckSignal(key, label, w, (cv if key == "desc_cv" else v) or 0.0, z if (key != "desc_cv" or cv is not None) else 0.0))
        total += w * z
    layer.fired = fired
    layer.score = 50.0 + 50.0 * math.tanh((total - SCORE_OFFSET) / SCORE_SCALE)
    layer.confidence = min(1.0, len(content) / 10.0) * (1.0 if fired >= 3 else 0.0)
    # per-slide scores: share of the deck's text sitting on slides that carry several habits
    tot = hi = 0
    for s in content:
        x = sum(SPEC[k][1] for k in s.habits)
        s.score = round(100.0 * (1 - math.exp(-max(0.0, x - 0.8) / 1.2)), 0)   # one or two weak habits stay < 55
        tot += s.words
        hi += s.words if s.score >= 55 else 0
    layer.suspect_share = hi / tot if tot else None
    return layer


def fuse(engine_score: float, layer: Optional[DeckLayer]) -> float:
    if layer is None or layer.score is None or layer.confidence <= 0 or layer.score <= engine_score:
        return engine_score
    w = 0.9 * layer.confidence
    return engine_score + w * (layer.score - engine_score)


# Engine features that, inside a slide deck, describe the LAYOUT rather than the writer.
LAYOUT_AS_HUMAN = ("err_density", "fmt_incons")
