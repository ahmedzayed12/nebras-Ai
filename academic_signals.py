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


# key: (Arabic label, weight, z=0 level (~ human p95), z=+1 level (clearly LLM), unit)
SPEC = {
    "lex":    ("مفردات أكاديمية مولَّدة (crucial, framework, ensure, interplay…)", 1.4, 8.0, 14.0, "لكل 1000 كلمة"),
    "tri":    ("ثلاثيات متوازنة «A, B, and C»", 1.0, 3.8, 10.0, "لكل 1000 كلمة"),
    "long_w": ("كثافة الكلمات الطويلة (≥ 10 أحرف)", 0.8, 13.8, 17.0, "% من الكلمات"),
}
MIN_WORDS = 800
SCORE_OFFSET = 1.2
SCORE_SCALE = 1.6


def measure(paragraphs) -> dict:
    paras = [str(p) for p in paragraphs if str(p).strip() and not _REFLINE.match(str(p))]
    text = "\n".join(paras)
    words = _W.findall(text)
    n = len(words)
    k = max(1.0, n / 1000.0)
    return {
        "words": n,
        "lex": len(_LEX.findall(text)) / k,
        "tri": len(_TRI.findall(text)) / k,
        "long_w": 100.0 * sum(1 for w in words if len(w) >= 10) / max(1, n),
        "first": len(_FIRST.findall(text)) / k,
    }


def analyze(paragraphs) -> AcadLayer:
    m = measure(paragraphs)
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
    conf = min(1.0, m["words"] / 3000.0) * (1.0 if fired >= 3 else 0.0)
    notes = ["اجتمعت %d بصمات أسلوبية مميِّزة للنثر الأكاديمي المولَّد." % fired] if fired >= 3 else []
    return AcadLayer(score, conf, fired, m["words"], sigs, notes)


def para_score(text: str) -> Optional[float]:
    """0-100 paragraph indicator from the same habits (only used to decide which passages get shaded)."""
    words = _W.findall(text)
    n = len(words)
    if n < 40:
        return None
    k = n / 1000.0
    lex = len(_LEX.findall(text)) / k
    tri = len(_TRI.findall(text)) / k
    lw = 100.0 * sum(1 for w in words if len(w) >= 10) / n
    x = max(0.0, (lex - 8.0) / 6.0) * 1.4 + max(0.0, (tri - 3.8) / 6.2) * 0.6 + max(0.0, (lw - 13.8) / 3.2) * 0.8
    return round(100.0 * (1 - math.exp(-x / 1.2)), 0)


def fuse(engine_score: float, layer: Optional[AcadLayer]) -> float:
    """Never lowers the engine. Lifts it toward the layer only when >= 3 habits converge."""
    if layer is None or layer.score is None or layer.confidence <= 0 or layer.score <= engine_score:
        return engine_score
    w = 0.9 * layer.confidence
    return engine_score + w * (layer.score - engine_score)
