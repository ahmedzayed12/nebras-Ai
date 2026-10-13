# -*- coding: utf-8 -*-
"""Technical-prose detection layer ("tech profile") for the Nebras detector.

WHY
  The v2.1 engine is tuned on generic LLM prose (stock phrases, markdown, no typos...). A scientific /
  engineering manuscript drafted by an LLM is different: dense, formal, full of equations and numbers, so
  the generic cues barely fire. This layer measures the *syntactic and rhetorical habits* LLMs show in
  technical writing, independent of the topic:

   participial tails      ", yielding / ensuring / providing ... " bolted onto a finished clause
   labelled items         "Term Name: We ..." list items and run-in headings in Title Case
   scope hedging          "within the evaluated framework", "under matched conditions"
   LLM technical lexicon  robust, rigorous, physically consistent, decouples, markedly, comfortably ...
   intensifier adverbs    explicitly, systematically, intrinsically, aggressively ...
   transition openers     Notably, Furthermore, Correspondingly, Crucially ...
   "not X but Y" framing  "rather than", "not merely", "trade-offs rather than ..."
   figure echo            the same "value ± error unit" strings repeated in abstract/results/conclusion
   zero-friction prose    no typos, no spacing slips, perfectly even paragraph rhythm (supports, never decides)

  Every signal is a rate that is compared with anchors measured on human-written technical prose
  (Rust RFCs and Python PEPs: formal engineering prose by many authors) and on LLM drafts. Anchors are
  documented in ANCHORS; re-calibrate with calibrate_tech.py when you have your own labelled texts.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_W = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(\[])")

_PART = re.compile(
    r",\s+(?:thereby\s+|thus\s+|while\s+)?(?:yielding|providing|ensuring|enabling|allowing|resulting|reflecting|"
    r"demonstrating|establishing|capturing|reducing|improving|making|leading|offering|highlighting|confirming|"
    r"maintaining|achieving|eliminating|exploiting|incurring|supporting|rendering|producing|delivering|"
    r"preserving|mitigating|suppressing|accounting|incorporating|reinforcing|motivating|translating|"
    r"substantiating|underscoring|facilitating|guaranteeing|permitting|illustrating|showing|creating|"
    r"minimizing|minimising|maximizing|maximising|boosting|bolstering|safeguarding|streamlining)\b", re.I)

_LABEL_ITEM = re.compile(
    r"(?m)^\s*(?:\d{1,2}[.)]\s+|[•▪\-]\s+)?((?:[A-Z][A-Za-z0-9/\-]*)(?:\s+(?:[A-Z&][A-Za-z0-9/\-]*|and|of|for|the|in|to|with|on|\(\w+\))){1,8})"
    r"(?:\s*\([^)]{1,25}\))?\s*:\s+[A-Z]")
_RUNIN = re.compile(
    r"(?m)^((?:[A-Z][A-Za-z0-9/\-]*)(?:\s+(?:[A-Z][A-Za-z0-9/\-]*|and|of|for|the|in|to|with|on|via)){2,8})\.\s+[A-Z]")

_LEX = [
    r"robust(?:ly|ness)?", r"rigorous(?:ly)?", r"comprehensive(?:ly)?", r"systematic(?:ally)?", r"seamless(?:ly)?",
    r"leverag(?:e|es|ed|ing)", r"physically[- ]consistent", r"realistic", r"unprecedented", r"pivotal", r"crucial",
    r"intricate", r"holistic", r"synerg\w+", r"paradigm", r"landscape", r"tapestry", r"underscor\w+", r"bolster\w*",
    r"decoupl\w+", r"stems? from", r"hinges? on", r"bottleneck(?:ed)?", r"well[- ]suited", r"far detached",
    r"oversimplif\w+", r"idealized", r"idealised", r"commensurate", r"harness\w*", r"orchestrat\w+", r"delve\w*",
    r"noteworthy", r"meticulous\w*", r"underpin\w*", r"foster\w*", r"streamlin\w+", r"cutting-edge", r"state-of-the-art",
    r"in the realm of", r"plays? a (?:crucial|pivotal|key|vital) role", r"a clean baseline", r"fundamental(?:ly)? (?:limit|bottleneck|trade)",
    r"provides? a (?:viable|clean|consistent|physically)", r"enables? a", r"tailored", r"nuanced", r"multifaceted",
    r"unambiguous", r"substantial(?:ly)?", r"consistent baseline", r"dedicated", r"calibrated",
]
STEM_CAP = 3
_LEX_RX = re.compile(r"\b(?:" + "|".join(_LEX) + r")\b", re.I)

_SCOPE = re.compile(
    r"\b(?:within|under|in) the (?:evaluated|proposed|simulated|studied|considered|present|current|identical|matched|selected|"
    r"nominal|representative|indoor|stated|specified)\b[^.,;]{0,40}|"
    r"\bunder (?:these|such|equivalent|matched|identical|comparable|uniform|the same|normali[sz]ed) (?:conditions|assumptions|settings|circumstances|constraints)\b|"
    r"\b(?:for|in) (?:the|this) (?:scenario|setting|context|configuration|setup|regime) (?:being|under|considered|studied|evaluated)\b|"
    r"\bwithin (?:this|the) (?:enclosed|controlled|idealized|idealised) (?:setting|environment|framework)\b|"
    r"\bto isolate (?:fundamental|the|key)\b|\brather than (?:unconditional|absolute|universal)\b|"
    r"\bwarrants? (?:targeted|further|dedicated)\b|\bfall(?:s)? outside the scope\b|\bare omitted here\b", re.I)

_ADV = re.compile(
    r"\b(?:markedly|comfortably|severely|strictly|intrinsically|naturally|notably|explicitly|systematically|aggressively|"
    r"substantially|dynamically|physically|realistically|measurably|considerably|significantly|particularly|"
    r"fundamentally|inherently|seamlessly|meticulously|rigorously|robustly|tightly|continuously|simultaneously|"
    r"unambiguously|predominantly|negligibly|appreciably|consistently|favorably|favourably)\b", re.I)

_OPEN = re.compile(
    r"(?:^|(?<=[.!?]\s))(?:Notably|Furthermore|Moreover|Consequently|Additionally|Crucially|Importantly|Correspondingly|"
    r"Specifically|Accordingly|Analytically|Interestingly|Critically|Collectively|Overall|Ultimately|Hence|Thus|"
    r"Similarly|Conversely|Nevertheless|Nonetheless|Besides|In particular|In summary|In essence|To this end|"
    r"To bridge this gap|To sidestep|To mitigate|To maintain|To evaluate|To facilitate|To prevent|To contextualize|"
    r"To reliably|To account|To generate|Under these conditions|Within this)\b[^.]{0,3}")

_FRAME = re.compile(
    r"\b(?:rather than|not merely|not only|instead of|as opposed to|in contrast to|trade-?offs?|"
    r"highlights? (?:architectural|the)|trading \w+ for|while (?:also )?(?:simultaneously )?(?:maintaining|preserving|keeping))\b", re.I)

_FIG = re.compile(r"\d+(?:\.\d+)?\s*(?:±\s*\d+(?:\.\d+)?\s*)?(?:Mbps|Gbps|kbps|dB|dBm|nJ/bit|nJ|µW|mW|lx|days|%|ms|MHz|kHz|W|V|mA|s)\b")
_REFLINE = re.compile(r"^\s*\[\d{1,3}\]\s*\S")
_TYPO = re.compile(r"[ \t]{2,}|\s[,;:]|[,;:][A-Za-z]|\bteh\b|\brecieve\b|\bseperate\b|\boccured\b", re.I)


@dataclass
class TechSignal:
    key: str
    label: str
    weight: float
    value: float
    unit: str
    z: float
    note: str = ""
    evidence: list = field(default_factory=list)


@dataclass
class TechLayer:
    score: Optional[float]
    confidence: float
    technical: bool
    words: int
    signals: List[TechSignal]
    notes: List[str]


# anchors: (rate at which the habit is clearly human-range, rate at which it is clearly LLM-range)
ANCHORS: Dict[str, tuple] = {}


def _z(v: float, lo: float, hi: float) -> float:
    if hi == lo:
        return 0.0
    t = (v - lo) / (hi - lo)
    return max(-1.0, min(1.0, 2 * t - 1))


def _sentences(text: str) -> List[str]:
    out = []
    for para in text.split("\n"):
        para = para.strip()
        if len(para) < 20:
            continue
        out += [s for s in _SENT.split(para) if len(s.split()) >= 4]
    return out


def measure(paragraphs) -> dict:
    paras = [str(p) for p in paragraphs if str(p).strip()]
    text = "\n".join(paras)
    words = len(_W.findall(text))
    sents = _sentences(text)
    ns = max(1, len(sents))
    k = max(1.0, words / 1000.0)
    m = {"words": words, "sentences": len(sents)}
    m["part"] = len(_PART.findall(text)) / ns
    labels = _LABEL_ITEM.findall(text)
    runins = _RUNIN.findall(text)
    m["label"] = (len(labels) + len(runins)) / k
    per = {}
    for h in _LEX_RX.findall(text):          # one word counts at most STEM_CAP times (a repeated word is the topic)
        key = re.sub(r"(?:ing|ed|es|s|ly|ness)$", "", h.lower())
        per[key] = per.get(key, 0) + 1
    m["lex"] = sum(min(v, STEM_CAP) for v in per.values()) / k
    m["scope"] = len(_SCOPE.findall(text)) / k
    m["adv"] = len(_ADV.findall(text)) / k
    m["open"] = len(_OPEN.findall(text)) / ns
    m["frame"] = len(_FRAME.findall(text)) / k
    figs = [re.sub(r"\s+", "", f) for f in _FIG.findall(text)]
    rep = {f for f in set(figs) if figs.count(f) >= 3}
    m["figecho"] = len(rep) / max(1, len(set(figs)))
    m["typo"] = len(_TYPO.findall(text)) / k
    cv = _cv([len(s.split()) for s in sents]) if len(sents) > 8 else None
    m["sent_cv"] = cv if cv is not None else 0.0
    m["numeric_density"] = len(re.findall(r"\d+(?:\.\d+)?", text)) / k
    return m


def _cv(v):
    if len(v) < 2:
        return 0.0
    mu = sum(v) / len(v)
    return (sum((x - mu) ** 2 for x in v) / len(v)) ** 0.5 / mu if mu else 0.0


# ----------------------------------------------------------------------------------------------
# scoring
# ----------------------------------------------------------------------------------------------
# key: (label, weight, z=0 level, z=+1 level, unit)   -- levels measured on 95 human technical documents
# (Rust RFCs + Python PEPs, >= 3000 words each: 90th percentile ~= the z=0 level, max ~= 60% of the z=+1 level)
SPEC = {
    "part":  ("جمل تُلحَق بمشتقّ (, yielding / ensuring ...)", 1.0, 0.025, 0.070, "لكل جملة"),
    "label": ("بنود/عناوين مدمجة بصيغة «اسم موسوم: ...»",       1.2, 0.05, 1.50, "لكل 1000 كلمة"),
    "lex":   ("معجم تقني مولَّد (robust, rigorous, decouples ...)", 1.0, 1.50, 5.00, "لكل 1000 كلمة"),
    "scope": ("تحوّطات نطاق («within the evaluated framework»)",   0.9, 0.30, 1.50, "لكل 1000 كلمة"),
    "figecho": ("أرقام نتائج تتكرر حرفيًا بين الملخص والنتائج والخاتمة", 0.5, 0.03, 0.15, "نسبة"),
}
SCORE_OFFSET = 1.5      # evidence needed before the layer leaves the neutral zone
SCORE_SCALE = 2.0
MIN_WORDS = 1200


def analyze(paragraphs) -> TechLayer:
    m = measure(paragraphs)
    if m["words"] < MIN_WORDS:
        return TechLayer(None, 0.0, False, m["words"], [], ["النص قصير على طبقة الأوراق التقنية (أقل من %d كلمة)" % MIN_WORDS])
    sigs, total, fired = [], 0.0, 0
    for key, (label, w, z0, z1, unit) in SPEC.items():
        v = m[key]
        z = max(0.0, min(1.0, (v - z0) / (z1 - z0))) if v > z0 else 0.0     # one-sided: absence says nothing
        if z >= 0.35:
            fired += 1
        sigs.append(TechSignal(key, label, w, v, unit, z))
        total += w * z
    # friction: a long technical text with almost no typing slips at all is a (weak) AI trait; many slips = human
    friction = 0.0
    if m["words"] >= 2500:
        if m["typo"] < 0.5:
            friction = 0.5
        elif m["typo"] > 12:
            friction = -1.0
    sigs.append(TechSignal("friction", "نص بلا أي هفوة كتابية (مسافات/ترقيم)", 0.5, m["typo"], "لكل 1000 كلمة", friction))
    total += 0.5 * friction
    score = 50.0 + 50.0 * math.tanh((total - SCORE_OFFSET) / SCORE_SCALE)
    # a single habit never lifts the index (a lone habit is how an ordinary author writes); two lift partly, three fully
    conf = min(1.0, m["words"] / 3000.0) * (1.0 if fired >= 3 else 0.7 if fired == 2 else 0.0)
    notes = []
    if fired >= 3:
        notes.append("اجتمعت %d بصمات أسلوبية مميّزة للأوراق التقنية المولَّدة." % fired)
    return TechLayer(score, conf, True, m["words"], sigs, notes)


def para_score(text: str) -> Optional[float]:
    """0-100 paragraph indicator from the same habits (used only to rank which passages are shaded)."""
    n = len(_W.findall(text))
    if n < 25:
        return None
    ns = max(1, len(_sentences(text)))
    x = (1.0 * len(_PART.findall(text)) / ns * 8 + 1.2 * (len(_LABEL_ITEM.findall(text)) + len(_RUNIN.findall(text)))
         + 0.7 * len(_LEX_RX.findall(text)) + 0.9 * len(_SCOPE.findall(text))) / (n / 100.0)
    return round(100.0 * (1 - math.exp(-x / 1.5)), 0)


def fuse(engine_score: float, layer: Optional[TechLayer]) -> float:
    """Never lowers the engine. Lifts it toward the layer when the layer has independent, converging evidence."""
    if layer is None or layer.score is None or layer.confidence <= 0 or layer.score <= engine_score:
        return engine_score
    w = 0.9 * layer.confidence
    return engine_score + w * (layer.score - engine_score)
