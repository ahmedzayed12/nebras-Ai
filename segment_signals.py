# -*- coding: utf-8 -*-
"""Paragraph-level evidence and segmentation of MIXED documents (v2.5).

WHY
  The engine and the layers before this one judge the document as ONE block. A thesis in which a student wrote some
  chapters and pasted others from a chat window is neither: averaged over the whole file the generated habits are
  diluted by the human chapters, and the Word traces of the human work on the FILE (edit time, revisions, hand
  formatting, mixed quotes) then pull the whole document down to "human". Measured on a labelled bench of Word files
  with 30 / 50 / 70 % generated text, the previous index averaged 21 / 35 / 52 and found 54 % of the generated words.

WHAT THIS LAYER DOES (reasons, not pressure)
  1. Every prose unit (a paragraph, or a run of list items read together) gets its own log-odds from the WORDING
     habits of generated prose. Each habit is anchored on human writing: it contributes nothing below the human 75th
     percentile and reaches full strength only at the human 97th percentile (anchors: pre-2020 linguistics monograph
     chapters and Python PEPs; see tools/calibrate_segments.py). A single habit can therefore never decide alone.
  2. Evidence that belongs to ONE paragraph stays on that paragraph:
       - a human slip typed inside it (typo, space before a comma, missing space, repeated word…)  -> human
       - a chat-window paste trace in its own formatting (ChatGPT text colour, UI font, web autospacing) -> generated
     Evidence that describes the FILE (editing time, revisions, rsid sessions, quote mixtures) is NOT pushed onto the
     paragraphs: it proves that a person worked on the file, not who wrote each paragraph.
  3. Generated text arrives in contiguous blocks (a section, a chapter), so the units are read as a sequence with a
     two-state model (human / generated) whose state rarely switches between neighbouring paragraphs. A paragraph is
     judged together with its neighbours; an isolated "polished" human paragraph inside human text stays human.
  4. The document index is the continuous word-weighted paragraph indicator.
     Thresholded attribution and shading are separate from this heuristic index.

LANGUAGE
  English units are scored by the habits below. Arabic units keep the engine's own paragraph score as their evidence.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

_W = re.compile(r"[A-Za-z][A-Za-z'\-]*")
_AR = re.compile(r"[؀-ۿ]")

# ---------------------------------------------------------------------------------------------------------------
# Wording habits (each one documented as a habit of generated prose in published corpus studies / detector work)
# ---------------------------------------------------------------------------------------------------------------
# Sentence-initial connectives used as paragraph glue
_CONN = re.compile(r"^(?:Furthermore|Moreover|Additionally|In addition|Consequently|Therefore|Nevertheless|Nonetheless|"
                   r"Thus|Hence|As a result|Overall|Ultimately|In contrast|Conversely|Similarly|Likewise|Accordingly|"
                   r"Notably|Importantly|Indeed|At the same time|In particular|Specifically|Finally|In conclusion|"
                   r"In summary|Taken together|To this end|In this regard|In this context|By doing so|In doing so)\b")
_FIRST = re.compile(r"\b(?:I|my|me|mine|myself)\b|\b(?:we|our|us)\b(?! (?:can|could|should|must|need) )")
_NOMIN = re.compile(r"\b[A-Za-z]{4,}(?:tion|tions|ment|ments|ity|ities|ness|ance|ances|ence|ences)\b")
# "clear and compelling", "timely and accurate": coordinated doublets of content words
_DOUBLET = re.compile(r"\b([A-Za-z]{4,})\s+and\s+([A-Za-z]{4,})\b")
_DOUBLET_STOP = {"that", "this", "these", "those", "there", "their", "then", "with", "from", "into", "have", "were",
                 "been", "they", "them", "what", "when", "which", "where", "while", "also", "more", "most", "such"}
# informal / hedging moves of a writer thinking on the page
_HUMAN_HEDGE = re.compile(r"\b(?:perhaps|maybe|of course|actually|really|quite|somewhat|apparently|admittedly|"
                          r"so-called|let us|let's|note that|in fact|that is to say|i\.e\.|e\.g\.|cf\.|viz\.|etc\.)"
                          r"(?=\W|$)", re.I)
# a parenthetical aside that is neither a citation "(Smith, 2019)" nor an acronym definition "(MRI)":
# "(henceforth CT)", "(learning)", "(see Section 2.3)", "(i.e. …)" -- a writer qualifying a thought on the page
_PAREN = re.compile(r"\(([^()]{1,120})\)")
_ACRONYM = re.compile(r"[A-Z][A-Za-z]{0,2}[A-Z]{1,6}s?")
_YEAR = re.compile(r"\b(?:19|20)\d\d[a-z]?\b")
_STATS = re.compile(r"[=<>±%§]|\b[pnNrtFβχ]\s*[=<>]|\d")       # "(R² = .04, p < .05)", "(n = 384)", "(Table 3)" is a reference
# Humanizer spacing: a space character a keyboard does not type, inserted BETWEEN two words of running English prose
# ("cyber\u2002attacks"). "Humanizer" tools sprinkle them (a few per cent of the word gaps) to break word matching.
# Measured on 2254 human source files (LangSci chapters, PEPs, RFCs): 0 such spaces between letters.
# Deliberately NOT counted and NOT cleaned (user decision): zero-width joiners inside words and look-alike Cyrillic
# letters ("Lі\u2060terаture"). That masking is also used against plagiarism checkers, so it says nothing about
# who wrote the words; such a file is read exactly as it is.
_ODD_SPACE = re.compile(r"(?<=[A-Za-z,.;:)])[\u2000-\u200a\u202f\u205f\u3000](?=[A-Za-z(])")
EVASION_L = 0.0          # v2.6: tortured phrases are reported only, they never raise the index
SPACE_L = 1.0            # v2.7: humanizer spacing (user decision): the spaces are cleaned to ordinary ones and the paragraph
                         # that carried them gets one moderate habit. Calibrated to the user's reference for Review (17-18%):
                         # cleaning alone gives 0, 1.0 gives 17.0. Masked letters/zero-width (Ch_pt type) stay untouched.
EVASION_DOC_MIN = 10       # ten or more in one document (none in 2254 human files): then one artefact marks its unit
EVASION_UNIT_MIN = 3       # otherwise a unit needs three of its own


# Synonym-spinner traces ("tortured phrases"): a paraphrasing tool replaces an established technical term word by word
# ("counterfeit consciousness" for artificial intelligence, "irregular woodland" for random forest). Documented by
# Cabanac, Labbé & Magazinov (2021) and the Problematic Paper Screener. 0 hits in 2254 human source files; a single one
# marks its paragraph. Only multi-word substitutions no writer chooses (no single ordinary words).
_TORTURED = [
 r"counterfeit consciousness", r"fake consciousness", r"man-?made consciousness", r"manufactured consciousness",
 r"profound learning (?:model|models|method|methods|algorithm|algorithms|approach|approaches|technique|techniques|architecture|network|networks|framework)",
 r"profound neural organi[sz]ations?", r"(?:counterfeit|fake|artificial|convolutional|repetitive|intermittent) neural organi[sz]ations?",
 r"(?:irregular|arbitrary) (?:woodland|backwoods)", r"colossal information", r"help vector machines?", r"backing vector machines?",
 r"(?:straight|calculated|strategic) relapse", r"k[- ]closest neighbou?rs?", r"(?:slope|angle) (?:plummet|drop|plunge)",
 r"(?:root )?mean squared? mistake", r"flag to commotion", r"sign to clamou?r", r"(?:highlight|include) extraction",
 r"picture handling", r"discourse acknowledg(?:e)?ment", r"(?:normal|regular|characteristic) language (?:handling|preparing)",
 r"(?:cloud|edge|haze) figuring", r"parcel misfortune", r"dark (?:opening|gap) assaults?", r"(?:disavowal|forswearing) of administration",
 r"(?:haphazard|irregular) access memory", r"lion'?s share casting a ballot", r"bosom (?:malignant growth|disease|malignancy)",
 r"(?:kidney|liver) disappointment", r"worldwide situating framework",
]
_TORTURED_RX = re.compile(r"\b(?:" + "|".join(_TORTURED) + r")\b", re.I)


def spinner_count(text: str) -> int:
    return len(_TORTURED_RX.findall(text or ""))


# Scientific notation is not a typing slip: "cobalt(II)", "N-(2-aminobenzylidene)thiourea", "[CoL2(NO3)2]",
# "N,S-donor", "2:1", "[3-5,17-24]". A slip counts only when the token around it is ordinary words.
_SCI_TOKEN = re.compile(r"\d|\((?:I{1,3}|IV|VI{0,3}|V|IX|X)\)|[-\u2013]\(|\)[-\u2013]|^[A-Z][a-z]?\(|\b[A-Z],[A-Z]\b|\b[A-Z]{1,3}[,:][A-Z]{1,3}\b")
SCI_SLIP_KINDS = {"bracket_space", "no_space_after_punct"}


def is_scientific_slip(text: str, start: int) -> bool:
    """True when a spacing 'slip' sits inside a formula, a chemical / IUPAC name, a ratio or a citation range."""
    if not text or start is None or start < 0 or start > len(text):
        return False
    a = text.rfind(" ", 0, start) + 1
    b = text.find(" ", start + 1)
    tok = text[a:b if b >= 0 else len(text)]
    if re.search(r"[A-Za-z]\((?:19|20)\d\d[a-z]?[),;]", tok):     # "Ulysses(1922)", "McCrary(2024)": a real slip
        return False
    return bool(_SCI_TOKEN.search(tok))


# Paragraphs placed in a Word file from OUTSIDE Word (v2.9). Word stamps every paragraph it writes, typed or pasted, with
# revision ids (w:rsid*). A tool that edits the file's XML directly (a script, an agent, a "rewrite my docx" service)
# leaves its paragraphs without any. Only a MIXED file counts: most prose carries Word's stamps and some does not.
# Files with no stamps at all (Google Docs, LibreOffice, python-docx) are left to the metadata layer.
OUTSIDE_L = 1.2                 # same weight as a chat-paste trace; never overrides a paragraph whose own wording is human
OUTSIDE_MIN_MARKED = 20         # the file must really be a Word-written file
OUTSIDE_MIN_MARKED_SHARE = 0.5
OUTSIDE_MIN_UNMARKED = 3
_XML_P = re.compile(r"<w:p[ >].*?</w:p>", re.S)
_XML_T = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>")


def _okey(t: str) -> str:
    return re.sub(r"\s+", "", t or "")[:70]       # spacing-free: Word tabs / run joins differ from the engine's text


def outside_word_texts(docx_bytes) -> frozenset:
    """Keys of prose paragraphs without any Word revision stamp, in a file otherwise stamped by Word."""
    if not docx_bytes:
        return frozenset()
    try:
        import io, zipfile, html as _h
        xml = zipfile.ZipFile(io.BytesIO(docx_bytes)).read("word/document.xml").decode("utf-8", "ignore")
    except Exception:
        return frozenset()
    marked, unmarked = 0, []
    for p in _XML_P.findall(xml):
        t = _h.unescape("".join(_XML_T.findall(p))).strip()
        if len(t.split()) < MIN_UNIT_WORDS:
            continue
        if "w:rsid" in p:
            marked += 1
        else:
            unmarked.append(_okey(t))
    n = marked + len(unmarked)
    if marked < OUTSIDE_MIN_MARKED or len(unmarked) < OUTSIDE_MIN_UNMARKED or marked < OUTSIDE_MIN_MARKED_SHARE * n:
        return frozenset()
    return frozenset(k for k in unmarked if k)


def evasion_count(text: str) -> int:
    return len(_ODD_SPACE.findall(text or ""))


_LABEL_ITEM = re.compile(r"^\s*(?:[\-•*•]\s*)?[A-Z][^:.!?]{1,60}:\s+\S")
_CITE = re.compile(r"\(\s*[A-Z][^()]{1,80}?\b(?:19|20)\d\d[a-z]?\s*\)|\b[A-Z][a-z]+(?: et al\.?)? \((?:19|20)\d\d\)")


# Anchors (z=0 at the human 75th percentile, z=1 at the human 97th percentile of single paragraphs >= 40 words).
# Re-measure them with: python tools/calibrate_segments.py <folder of human .txt files>
# key: (weight, human p75, human p97)
ANCHORS = {
    "lex":      (1.0, 0.00, 1.89),   # LLM excess vocabulary (academic_signals._LEX) per 100 words
    "vocab":    (0.8, 0.00, 0.93),   # engine VOCAB_STRONG (x1) + VOCAB_MED (x0.3) per 100 words
    "tri":      (0.8, 0.00, 0.50),   # share of sentences carrying a balanced triad "A, B, and C"
    "wlen":     (1.2, 5.40, 6.29),   # mean word length (letters)
    "nomin":    (0.8, 5.74, 11.11),  # nominalisations (-tion, -ment, -ity, -ness, -ance, -ence) per 100 words
    "doublet":  (0.6, 1.32, 3.75),   # "X and Y" doublets per 100 words
    "conn":     (0.5, 0.00, 0.33),   # share of sentences opened by a connective
    "generic":  (0.6, 0.00, 2.38),   # engine generic phrasing (GEN_EN) per 100 words
    "phrase":   (0.6, 0.00, 0.93),   # engine AI phrase lexicon per 100 words
    "rhetoric": (0.6, 0.00, 1.28),   # engine contrast / signpost templates per 100 words
}
UNIFORM_W, CV_HI, CV_LO = 0.6, 0.30, 0.15     # sentence lengths inside the paragraph almost identical
FIRST_W, FIRST_P97 = 1.0, 2.9                 # first person (I / my / we / our) per 100 words: human voice
ASIDE_W, ASIDE_P75 = 0.6, 1.7                 # parenthetical asides per 100 words (human p75 1.7, generated p90 0)
HEDGE_W, HEDGE_P97 = 0.6, 2.0
LABEL_LIST_W = 0.6                            # a run of "Label: explanation" items (the chat-answer list layout)
MAX_Z = 1.5

# x -> log-odds: L = ALPHA * (x - X0); X0 = the human 97th percentile of x for single paragraphs
X0, ALPHA, L_CAP = 2.9, 0.9, 3.0
STAY = 0.90                       # probability that the next unit continues the current state (blocks, not confetti)
PRIOR = 0.25
SLIP_L, SLIP_CAP = 0.6, 1.8       # each human slip inside a unit (log-odds), and its ceiling
FMT_SLIP_L = 0.25                 # a scattered hand-format difference on that paragraph
PASTE_L = 1.2                     # chat-window formatting carried by that very paragraph
MIN_UNIT_WORDS = 12
OWN_HUMAN_VETO = 0.0               # attribution requires positive local evidence
CLEAR_HUMAN_L = -1.5              # own evidence at least 1:4.5 for human: document-wide evidence does not move it
SUSPECT = 0.5                     # a unit is attributed to generation when that is more likely than not

TEXT_SLIPS_DOCX = {"space_before_punct", "no_space_after_punct", "double_space", "bracket_space", "trailing_space",
                   "repeat_punct", "unbalanced", "lower_start", "repeated_word", "typo_en", "ar_orth",
                   "mixed_punct_script"}
# pasted text / PDF extraction collapse spaces: only slips that survive copying count there
TEXT_SLIPS_FLAT = {"space_before_punct", "no_space_after_punct", "repeat_punct", "lower_start", "repeated_word",
                   "typo_en", "ar_orth"}
FORMAT_SLIPS = {"fmt_font", "fmt_size", "fmt_align", "fmt_spacing", "fmt_indent", "fmt_hard_break", "fmt_fake_heading"}


@dataclass
class Unit:
    idx: List[int]
    words: int
    x: float = 0.0
    L: float = 0.0
    slips: int = 0
    paste: float = 0.0
    arabic: bool = False
    evasion: int = 0
    spun: int = 0
    tool_marked: bool = False       # carries humanizer spacing (counted as one strong habit)
    outside: bool = False           # placed in the Word file from outside Word (no revision stamp)
    own_L: Optional[float] = None   # the paragraph's OWN evidence: wording, its slips, its paste / tool traces (no file-wide push)
    post: float = 0.0
    habits: Dict[str, float] = field(default_factory=dict)


@dataclass
class Segmentation:
    units: List[Unit] = field(default_factory=list)
    share: float = 0.0              # expected share of generated words
    suspect_share: float = 0.0      # share of words in units with posterior >= 0.5
    words: int = 0
    blocks: List[tuple] = field(default_factory=list)   # (first text idx, last text idx, words) of suspected runs
    para_post: Dict[int, float] = field(default_factory=dict)
    evasion: int = 0                # evasion artefacts found, when they are established across the document
    spun: int = 0                   # synonym-spinner phrases found
    outside: int = 0                # units placed in the Word file from outside Word


def _engine():
    import backend_adapter
    return backend_adapter.get_engine()


def _stem(w: str) -> str:
    return re.sub(r"(?:ing|ed|es|s|e|ly)$", "", w.lower())


def habits(text: str, topic=frozenset()) -> Dict[str, float]:
    """Raw habit measurements of one unit (English)."""
    E = _engine()
    import academic_signals as acad
    ws = _W.findall(text)
    n = max(1, len(ws))
    k = n / 100.0
    sents = [text[a:b] for a, b in E.split_sentences(text)]
    sl = [len(_W.findall(s)) for s in sents if _W.findall(s)]
    ns = max(1, len(sl))

    def topic_ok(m):
        return not topic or _stem(m) not in topic

    lex = [m for m in acad._LEX.findall(text) if topic_ok(m)]
    vs = [m for m in E.RX_VOC_STRONG.findall(text) if topic_ok(m)]
    vm = [m for m in E.RX_VOC_MED.findall(text) if topic_ok(m)]
    h = {
        "lex": len(lex) / k,
        "vocab": (len(vs) + 0.3 * len(vm)) / k,
        "tri": sum(1 for s in sents if acad.count_triads(s, topic)) / ns,
        "wlen": sum(len(w) for w in ws) / n,
        "nomin": len(_NOMIN.findall(text)) / k,
        "doublet": sum(1 for a, b in _DOUBLET.findall(text) if a.lower() not in _DOUBLET_STOP and b.lower() not in _DOUBLET_STOP) / k,
        "conn": sum(1 for s in sents if _CONN.match(s.strip())) / ns,
        "generic": sum(len(rx.findall(text)) for rx, w, ar in E.RX_GEN if not ar) / k,
        "phrase": len(E.RX_LEXICON.findall(text.translate(E.NORM_AR))) / k,
        "rhetoric": (sum(len(rx.findall(text)) for rx, w in E.CONTRAST_PATTERNS if not _AR.search(rx.pattern))
                     + sum(len(rx.findall(text)) for rx, w in E.SIGNPOST_PATTERNS if not _AR.search(rx.pattern))) / k,
        "first": len(_FIRST.findall(text)) / k,
        "hedge": len(_HUMAN_HEDGE.findall(text)) / k,
        "aside": sum(1 for m in _PAREN.finditer(text) if _is_aside(m.group(1))) / k,
        "cv": E._cv(sl) if len(sl) >= 3 else float("nan"),
        "sents": float(len(sl)),
    }
    return h


def _is_aside(c: str) -> bool:
    c = c.strip()
    return (len(_W.findall(c)) >= 1 and not _YEAR.search(c) and not _ACRONYM.fullmatch(c) and not _STATS.search(c))


def wording_score(h: Dict[str, float], label_list: bool = False) -> float:
    contributions = {}
    for key, (w, lo, hi) in ANCHORS.items():
        if hi <= lo:
            hi = lo + 1e-6
        contributions[key] = w * max(0.0, min(MAX_Z, (h[key] - lo) / (hi - lo)))
    # Pool overlapping dictionaries and morphological complexity once per family.
    x = max(contributions.pop("lex"), contributions.pop("vocab"))
    x += max(contributions.pop("wlen"), contributions.pop("nomin"))
    x += sum(contributions.values())
    cv = h.get("cv", float("nan"))
    if cv == cv:
        x += UNIFORM_W * max(0.0, min(1.0, (CV_HI - cv) / (CV_HI - CV_LO)))
    x -= FIRST_W * min(MAX_Z, h["first"] / FIRST_P97)
    x -= HEDGE_W * min(MAX_Z, h["hedge"] / HEDGE_P97)
    x -= ASIDE_W * min(MAX_Z, h.get("aside", 0.0) / ASIDE_P75)
    if label_list:
        x += LABEL_LIST_W
    return x


def _to_logodds(x: float, words: int) -> float:
    L = max(-L_CAP, min(L_CAP, ALPHA * (x - X0)))
    return L * max(0.5, min(1.5, math.sqrt(words / 90.0)))


def _build_units(texts: Sequence[str], roles: Sequence[str]) -> List[Unit]:
    """Prose units: a body paragraph, or a run of list items / 'Label: explanation' lines read together."""
    units: List[Unit] = []
    run: List[int] = []

    def flush():
        if run:
            units.append(Unit(idx=list(run), words=sum(len(texts[i].split()) for i in run)))
            run.clear()

    for i, t in enumerate(texts):
        r = roles[i] if i < len(roles) else "body"
        t = t or ""
        if r not in ("body", "list") or not t.strip():
            flush()                       # a heading / table / caption / empty line ends a list run
            continue
        nw = len(t.split())
        if r == "list" or (nw <= 70 and _LABEL_ITEM.match(t)):
            run.append(i)                 # list items are read together as one unit
            continue
        flush()
        if nw >= MIN_UNIT_WORDS:
            units.append(Unit(idx=[i], words=nw))
    flush()
    return [u for u in units if u.words >= MIN_UNIT_WORDS]


def _forward_backward(Ls: List[float], prior: float = PRIOR, stay: float = STAY) -> List[float]:
    m = len(Ls)
    if not m:
        return []
    T = ((stay, 1 - stay), (1 - stay, stay))
    em = [(1.0, math.exp(max(-30.0, min(30.0, L)))) for L in Ls]
    fw, prev = [], (1 - prior, prior)
    for e in em:
        cur = [e[s] * (prev[0] * T[0][s] + prev[1] * T[1][s]) for s in (0, 1)]
        z = cur[0] + cur[1]
        prev = (cur[0] / z, cur[1] / z)
        fw.append(prev)
    bw = [(1.0, 1.0)] * m
    for i in range(m - 2, -1, -1):
        e = em[i + 1]
        v = [T[s][0] * e[0] * bw[i + 1][0] + T[s][1] * e[1] * bw[i + 1][1] for s in (0, 1)]
        z = v[0] + v[1]
        bw[i] = (v[0] / z, v[1] / z)
    out = []
    for i in range(m):
        p0, p1 = fw[i][0] * bw[i][0], fw[i][1] * bw[i][1]
        out.append(p1 / (p0 + p1))
    return out


def segment(texts: Sequence[str], roles: Sequence[str], findings=(), engine_para_scores=(), topic=frozenset(),
            docx: bool = False, paste_texts: Optional[Dict[str, float]] = None, pasted_idx=frozenset(), outside_texts=frozenset(),
            global_logit: float = 0.0) -> Segmentation:
    units = _build_units(texts, roles)
    seg = Segmentation(units=units)
    if not units:
        return seg
    eng_ps = {i: sc for i, sc, _n in (engine_para_scores or [])}
    slip_kinds = TEXT_SLIPS_DOCX if docx else TEXT_SLIPS_FLAT
    slips, fmt = {}, {}
    seen_fmt = set()
    for f in findings or ():
        p = getattr(f, "para", -1)
        if p is None or p < 0:
            continue
        if f.kind in slip_kinds:
            if f.kind in SCI_SLIP_KINDS and p < len(texts) and is_scientific_slip(texts[p], getattr(f, "start", -1)):
                continue
            slips[p] = slips.get(p, 0) + 1
        elif docx and f.kind in FORMAT_SLIPS and p not in pasted_idx and (p, f.kind) not in seen_fmt:
            seen_fmt.add((p, f.kind))
            fmt[p] = fmt.get(p, 0) + 1
    paste_texts = paste_texts or {}
    for u in units:
        text = "\n".join(texts[i] for i in u.idx)
        u.evasion = evasion_count(text)
        u.spun = spinner_count(text)
        u.arabic = len(_AR.findall(text)) > len(_W.findall(text)) * 2
        if u.arabic:
            scs = [eng_ps[i] for i in u.idx if i in eng_ps]
            if scs:
                p = min(0.97, max(0.03, sum(scs) / len(scs) / 100.0))
                u.L = 0.8 * math.log(p / (1 - p))
        else:
            h = habits(text, topic)
            lab = len(u.idx) >= 2 and sum(1 for i in u.idx if _LABEL_ITEM.match(texts[i])) >= max(2, 0.6 * len(u.idx))
            u.x = wording_score(h, lab)
            u.habits = h
            u.L = _to_logodds(u.x, u.words)
        u.slips = sum(slips.get(i, 0) for i in u.idx)
        u.L -= min(SLIP_CAP, SLIP_L * u.slips + FMT_SLIP_L * sum(fmt.get(i, 0) for i in u.idx))
        pt = max([paste_texts.get(_key(texts[i]), 0.0) for i in u.idx] or [0.0])
        if pt > 0:
            u.paste = pt
            u.L += PASTE_L * min(1.0, pt)
        u.L += global_logit
    ev_total = sum(u.evasion for u in units)
    doc_evasion = ev_total >= EVASION_DOC_MIN
    seg.evasion = ev_total if doc_evasion else 0
    seg.spun = sum(u.spun for u in units)
    for u in units:                           # deliberate obfuscation of the words: text passed through an evasion tool
        spaced = u.evasion >= (1 if doc_evasion else EVASION_UNIT_MIN)
        if spaced or u.spun:
            u.L += (SPACE_L if spaced else 0.0) + (EVASION_L if u.spun else 0.0)
            u.tool_marked = True
    seg.outside = 0
    for u in units:
        u.own_L = u.L - global_logit
        if outside_texts and any(_okey(texts[i]) in outside_texts for i in u.idx):
            u.outside = True                  # judged on its own words for the veto; the stamp is file provenance
            u.L += OUTSIDE_L
            seg.outside += 1
    post = _forward_backward([u.L for u in units])
    for u, p in zip(units, post):
        u.post = p
    _summarise(seg)
    return seg


def strengthen_clean_suspects(seg: Segmentation, delta: float):
    """'A human always slips': when the suspected text is long and (almost) slip-free, its paragraphs are confirmed."""
    if delta <= 0:
        return
    for u in seg.units:
        if u.post >= SUSPECT and u.slips == 0:
            u.L += delta
    post = _forward_backward([u.L for u in seg.units])
    for u, p in zip(seg.units, post):
        u.post = p
    _summarise(seg)


def raise_prior(seg: Segmentation, delta: float):
    """Document-wide evidence: the same log-odds added to every unit that its own evidence leaves undecided.
    A unit whose own evidence is clearly human (L <= CLEAR_HUMAN_L) stays human whatever the document says."""
    if delta <= 0 or not seg.units:
        return
    for u in seg.units:
        if u.L > CLEAR_HUMAN_L:
            u.L += delta
    post = _forward_backward([u.L for u in seg.units])
    for u, p in zip(seg.units, post):
        u.post = p
    _summarise(seg)


def own_evidence_veto(seg: Segmentation, veto: float = None):
    """Neighbours, file metadata and the slip-free rule may decide a unit its own evidence leaves undecided, never one
    whose OWN evidence leans human: a unit whose own wording/slips/traces give <= OWN_HUMAN_VETO is not shaded,
    whatever the block around it or the file as a whole says (v2.8: judged on own_L, before any file-wide push)."""
    veto = OWN_HUMAN_VETO if veto is None else veto
    changed = False
    for u in seg.units:
        own = u.L if u.own_L is None else min(u.L, u.own_L)
        if own <= veto and u.post > min(SUSPECT - 0.01, 1.0 / (1.0 + math.exp(-max(-30.0, own)))):
            # Preserve the strength of local evidence rather than flattening
            # every vetoed paragraph to the same 49 percent.
            u.post = min(u.post, SUSPECT - 0.01, 1.0 / (1.0 + math.exp(-max(-30.0, own))))
            changed = True
    if changed:
        _summarise(seg)


def _summarise(seg: Segmentation):
    tot = sum(u.words for u in seg.units) or 1
    seg.words = tot
    seg.share = sum(u.post * u.words for u in seg.units) / tot
    seg.suspect_share = sum(u.words for u in seg.units if u.post >= SUSPECT) / tot
    seg.para_post = {i: u.post for u in seg.units for i in u.idx}
    blocks, cur = [], None
    for u in seg.units:
        if u.post >= SUSPECT:
            if cur is None:
                cur = [u.idx[0], u.idx[-1], u.words]
            else:
                cur[1], cur[2] = u.idx[-1], cur[2] + u.words
        elif cur is not None:
            blocks.append(tuple(cur))
            cur = None
    if cur is not None:
        blocks.append(tuple(cur))
    seg.blocks = blocks


def _key(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip()[:120]
