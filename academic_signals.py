# -*- coding: utf-8 -*-
"""Academic-prose layer for the Nebras detector (English theses, papers, reports).

WHY
  The v2.1 engine under-reports LLM-drafted *humanities / social-science* academic prose: a thesis written with
  an LLM and then pasted into a university Word template can score near 0%.
  tech_signals only covers engineering prose (Mbps, dB, "yielding ..."), so nothing caught this genre.

CALIBRATION (anchors: z=0 at the human 99th percentile, z=1 near the minimum of the LLM set)
  human: 352 texts written before 2020 -- 88 linguistics monograph chapters (Language Science Press),
         163 Python PEPs, 101 Rust RFCs.
  LLM:   20 academic documents (thesis introduction, literature review, methods, discussion, conclusion) on
         education, public health, climate, business, media and psychology. No user file was used.

   lex     LLM excess vocabulary per 1000 words      human med 1.5, p99 8.1, max 12.8 | LLM min 13.8, med 17.6
   tri     balanced triads "A, B, and C" per 1000     human med 0.7, p99 3.8, max 6.2  | LLM min 10.2, med 14.0
   long_w  % of words >= 10 letters                   human med 8.0, p99 13.8, max 15.3| LLM min 16.9, med 18.1
   voice   no I / we / my / our in >= 2000 words      human med 3.8 per 1000           | LLM 0

  ONE-SIDED: absence of a habit says nothing, and the layer can only RAISE the engine score, and only when at
  least 3 independent habits converge (the same rule tech_signals uses). Re-calibrate the anchors in SPEC with
  your own labelled texts: tools/calibrate_academic.py prints the human false-positive rate for any change.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import List, Optional
from matching_text import matching_text

_W = re.compile(r"[A-Za-z][A-Za-z'\-]*")

# LLM "excess vocabulary" in academic writing, taken from published corpus studies of post-ChatGPT papers
# (Kobak et al. 2024 "Delving into ChatGPT usage in academic writing through excess vocabulary";
#  Liang et al. 2024 "Mapping the increasing use of LLMs in scientific papers"). Not tuned on any user file.
_LEX = re.compile(
    r"\b(?:delv\w+|intricat\w+|pivotal|underscor\w+|showcas\w+|meticulous\w*|commendabl\w+|realms?|notabl[ey]|"
    r"comprehensive\w*|crucial(?:ly)?|enhanc\w+|insights?|additionally|valuable|invaluable|multifaceted|nuanced?|robust\w*|"
    r"frameworks?|foster\w*|landscapes?|emphasi[sz]\w+|align\w*|facilitat\w+|leverag\w+|ensur\w+|holistic\w*|seamless\w*|"
    r"interplay|transformative|navigat\w+|highlight\w*|garner\w*|elucidat\w+|encompass\w*|profound(?:ly)?|groundbreaking|"
    r"unprecedented|strategically|noteworthy|paramount|utiliz\w+|vital|essential(?:ly)?|dynamics?|innovative|"
    r"significantly|tapestry|intersection|underpin\w*)\b", re.I)
_TRI = re.compile(r"\b[\w\-/]+(?:\s[\w\-/]+){0,3},\s+[\w\-/]+(?:\s[\w\-/]+){0,3},\s+(?:and|or)\s+[\w\-/]+")
_FIRST = re.compile(r"\b(?:I|my|me|we|our|us)\b")
_REFLINE = re.compile(r"^\s*(?:\[\d{1,3}\]|[A-Z][A-Za-z'\-]+,\s+(?:[A-Z]\.\s*)+.*\(?(?:19|20)\d\d)")


@dataclass
class AcadSignal:
    key: str
    label: str
    weight: float
    value: float
    unit: str
    z: float


@dataclass
class AcadLayer:
    score: Optional[float]
    confidence: float
    fired: int
    words: int
    signals: List[AcadSignal] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    strength: float = 0.0     # how far the habits CONVERGE (0 none, 1 three or more); independent of text length


# key: (Arabic label, weight, z=0 level (~ human p95), z=+1 level (clearly LLM), unit)
SPEC = {
    "lex":    ("مفردات أكاديمية مولَّدة (crucial, framework, ensure, interplay…)", 1.4, 8.0, 14.0, "لكل 1000 كلمة"),
    "tri":    ("ثلاثيات متوازنة «A, B, and C»", 1.0, 3.8, 10.0, "لكل 1000 كلمة"),
    "long_w": ("كثافة الكلمات الطويلة (≥ 10 أحرف)", 0.8, 13.8, 17.0, "% من الكلمات"),
}
MIN_WORDS = 800
TWO_STRONG = 1.4          # two habits must together be well past the human 99th percentile
SCORE_OFFSET = 1.2
SCORE_SCALE = 1.6


def _stem(w: str) -> str:
    return re.sub(r"(?:ing|ed|es|s|e|ly)$", "", w.lower())


def topic_terms(headings, extra=None) -> frozenset:
    """Stems of the words the author put in the title / headings. A word that NAMES the subject of the document
    ("framework" in a thesis about a framework, "nuances" in a thesis about nuances) is vocabulary the topic forces,
    not a stylistic habit, so it is not counted as LLM 'excess vocabulary'."""
    out = set()
    for h in list(headings or []) + list(extra or []):      # extra: the keywords line, which names the subject too
        for w in _W.findall(str(h)):
            if len(w) >= 5:
                out.add(_stem(w))
    return frozenset(out)


STEM_CAP = 3        # per 1000 words: the LLM signature is MANY different marker words,
                    # while a word repeated dozens of times is the subject of the text ("framework", "robustness")


def _lex_hits(text: str, topic) -> float:
    hits = _LEX.findall(text)
    if topic:
        hits = [h for h in hits if _stem(h) not in topic]
    per = {}
    for h in hits:
        k = _stem(h)
        per[k] = per.get(k, 0) + 1
    # Keep the original short-text cap, but scale it with the density denominator
    # for long documents. A fixed count ceiling diluted identical wording to
    # zero as chapters grew, independently of their actual style.
    cap = STEM_CAP * max(1.0, len(_W.findall(text)) / 1000.0)
    return sum(min(v, cap) for v in per.values())


_TECH_ITEM = re.compile(r"^(?:[A-Z]{2,}[A-Za-z0-9\-/]*|[A-Za-z]*[A-Z][a-z]+[A-Z][A-Za-z0-9]*|[A-Za-z]*\d[\w\-/.]*)$")


def technical_list(s: str) -> bool:
    """A, B, and C that NAMES things instead of making a rhetorical point: acronyms, numbers, product / protocol names or code
    (MQTT, CoAP, and Zigbee; 128, 64, and 32), or proper names / taxa written with capitals inside the sentence
    (Rhizopus, Alternaria and Fusarium; Nepal, Sri Lanka, and Yemen). The subject forces such an enumeration."""
    items = [x.strip() for x in re.split(r",\s+(?:and|or|&)\s+|,\s+|\s+(?:and|or|&)\s+", s) if x.strip()]
    tech = sum(1 for x in items if _TECH_ITEM.match(x.split()[-1].strip(".;:")))
    if tech >= 2:
        return True
    named = sum(1 for x in items[1:] if x[:1].isupper() and not x.isupper())
    return named >= 2


_CLAUSE = re.compile(r"\b(?:because|however|whereas|although|though|while|which|that|but|so|when|if|nor|then|therefore|thus|since|"
                     r"whether|as|where|who|whose|unless|until|yet|either|neither)\b", re.I)


def balanced_triad(s: str) -> bool:
    """The label says BALANCED triad: three short, parallel items. `s` is the loose regex match, so its first item can carry a
    few lead-in words ("to examine how institutional"); only the TAIL of that item, as long as the middle item, is judged.
    A match is not a rhetorical triad when a clause word sits inside it (because, however, whereas ...), when an item itself
    contains and / or ("between healthy and rotted tissue, placed on PDA and incubated"), or when the middle item is long."""
    # the regex match may end in ", and X" or in plain " and X" (no serial comma): normalise to three comma parts
    s = re.sub(r"(?<!,)\s+(and|or)\s+(?=[\w\-/]+\s*$)", r", \1 ", s.strip())
    parts = [x.strip() for x in s.split(",") if x.strip()]
    if len(parts) != 3:
        return False
    first, mid, last = parts
    last = re.sub(r"^(?:and|or)\s+", "", last, flags=re.I)
    mw = mid.split()
    if not mw or len(mw) > 3 or len(last.split()) > 3:
        return False
    tail = " ".join(first.split()[-(len(mw) + 1):])
    for item in (tail, mid, last):
        if _CLAUSE.search(item) or re.search(r"\b(?:and|or)\b", item, re.I):
            return False
    return True


def rhetorical_triad(s: str, topic=None) -> bool:
    if not balanced_triad(s) or technical_list(s):
        return False
    # Named events with dates and first-person accounts describe observations;
    # a three-part grammatical construction alone is not a rhetorical trace.
    if re.search(r"\d", s) or _FIRST.search(s):
        return False
    if topic:
        items = [x.strip() for x in re.split(r",\s+(?:and|or|&)\s+|,\s+|\s+(?:and|or|&)\s+", s) if x.strip()]
        # Two items naming the document's subject describe its domain rather
        # than a rhetorical habit. A generic triad elsewhere still counts.
        if sum(any(_stem(w) in topic for w in _W.findall(item)) for item in items) >= 2:
            return False
    return True


def count_triads(text: str, topic=None) -> int:
    return sum(1 for m in _TRI.findall(matching_text(text)) if rhetorical_triad(m, topic))


def measure(paragraphs, topic=None) -> dict:
    paras = [str(p) for p in paragraphs if str(p).strip() and not _REFLINE.match(str(p))]
    text = "\n".join(paras)
    words = _W.findall(text)
    n = len(words)
    k = max(1.0, n / 1000.0)
    return {
        "words": n,
        "lex": _lex_hits(text, topic) / k,
        "tri": count_triads(text, topic) / k,
        "long_w": 100.0 * sum(1 for w in words if len(w) >= 10) / max(1, n),
        "first": len(_FIRST.findall(text)) / k,
    }


def analyze(paragraphs, topic=None) -> AcadLayer:
    m = measure(paragraphs, topic)
    if m["words"] < MIN_WORDS:
        return AcadLayer(None, 0.0, 0, m["words"], [], ["النص الإنجليزي أقصر من %d كلمة على الطبقة الأكاديمية" % MIN_WORDS])
    sigs, total, fired = [], 0.0, 0
    for key, (label, w, z0, z1, unit) in SPEC.items():
        v = m[key]
        z = max(0.0, min(1.0, (v - z0) / (z1 - z0))) if v > z0 else 0.0
        if z >= 0.25:
            fired += 1
        sigs.append(AcadSignal(key, label, w, v, unit, z))
        total += w * z
    # impersonal voice: supports, never decides (a long text with zero I/we/my/our)
    zv = 1.0 if (m["words"] >= 2000 and m["first"] < 0.3) else 0.0
    sigs.append(AcadSignal("voice", "غياب تام لضمير المتكلم في نص طويل", 0.5, m["first"], "لكل 1000 كلمة", zv))
    total += 0.5 * zv
    if zv and fired >= 2:
        fired += 1
    score = 50.0 + 50.0 * math.tanh((total - SCORE_OFFSET) / SCORE_SCALE)
    # graded, no cliff: 3+ converging habits = full confidence; 2 STRONG habits (combined evidence >= TWO_STRONG)
    # = partial confidence growing with their strength; fewer = nothing.
    if fired >= 3:
        g = 1.0
    elif fired == 2 and total >= TWO_STRONG:
        g = min(0.6, 0.6 * (total - TWO_STRONG + 0.4) / 0.8)
    else:
        g = 0.0
    conf = min(1.0, m["words"] / 3000.0) * g
    notes = ["اجتمعت %d بصمات أسلوبية مميِّزة للنثر الأكاديمي المولَّد." % fired] if fired >= 3 else []
    return AcadLayer(score, conf, fired, m["words"], sigs, notes, g)


def para_score(text: str, topic=None) -> Optional[float]:
    """0-100 paragraph indicator from the same habits (only used to decide which passages get shaded)."""
    words = _W.findall(text)
    n = len(words)
    if n < 40:
        return None
    # Sparse observations in one short paragraph are not stable density
    # estimates. Use the same 150-word damping as the bilingual local layer.
    k = max(n, 150) / 1000.0
    lex = _lex_hits(text, topic) / k
    tri = count_triads(text, topic) / k
    lw = 100.0 * sum(1 for w in words if len(w) >= 10) / n
    if sum((lex > SPEC["lex"][2], tri > SPEC["tri"][2], lw > SPEC["long_w"][2])) < 2:
        return None  # document-level agreement cannot manufacture local evidence
    x = max(0.0, (lex - 8.0) / 6.0) * 1.4 + max(0.0, (tri - 3.8) / 6.2) * 0.6 + max(0.0, (lw - 13.8) / 3.2) * 0.8
    return round(100.0 * (1 - math.exp(-x / 1.2)), 0)


def fuse(engine_score: float, layer: Optional[AcadLayer]) -> float:
    """Never lowers the engine. Lifts it toward the layer only when >= 3 habits converge."""
    if layer is None or layer.score is None or layer.confidence <= 0 or layer.score <= engine_score:
        return engine_score
    w = 0.9 * layer.confidence
    return engine_score + w * (layer.score - engine_score)
