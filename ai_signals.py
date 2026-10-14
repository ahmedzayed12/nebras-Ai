# -*- coding: utf-8 -*-
"""Nebras supplementary detection layer ("second opinion").

English-first, Arabic-aware, pure standard library.

WHAT THIS IS
  An independent, transparent stylometric layer that runs NEXT TO the original v2.1 engine.
  It never edits the engine's score. It (1) hardens the text before it reaches the engine
  against copy artefacts and evasion tricks, (2) measures independent signals, (3) fuses
  them with the engine score and reports whether the two agree, (4) scores each paragraph.

WHAT THIS IS NOT
  A trained classifier. Every anchor point below is a documented heuristic. Calibrate it on
  your own labelled texts with eval_signals.py before trusting any threshold.

Every signal yields z in [-1, +1]: -1 = clearly human-like, 0 = no information, +1 = clearly
AI-like. "One-sided" signals (artefacts, informality) only speak when they fire; their absence
says nothing, so they never pull a text toward the other side.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional
from matching_text import matching_text

LAYER_MAX_WEIGHT = 0.35      # largest share the layer may take in the combined index
HIGH, LOW = 55, 30           # same display bands the app already uses


# =============================================================================
# 1. INPUT HARDENING  (applied to pasted text before the engine sees it)
# =============================================================================
# Original app behaviour = zero-width removal + trailing-space trim. Extended with the
# invisible characters and look-alike tricks that are used to slip text past detectors.
_INVISIBLE = "\u200b\u200c\u200d\u2060\ufeff\u00ad\u180e\u2061\u2062\u2063\u2064"
_INV_TABLE = dict.fromkeys(map(ord, _INVISIBLE))
_INV_RE = re.compile(f"[{_INVISIBLE}]")
_EXOTIC = "\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000"
_EXOTIC_ONE = re.compile(f"[{_EXOTIC}]")
_EXOTIC_RUN = re.compile(f"[ ]*[{_EXOTIC}][ {_EXOTIC}]*")

# Cyrillic / Greek letters that are visually identical to Latin ones.
_CONFUSABLES = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i",
    "ј": "j", "ѕ": "s", "һ": "h", "ԁ": "d", "ԛ": "q", "ԝ": "w", "ӏ": "l",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P",
    "С": "C", "Т": "T", "Х": "X", "І": "I", "Ј": "J", "Ѕ": "S", "У": "Y",
    "ο": "o", "Ο": "O", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I",
    "Κ": "K", "Μ": "M", "Ν": "N", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X", "ν": "v",
}
_CONF_RE = re.compile("[" + "".join(_CONFUSABLES) + "]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_FULLWIDTH_RE = re.compile("[\uff01-\uff5e]")


@dataclass
class Sanitized:
    text: str
    report: dict = field(default_factory=dict)


def _fix_homoglyphs(s: str):
    if not _CONF_RE.search(s):
        return s, 0
    fixed = 0

    def repl(m):
        nonlocal fixed
        tok = m.group(0)
        if _LATIN_RE.search(tok) and _CONF_RE.search(tok):
            fixed += 1
            return "".join(_CONFUSABLES.get(ch, ch) for ch in tok)
        return tok

    return re.sub(r"\S+", repl, s), fixed


def sanitize(text: str) -> Sanitized:
    """Remove copy artefacts and anti-detector tricks. Genuine double spaces are preserved
    because the engine treats them as human evidence."""
    s = text or ""
    rep = {}
    n0 = len(s)
    s = s.translate(_INV_TABLE)
    rep["invisible"] = n0 - len(s)
    n1 = len(s)
    s = s.replace("\u0640", "")                      # Arabic tatweel padding
    rep["tatweel"] = n1 - len(s)
    rep["odd_space"] = len(_EXOTIC_ONE.findall(s))
    if rep["odd_space"]:
        s = _EXOTIC_RUN.sub(" ", s)
    fw = len(_FULLWIDTH_RE.findall(s))
    if fw:
        s = _FULLWIDTH_RE.sub(lambda m: chr(ord(m.group(0)) - 0xFEE0), s)
    rep["fullwidth"] = fw
    s, rep["homoglyph_words"] = _fix_homoglyphs(s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    rep = {k: v for k, v in rep.items() if v}
    if rep:
        rep["sanitized"] = True
    return Sanitized(s, rep)


def scan_hygiene(paragraphs) -> dict:
    """Same findings, but detection only (used for files, which reach the engine untouched)."""
    text = "\n".join(map(str, paragraphs))
    out = {}
    inv = len(_INV_RE.findall(text)) - (1 if text.startswith("\ufeff") else 0)
    if inv > 0:
        out["invisible"] = inv
    odd = len(_EXOTIC_ONE.findall(text))
    if odd >= 10:                                      # a few NBSPs are normal in Word files
        out["odd_space"] = odd
    tat = text.count("\u0640")
    if tat >= 3:
        out["tatweel"] = tat
    homog = 0
    if _CONF_RE.search(text):
        for tok in re.findall(r"\S+", text):
            if _LATIN_RE.search(tok) and _CONF_RE.search(tok):
                homog += 1
    if homog:
        out["homoglyph_words"] = homog
    return out


# =============================================================================
# 2. TEXT UTILITIES
# =============================================================================
_AR_DIAC = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed\u0640]")
_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
_PUNCT_MAP = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'", "“": '"', "”": '"',
                            "‐": "-", "‑": "-", "‒": "-"})


def _norm(s: str) -> str:
    s = s.translate(_PUNCT_MAP)
    return matching_text(_AR_DIAC.sub("", s).translate(_AR_MAP).lower())


_TOK = re.compile(r"[a-z\u00e0-\u00f6\u00f8-\u024f]+(?:'[a-z]+)*"
                  r"|[\u0621-\u064a\u0671-\u06d3]+"
                  r"|[0-9\u0660-\u0669]+(?:[.,][0-9\u0660-\u0669]+)*")


def _kind(t: str) -> str:
    c = t[0]
    if "\u0621" <= c <= "\u06d3":
        return "ar"
    return "lat" if c.isalpha() else "num"


_ABBR = re.compile(r"\b(?:mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|inc|ltd|co|fig|eq|no|cf|al|approx|dept|est|vol)\."
                   r"|\b(?:e\.g|i\.e|u\.s|u\.k|a\.m|p\.m|ph\.d)\.")
_INITIAL = re.compile(r"\b([a-z])\.(?=\s)")
_SENT_SPLIT = re.compile(r"(?<=[.!?؟…])[\"')\]»]*\s+")
_BULLET = re.compile(r"^\s*(?:[-*•▪◦●·–—]|\d{1,3}[.)]|[a-z][.)])\s+")
_TERM = (".", "!", "?", "؟", "…", ":", ";", '"', "'", "»", ")")


def _segments(par: str):
    """Hard-wrapped lines (PDF style) are re-joined; headings and list items stay separate."""
    segs, buf = [], ""
    for raw in par.splitlines():
        ln = raw.strip()
        if not ln:
            if buf:
                segs.append(buf)
                buf = ""
            continue
        if buf and _BULLET.match(ln):
            segs.append(buf)
            buf = ln
        else:
            buf = f"{buf} {ln}" if buf else ln
        if buf.endswith(_TERM) or len(ln) < 55:
            segs.append(buf)
            buf = ""
    if buf:
        segs.append(buf)
    return segs


def _split_sentences(seg: str):
    s = _ABBR.sub(lambda m: m.group(0)[:-1] + "\x01", seg)
    s = _INITIAL.sub("\\1\x01", s)
    return [p.replace("\x01", ".") for p in _SENT_SPLIT.split(s) if p.strip()]


def _pw(x: float, pts) -> float:
    """Piece-wise linear map. Anchor points are the tunable part of every signal."""
    if x <= pts[0][0]:
        return pts[0][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _mean(v):
    return sum(v) / len(v)


def _pstdev(v):
    m = _mean(v)
    return (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5


_STOP = set("""the of and to in a an is are was were be been it its that this these those for on as with by at from or
not but if so than then there their they them he she we you i his her our your my me us do does did has have had will
would can could may might should must which who whom what when where why how also very more most such into about
من في على الي ان او ثم هذا هذه ذلك تلك التي الذي كان كانت يكون هو هي هم ما لا قد كما عن مع""".split())
_STOP = {_norm(w) for w in _STOP}


def _stem(t: str) -> str:
    k = _kind(t)
    if k == "lat":
        if t.endswith("'s"):
            t = t[:-2]
        if t.endswith("ies") and len(t) > 4:
            return t[:-3] + "y"
        if t.endswith("s") and not t.endswith("ss") and len(t) > 3:
            return t[:-1]
        return t
    if k == "ar" and len(t) > 4:
        for p in ("وال", "بال", "كال", "فال", "لل", "ال", "و"):
            if t.startswith(p) and len(t) - len(p) >= 3:
                return t[len(p):]
    return t


def _mattr(stems, w: int = 50) -> Optional[float]:
    n = len(stems)
    if n < w:
        return None
    c = Counter(stems[:w])
    uniq = len(c)
    tot, cnt = uniq, 1
    for i in range(w, n):
        out = stems[i - w]
        c[out] -= 1
        if c[out] == 0:
            uniq -= 1
        inn = stems[i]
        if c[inn] == 0:
            uniq += 1
        c[inn] += 1
        tot += uniq
        cnt += 1
    return tot / (cnt * w)


# =============================================================================
# 3. LEXICONS  (written naturally, normalised at load; weight 1.0 = strong tell)
# =============================================================================
_EN_STOCK = {
    # discourse clichés
    "delve into": 1, "delves into": 1, "delving into": 1, "delve deeper": 1, "dive into": .8, "dives into": .8,
    "diving into": .8, "dive deeper": .8, "let's dive": 1, "let's delve": 1, "let's explore": .8,
    "let's take a closer look": 1, "buckle up": .8, "look no further": 1, "whether you're a": .8,
    "whether you are a": .8, "whether you're looking": .8,
    "it is important to note": 1, "it's important to note": 1, "it is worth noting": 1, "it's worth noting": 1,
    "it is worth mentioning": 1, "it is important to remember": 1, "it is important to understand": 1,
    "it is essential to": .8, "it's essential to": .8, "it is crucial to": .8, "it's crucial to": .8,
    "it is important to": .6, "it's important to": .6,
    # grand framing
    "a testament to": 1, "serves as a testament": 1, "stands as a testament": 1, "stands as a": .7,
    "serves as a": .5, "serves as an": .5, "rich tapestry": 1, "tapestry of": 1, "intricate tapestry": 1,
    "vibrant tapestry": 1, "navigate the complexities": 1, "navigating the complexities": 1,
    "navigate the landscape": 1, "navigating the landscape": 1, "complex landscape": .7,
    "ever evolving": .8, "ever changing": .6, "rapidly evolving": .7, "constantly evolving": .7,
    "in today's fast paced": 1, "in today's digital age": 1, "in today's world": .8, "in today's rapidly": 1,
    "in the digital age": .8, "in the modern world": .7, "in an increasingly": .7, "in the realm of": 1,
    "in the world of": .6, "in the field of": .4, "when it comes to": .5,
    "underscores the importance": 1, "underscore the importance": 1, "underscoring the importance": 1,
    "highlights the importance": .9, "highlight the importance": .9, "emphasizes the importance": .9,
    "highlights the need": .8, "underscores the need": 1, "underscores the significance": 1,
    "shed light on": .9, "sheds light on": .9, "shedding light on": .9,
    "a myriad of": 1, "myriad of": 1, "a plethora of": 1, "a wide array of": .9, "a wide range of": .5,
    "a variety of": .3, "unlock the potential": 1, "unlocking the potential": 1, "unlock new": .8,
    "harness the power": 1, "harnessing the power": 1, "full potential": .5,
    "foster a sense of": 1, "fostering a sense of": 1, "foster innovation": .9, "fostering innovation": .9,
    "foster collaboration": .9, "fostering collaboration": .9, "fosters collaboration": .9,
    "at the forefront": .8, "at the heart of": .7, "at the intersection of": .9, "the intersection of": .5,
    "the interplay between": .9, "interplay between": .8, "intricate interplay": 1, "delicate balance": .8,
    "striking a balance": .5, "strike a balance": .5, "pave the way": .8, "paves the way": .8, "paving the way": .8,
    "lay the groundwork": .7, "lays the groundwork": .7, "lays the foundation": .7,
    "embark on a journey": 1, "embarking on a journey": 1, "a journey through": .6, "remarkable journey": .8,
    "cornerstone of": .7, "beacon of": 1, "pillar of": .5, "driving force behind": .6, "game changer": .6,
    "paradigm shift": .7, "double edged sword": .8,
    "holistic approach": 1, "comprehensive approach": .8, "multifaceted approach": 1, "comprehensive understanding": .8,
    "deeper understanding": .5, "nuanced understanding": 1, "valuable insights": .6, "key takeaways": .7,
    "key takeaway": .7, "gain valuable insights": 1, "play a vital role in shaping": 1,
    # single words that LLM prose over-uses (small weight each)
    "delve": 1, "delves": 1, "delving": 1, "tapestry": .9, "multifaceted": .9, "intricacies": .8, "pivotal": .8,
    "underscore": .7, "underscores": .7, "underscoring": .7, "underscored": .7, "leverage": .5, "leveraging": .6,
    "leveraged": .5, "seamless": .6, "seamlessly": .6, "holistic": .7, "myriad": .8, "plethora": .8,
    "testament": .6, "beacon": .8, "cornerstone": .6, "foster": .5, "fosters": .5, "fostering": .6,
    "embark": .8, "embarking": .8, "realm": .6, "transformative": .6, "streamline": .5, "streamlining": .5,
    "empower": .45, "empowering": .5, "empowers": .5, "elevate": .5, "elevating": .5, "harness": .5,
    "harnessing": .6, "unleash": .6, "unleashing": .6, "meticulous": .7, "meticulously": .8, "commendable": .8,
    "invaluable": .5, "paramount": .6, "indispensable": .5, "burgeoning": .8, "labyrinth": .8,
    "kaleidoscope": .8, "symphony": .5, "nuanced": .45, "robust": .35, "comprehensive": .25, "crucial": .3,
    "navigate": .3, "navigating": .35, "landscape": .25, "innovative": .3, "evolving": .25,
}
_AR_STOCK = {
    "في عالمنا اليوم": 1, "في عالم اليوم": 1, "في عصرنا الحالي": 1, "في عصرنا الحديث": 1,
    "في العصر الرقمي": 1, "في العصر الحديث": .6, "في ظل التطورات المتسارعة": 1, "في ظل التطور السريع": 1,
    "في ظل التحولات": .8, "يشهد العالم": .8, "عالم سريع التغير": 1, "التطورات المتسارعة": .8,
    "من الجدير بالذكر": 1, "تجدر الإشارة إلى": 1, "من الجدير بالملاحظة": 1, "من المهم الإشارة": .8,
    "من المهم أن نلاحظ": 1, "من الأهمية بمكان": 1, "لا بد من الإشارة": .8,
    "دورا محوريا": 1, "دورا حيويا": 1, "دورا بارزا": .8, "دورا حاسما": .8, "دورا هاما": .6, "دورا مهما": .6,
    "دورا أساسيا": .6, "دورا رئيسيا": .6, "مما لا شك فيه": 1, "لا يمكن إنكار": 1, "لا يمكن تجاهل": .8,
    "لا يخفى على أحد": .6, "يمكن القول إن": .6, "في نهاية المطاف": .8, "خلاصة القول": .8,
    "يعد من أهم": .7, "تعد من أهم": .7, "يعد من أبرز": .8, "تعد من أبرز": .8, "تعتبر من أهم": .6, "يعتبر من أهم": .6,
    "آفاقا جديدة": 1, "آفاق جديدة": 1, "حلولا مبتكرة": 1, "حلول مبتكرة": 1, "نهجا شاملا": 1, "نهج شامل": .8,
    "نهج متكامل": .8, "ركيزة أساسية": .8, "التحديات والفرص": 1, "الفرص والتحديات": 1,
    "يسهم بشكل كبير": 1, "تسهم بشكل كبير": 1, "بشكل ملحوظ": .4, "بشكل فعال": .5, "بصورة فعالة": .6,
    "في هذا المقال": .8, "سنستعرض": .8, "دعونا نستكشف": 1, "دعونا نتعرف": 1, "هل تساءلت يوما": 1,
    "رحلة استكشافية": 1, "التحول الرقمي": .4, "تحقيق التوازن": .5, "مشهد متغير": .8,
}
_STOCK, _STOCK_DISP = {}, {}
for _d in (_EN_STOCK, _AR_STOCK):
    for _k, _w in _d.items():
        _nk = " ".join(_TOK.findall(_norm(_k)))
        _STOCK[_nk] = _w
        _STOCK_DISP[_nk] = _k
_STOCK_RE = re.compile(r"(?<!\S)(?:[وف])?(" + "|".join(
    re.escape(k) for k in sorted(_STOCK, key=len, reverse=True)) + r")(?!\S)")

# Sentence openers: strong = classic LLM transitions, weak = ordinary connectors.
_EN_OPEN = {k: 1.0 for k in [
    "furthermore", "moreover", "additionally", "in addition", "in conclusion", "to conclude", "in summary",
    "to summarize", "to sum up", "overall", "ultimately", "consequently", "nonetheless", "nevertheless",
    "in essence", "in light of", "that said", "that being said", "having said that", "importantly", "notably",
    "it is important", "it's important", "it is worth", "it's worth", "it is crucial", "it's crucial",
    "by leveraging", "by fostering", "by understanding", "by embracing", "by prioritizing", "by doing so",
    "as such", "in this article", "in this essay", "in this post", "in today's"]}
_EN_OPEN.update({k: .4 for k in [
    "however", "therefore", "thus", "hence", "as a result", "for example", "for instance", "finally", "lastly",
    "indeed", "similarly", "likewise", "in contrast", "on the other hand", "meanwhile", "specifically",
    "in particular", "firstly", "secondly", "thirdly", "accordingly", "subsequently"]})
_AR_OPEN = {k: 1.0 for k in [
    "علاوة على ذلك", "بالإضافة إلى ذلك", "إضافة إلى ذلك", "فضلا عن ذلك", "من ناحية أخرى", "من جهة أخرى",
    "في المقابل", "على الرغم من ذلك", "في هذا السياق", "في سياق متصل", "في ضوء ذلك", "بناء على ذلك",
    "في الختام", "ختاما", "في الخلاصة", "بشكل عام", "بوجه عام", "بصفة عامة"]}
_AR_OPEN.update({k: .5 for k in ["مع ذلك", "من هنا", "أولا", "ثانيا", "ثالثا", "رابعا", "أخيرا"]})
_OPEN, _OPEN_DISP = {}, {}
for _d in (_EN_OPEN, _AR_OPEN):
    for _k, _w in _d.items():
        _nk = " ".join(_TOK.findall(_norm(_k)))
        _OPEN[_nk] = _w
        _OPEN_DISP[_nk] = _k


def _opener(tk):
    cands = [tk]
    if tk and len(tk[0]) > 3 and tk[0][0] in "وف" and _kind(tk[0]) == "ar":
        cands.append([tk[0][1:]] + tk[1:])
    for c in cands:
        for k in (3, 2, 1):
            if len(c) >= k:
                key = " ".join(c[:k])
                if key in _OPEN:
                    return _OPEN[key], key
    return 0.0, None


_EN_INFORMAL = set("""gonna wanna gotta kinda sorta lol tbh btw imo imho idk omg yeah yep nope yup nah dunno lemme gimme cuz tho
ya y'all whatever damn crap sucks awesome cool guys dude bro stuff hey wow ugh hmm haha hahaha lmao pls plz thx u ur
ok okay yo""".split())
_AR_INFORMAL = {_norm(w) for w in (
    "ده دي دا إزاي ليه إيه مش كده أوي عشان فين امتى دلوقتي كويس شلون وش ايش هيك كتير شو لسا مو يلا والله حبيبي بس "
    "طب هاي هادا خلاص معلش ايوه انتو احنا ياجماعة").split()}
_CONTR = re.compile(r"(?:n't|'m|'re|'ve|'ll|'d)$")

# Chat leftovers: near-certain evidence of text pasted from a chatbot.
_LEFTOVER = [_norm(x) for x in (
    "as an ai language model", "as an ai", "as a large language model", "as a language model", "i'm an ai",
    "i am an ai", "i don't have personal", "i do not have personal", "i cannot browse", "my knowledge cutoff",
    "knowledge cutoff", "i hope this helps", "hope this helps", "let me know if you'd like",
    "let me know if you would like", "let me know if you need", "let me know if you want",
    "would you like me to", "feel free to ask", "certainly here's", "sure here's", "certainly here is",
    "sure here is", "i'm sorry but i", "i apologize but", "i can't assist with", "as of my last update",
    "regenerate response", "i'd be happy to help",
    "كنموذج لغوي", "بصفتي نموذج", "كذكاء اصطناعي", "بصفتي ذكاء اصطناعي", "ليس لدي رأي شخصي", "لا يمكنني تصفح",
    "أتمنى أن يكون هذا مفيدا", "أرجو أن يكون هذا مفيدا", "هل تريد مني", "هل ترغب في أن أقوم",
    "إذا كنت بحاجة إلى مزيد من", "أتمنى أن تكون هذه المعلومات مفيدة")]
_LEFTOVER_RE = re.compile("(?<!\\S)(?:" + "|".join(
    re.escape(" ".join(_TOK.findall(x))) for x in _LEFTOVER) + ")(?!\\S)")

_MD_RULES = [   # (compiled regex, weight per hit, cap, label)
    (re.compile(r"\*\*[^*\n]{2,80}\*\*"), 1.0, 6, "**bold**"),
    (re.compile(r"(?m)^\s{0,3}#{1,6}\s+\S"), 1.0, 4, "# heading"),
    (re.compile(r"```"), 1.0, 2, "```code```"),
    (re.compile(r"(?m)^\s*\d{1,2}\.\s+\*\*"), 1.0, 4, "1. **item**"),
    (re.compile(r"(?m)^\s*[-*•]\s+\*\*[^*\n]+\*\*\s*[:\-–—]"), 1.0, 4, "- **term**: …"),
    (re.compile(r"(?m)^\s*\|.+\|\s*$"), .3, 3, "| table |"),
    (re.compile(r"(?m)^\s*(?:---|\*\*\*|___)\s*$"), .5, 2, "---"),
    (re.compile("(?m)^\\s*[\u2600-\u27bf\u2b50\U0001f300-\U0001faff]"), .5, 4, "emoji bullets"),
]

# Rhetorical frames (English + Arabic)
_FRAME_RES = [
    re.compile(r"\bnot (?:just|only|merely|simply)\b[^.!?\n]{3,80}?(?:[,;:—–]|\s-\s)\s*(?:it's|it is|but|they're|they are|that's|that is)\b"),
    re.compile(r"\b(?:isn't|is not|aren't|are not|wasn't|was not)\s+(?:just|only|merely|simply)\b[^.!?\n]{3,80}?(?:[,;:—–]|\s-\s)\s*(?:it's|it is|but|they're|they are|that's|that is)\b"),
    re.compile(r"\bnot only\b[^.!?\n]{3,90}?\bbut (?:also )?\b"),
    re.compile(r"\bwhile\b[^.!?\n]{5,90}?,\s*(?:it(?:'s| is)|there(?:'s| is)) (?:also )?(?:important|essential|crucial|worth)\b"),
    re.compile(r"لا [يت]قتصر[^.!?؟\n]{3,90}?\bبل\b"),
    re.compile(r"ليس فقط[^.!?؟\n]{3,90}?\bبل\b"),
    re.compile(_norm("ليس هذا فحسب")),
]
_TRIAD = re.compile(r"\b[a-z'-]+(?:\s[a-z'-]+){0,2},\s[a-z'-]+(?:\s[a-z'-]+){0,2},\s(?:and|or)\s[a-z'-]+")
_NOMINAL = re.compile(r"(?:tion|tions|sion|sions|ment|ments|ity|ities|ness|ance|ances|ence|ences)$")
_I_LOWER = re.compile(r"(?<![\w'])i(?![\w'])(?!\.[a-z])")
_REPEAT_PUNCT = re.compile(r"[!?؟]{2,}|\.{4,}")
_DASH = re.compile(r"—| – |--")


# =============================================================================
# 4. DATA STRUCTURES
# =============================================================================
@dataclass
class Signal:
    key: str
    label: str
    weight: float
    z: Optional[float]
    value: str = ""
    note: str = ""
    evidence: list = field(default_factory=list)
    one_sided: bool = False


@dataclass
class Layer:
    score: Optional[float]
    confidence: float
    sufficient: bool
    words: int
    sentences: int
    lang: str
    signals: list
    flags: list
    evidence: list
    label: str
    coverage: float


@dataclass
class Fusion:
    combined: float
    engine: float
    layer: Optional[float]
    agreement: str
    message: str
    weight: float


@dataclass
class ParaResult:
    index: int
    layer: Optional[float]
    combined: Optional[float]
    evidence: list


@dataclass
class Enhanced:
    doc: Layer
    fusion: Fusion
    paras: list
    hybrid: Optional[dict]
    hygiene: dict
    layer_only: list


def tilt(z: Optional[float]) -> str:
    if z is None:
        return "—"
    return "مولَّد" if z >= .25 else "بشري" if z <= -.25 else "محايد"


def layer_label(score: Optional[float]) -> str:
    if score is None:
        return "غير كافٍ"
    if score >= 68:
        return "أقرب بقوة إلى أسلوب مولَّد"
    if score >= 56:
        return "تميل المؤشرات إلى نص مولَّد"
    if score >= 44:
        return "غير حاسم"
    if score >= 32:
        return "تميل المؤشرات إلى كتابة بشرية"
    return "أقرب إلى كتابة بشرية"


# =============================================================================
# 5. CORE ANALYSIS
# =============================================================================
def analyze(paragraphs, mode: str = "doc", topic=None) -> Layer:
    doc = mode == "doc"
    paras = [p for p in (str(x) for x in paragraphs) if p.strip()]
    raw = "\n".join(paras)
    nraw = _norm(raw)
    toks = _TOK.findall(nraw)
    n = len(toks)
    if n < (60 if doc else 20):
        return Layer(None, 0.0, False, n, 0, "?", [], [], [], "النص قصير على الطبقة الإضافية", 0.0)

    kinds = [_kind(t) for t in toks]
    n_ar, n_lat = kinds.count("ar"), kinds.count("lat")
    if n_ar + n_lat < max(20 if not doc else 40, 0.5 * n):      # numbers / symbols / emoji only
        return Layer(None, 0.0, False, n, 0, "?", [], [], [], "النص لا يحتوي كلمات كافية", 0.0)
    lat_share = n_lat / max(1, n_ar + n_lat)
    lang = "en" if lat_share >= .7 else "ar" if lat_share <= .3 else "mixed"
    english = lang in ("en", "mixed")
    den = n if doc else max(n, 150)            # short paragraphs: damp single-hit rates

    # sentences ----------------------------------------------------------------
    sents = []
    for p in paras:
        for seg in _segments(_norm(p)):
            for s in _split_sentences(seg):
                tk = _TOK.findall(s)
                if len(tk) >= 3:
                    sents.append(tk)
    ns = len(sents)
    lens = [min(len(s), 70) for s in sents]

    joined = " " + " ".join(toks) + " "
    sigs: list = []
    flags: list = []
    top_evidence: list = []

    # 1. burstiness ---------------------------------------------------------------
    z, val, note = None, "جمل قليلة", "عدد الجمل أقل من اللازم لقياس التفاوت."
    if ns >= (8 if doc else 5):
        mean = _mean(lens)
        cv = _pstdev(lens) / mean
        rh = _mean([abs(a - b) for a, b in zip(lens, lens[1:])]) / mean
        z = (_pw(cv, [(.25, 1), (.38, .5), (.50, 0), (.65, -.6), (.85, -1)])
             + _pw(rh, [(.28, 1), (.45, .4), (.60, 0), (.80, -.6), (1.1, -1)])) / 2
        val = f"CV={cv:.2f} · إيقاع={rh:.2f}"
        note = ("أطوال الجمل متقاربة بانتظام أكبر مما يُعهد في الكتابة البشرية." if z >= .25 else
                "تفاوت واضح بين الجمل الطويلة والقصيرة، وهو أقرب لإيقاع الكتابة البشرية." if z <= -.25 else
                "تفاوت متوسط في أطوال الجمل.")
    sigs.append(Signal("burstiness", "تفاوت أطوال الجمل (Burstiness)", 1.0, z, val, note))

    # 2. stock phrases ----------------------------------------------------------------
    hits = Counter(m.group(1) for m in _STOCK_RE.finditer(joined))
    if topic:
        from academic_signals import _stem as topic_stem
        hits = Counter({k: v for k, v in hits.items()
                        if not all(topic_stem(w) in topic for w in _TOK.findall(k))})
    # Bound each phrase's density, rather than its total count in a chapter.
    # Repeating the same prose must not make its phrasing evidence disappear.
    phrase_cap = 2 * max(1.0, n / 1000.0)
    wsum = sum(_STOCK[k] * min(c, phrase_cap) for k, c in hits.items())
    rate = wsum / den * 1000
    z = _pw(rate, [(0, -.2), (1.5, -.05), (4, .3), (8, .75), (14, 1.0)])
    ev = [f"{_STOCK_DISP[k]} ×{c}" if c > 1 else _STOCK_DISP[k]
          for k, c in sorted(hits.items(), key=lambda kv: -_STOCK[kv[0]] * kv[1])[:8]]
    top_evidence += ev[:3]
    note = ("كثافة عالية من العبارات والقوالب المألوفة في النصوص المولَّدة." if z >= .25 else
            "لا توجد عبارات قوالب لافتة." if hits == {} else
            "عبارات قوالب قليلة لا تكفي وحدها للاشتباه.")
    sigs.append(Signal("stock", "عبارات وقوالب شائعة في النصوص المولَّدة", 1.3, z,
                       f"{rate:.1f} لكل 1000 كلمة · {sum(hits.values())} عبارة", note, ev))

    # 3. rhetorical frames ----------------------------------------------------------------
    frames = sum(len(r.findall(nraw)) for r in _FRAME_RES)
    # Reuse the balanced-triad rule and retain capitals: named protocols,
    # taxa and numeric lists are subject matter, not rhetorical authorship.
    if english:
        from academic_signals import count_triads
        triads = count_triads(raw, topic)
    else:
        triads = 0
    zf = _pw(frames / den * 1000, [(0, -.1), (1, .25), (3, .8), (6, 1)])
    z = (.6 * zf + .4 * _pw(triads / den * 1000, [(0, -.2), (4, 0), (10, .5), (18, .9)])) if english else zf
    note = ("أنماط بلاغية متكررة (التوازي السلبي «ليس فقط… بل» والثلاثيات)." if z >= .25 else
            "لا أنماط بلاغية متكررة لافتة." if z <= -.1 else "حضور محدود للأنماط البلاغية.")
    sigs.append(Signal("frames", "أنماط بلاغية متكررة", .8, z,
                       f"توازٍ: {frames} · ثلاثيات: {triads}" if english else f"توازٍ: {frames}", note))

    # 4. connector openers ----------------------------------------------------------------
    z, val, note, ev = None, "جمل قليلة", "عدد الجمل أقل من اللازم.", []
    if ns >= (6 if doc else 3):
        oc = Counter()
        wsum = 0.0
        for tk in sents:
            w, key = _opener(tk)
            if w:
                wsum += w
                oc[key] += 1
        share = wsum / (ns if doc else max(ns, 4))
        z = _pw(share, [(0, -.4), (.06, -.1), (.12, .3), (.20, .8), (.30, 1)])
        val = f"{share * 100:.0f}% من الجمل"
        ev = [f"{_OPEN_DISP[k]} ×{c}" if c > 1 else _OPEN_DISP[k] for k, c in oc.most_common(5)]
        note = ("نسبة مرتفعة من الجمل تبدأ بأدوات انتقال رسمية." if z >= .25 else
                "بدايات الجمل متنوعة وغير مقولبة." if z <= -.25 else "حضور معتدل لأدوات الانتقال.")
    sigs.append(Signal("connectors", "افتتاح الجمل بأدوات انتقال", .8, z, val, note, ev))

    # 5. lexical diversity ----------------------------------------------------------------
    stems = [_stem(t) for t in toks]
    mt = _mattr(stems, 50) if n >= (100 if doc else 60) else None
    if mt is None:
        sigs.append(Signal("lexdiv", "التنوع المعجمي (MATTR)", .3, None, "نص قصير", "الطول لا يكفي لقياس التنوع."))
    else:
        z = _pw(mt, [(.55, .6), (.68, .2), (.76, 0), (.84, -.3), (.92, -.5)])
        sigs.append(Signal("lexdiv", "التنوع المعجمي (MATTR)", .3, z, f"{mt:.2f}",
                           "مفردات أقل تنوعًا من المعتاد." if z >= .25 else
                           "مفردات متنوعة." if z <= -.25 else "تنوع معجمي متوسط."))

    # 6. repetition -------------------------------------------------------------------------
    tri = list(zip(toks, toks[1:], toks[2:]))
    good = [t for t in tri if sum(1 for w in t if w not in _STOP and _kind(w) != "num") >= 2]
    rep_ratio = 0.0
    if good:
        c3 = Counter(good)
        rep_ratio = sum(c for c in c3.values() if c >= 2) / len(good)
    keys = [" ".join(s[:2]) for s in sents]
    same = sum(1 for i in range(1, ns) if keys[i] in keys[max(0, i - 5):i])
    op_rep = same / max(1, ns - 1)
    zs = [_pw(rep_ratio, [(0, -.1), (.02, 0), (.05, .4), (.10, .8), (.15, 1)])]
    if ns >= (6 if doc else 4):
        zs.append(_pw(op_rep, [(0, -.2), (.10, 0), (.22, .6), (.40, 1)]))
    z = _mean(zs)
    sigs.append(Signal("repetition", "التكرار وتشابه بدايات الجمل", .6, z,
                       f"ثلاثيات مكررة {rep_ratio * 100:.1f}% · بدايات متشابهة {op_rep * 100:.0f}%",
                       "تكرار في الصياغة وبدايات الجمل." if z >= .25 else
                       "قلة تكرار." if z <= -.25 else "تكرار معتدل."))

    # 7. paragraph uniformity (document only) -----------------------------------------------
    if doc:
        pl = [len(_TOK.findall(_norm(p))) for p in paras]
        pl = [x for x in pl if x >= 15]
        if len(pl) >= 4:
            pcv = _pstdev(pl) / _mean(pl)
            z = _pw(pcv, [(.15, 1), (.30, .5), (.45, 0), (.70, -.7), (1.0, -1)])
            sigs.append(Signal("para_uniform", "انتظام أطوال الفقرات", .5, z, f"CV={pcv:.2f} · {len(pl)} فقرات",
                               "فقرات متقاربة الحجم على نحو منتظم." if z >= .25 else
                               "أحجام الفقرات متفاوتة." if z <= -.25 else "انتظام متوسط للفقرات."))
        else:
            sigs.append(Signal("para_uniform", "انتظام أطوال الفقرات", .5, None, "فقرات قليلة",
                               "عدد الفقرات أقل من اللازم."))

    # 8. typography / punctuation -------------------------------------------------------------
    dash = len(_DASH.findall(raw))
    den_t = max(den, 300)
    zt = [_pw(dash / den_t * 1000, [(0, -.05), (2, .1), (5, .6), (9, 1)])]
    val = f"شرطات طويلة {dash / den_t * 1000:.1f}/1000"
    if n_ar / max(1, n_ar + n_lat) >= .6:
        latin_c = len(re.findall(r"(?<!\d),(?!\d)|\?", raw))
        ar_c = raw.count("،") + raw.count("؟")
        if latin_c + ar_c >= 6:
            m = latin_c / (latin_c + ar_c)
            zt.append(_pw(m, [(0, .1), (.08, 0), (.3, -.6), (.7, -1)]))
            val += f" · علامات لاتينية في نص عربي {m * 100:.0f}%"
    z = _mean(zt)
    sigs.append(Signal("typography", "علامات الترقيم والطباعة", .7, z, val,
                       "كثافة شرطات طويلة (—) أو انتظام ترقيم غير معتاد في الكتابة اليدوية." if z >= .25 else
                       "ترقيم يحمل ضجيج الكتابة اليدوية." if z <= -.25 else "لا دلالة طباعية واضحة."))

    # 9. register (English nominalisation density) --------------------------------------------
    if english and n_lat >= (80 if doc else 40):
        lat_tokens = [t for t, k in zip(toks, kinds) if k == "lat"]
        nom = sum(1 for t in lat_tokens if len(t) >= 6 and _NOMINAL.search(t)) / len(lat_tokens)
        z = _pw(nom, [(.025, -.5), (.055, 0), (.085, .45), (.12, .7)])
        sigs.append(Signal("register", "كثافة الأسماء المجرَّدة (إنجليزي)", .4, z, f"{nom * 100:.1f}% من الكلمات",
                           "لغة مجرَّدة رسمية بكثافة عالية." if z >= .25 else
                           "لغة أقرب للمحادثة." if z <= -.25 else "مستوى رسمية معتاد."))

    # 10. informality (one-sided → human) -------------------------------------------------------
    inf = 0.0
    inf_found = []
    for t in toks:
        if t in _EN_INFORMAL or t in _AR_INFORMAL or _CONTR.search(t):
            inf += 1
            if len(inf_found) < 6 and t not in inf_found:
                inf_found.append(t)
    inf += len(_I_LOWER.findall(raw)) + len(_REPEAT_PUNCT.findall(raw))
    if inf:
        z = _pw(inf / den * 1000, [(0, 0), (3, -.25), (10, -.6), (20, -.9), (35, -1)])
        sigs.append(Signal("informality", "علامات الكتابة العفوية (اختصارات/عامية)", .9, z,
                           f"{inf / den * 1000:.1f} لكل 1000 كلمة",
                           "اختصارات وعامية وتعبيرات عفوية تميل لصالح الكتابة البشرية.", inf_found, True))

    # 11. artefacts (one-sided → AI) ------------------------------------------------------------
    art, art_ev = 0.0, []
    for rx, w, cap, label in _MD_RULES:
        c = min(len(rx.findall(raw)), cap)
        if c:
            art += w * c
            art_ev.append(f"{label} ×{c}")
    left = Counter(m.group(0).strip() for m in _LEFTOVER_RE.finditer(joined))
    n_left = sum(left.values())
    if n_left:
        art += 2.0 * min(n_left, 3)
        art_ev = [f"«{k}»" for k in list(left)[:3]] + art_ev
        flags.append(("leftover", f"بقايا محادثة مع روبوت ذكاء اصطناعي ({n_left})"))
    md_hits = [e for e in art_ev if not e.startswith("«")]
    if md_hits:
        flags.append(("markdown", "تنسيق Markdown ملصق من واجهة محادثة: " + "، ".join(md_hits[:4])))
    if art:
        z = _pw(art, [(0, 0), (1, .45), (2, .7), (4, .9), (7, 1.0)])
        sigs.append(Signal("artifacts", "بقايا محادثة وتنسيق Markdown", 1.5, z, f"{art:.1f} نقطة",
                           "مخلّفات تنسيق أو عبارات محادثة تُرى عادة عند لصق ناتج روبوت دردشة.", art_ev[:6], True))
    top_evidence += art_ev[:2]

    # ---- fuse ----------------------------------------------------------------------------------
    two = [s for s in sigs if not s.one_sided]
    meas = [s for s in two if s.z is not None]
    w_all = sum(s.weight for s in two)
    w_av = sum(s.weight for s in meas)
    if w_av <= 0:
        return Layer(None, 0.0, False, n, ns, lang, sigs, flags, top_evidence, "غير كافٍ", 0.0)
    num = sum(s.weight * s.z for s in meas) + sum(s.weight * s.z for s in sigs if s.one_sided and s.z is not None)
    S = max(-1.0, min(1.0, num / w_av))
    cov = w_av / w_all
    score = 50 + 50 * S * (0.4 + 0.6 * cov)
    if n_left >= 1:
        score = max(score, 78 if n_left == 1 else 88)
    score = max(0.0, min(100.0, score))
    conf = min(1.0, n / (300 if doc else 150)) * (0.5 + 0.5 * cov) * (1.0 if doc else 0.6)
    return Layer(score, conf, True, n, ns, lang, sigs, flags, top_evidence, layer_label(score), cov)


# =============================================================================
# 6. FUSION AND PER-PARAGRAPH RUN
# =============================================================================
def fuse(engine: float, layer: Optional[Layer], wmax: float = LAYER_MAX_WEIGHT) -> Fusion:
    if layer is None or not layer.sufficient or layer.score is None:
        return Fusion(engine, engine, None, "na", "النص قصير على الطبقة الإضافية؛ المؤشر المعزز = مؤشر المحرك.", 0.0)
    w = wmax * layer.confidence
    combined = (1 - w) * engine + w * layer.score
    ls = layer.score
    if engine >= HIGH and ls >= 58:
        ag, msg = "both_high", "المحرك والطبقة الإضافية متفقان على الاشتباه."
    elif engine < LOW and ls < 42:
        ag, msg = "both_low", "المحرك والطبقة الإضافية متفقان على أن الأسلوب أقرب للبشري."
    elif abs(engine - ls) >= 35:
        ag, msg = "conflict", "تعارض كبير بين المحرك والطبقة الإضافية؛ تلزم مراجعة بشرية ولا يُعتمد أي رقم وحده."
    else:
        ag, msg = "partial", "توافق جزئي؛ القرائن غير حاسمة."
    return Fusion(combined, engine, ls, ag, msg, w)


def run(paragraphs, engine_score: float, para_scores=None, hygiene: Optional[dict] = None) -> Enhanced:
    paras = [str(p) for p in paragraphs]
    doc = analyze([p for p in paras if p.strip()], "doc")
    fusion = fuse(float(engine_score), doc)
    hy = scan_hygiene(paras)
    hy.update(hygiene or {})

    out, layer_only, combined_vals = [], [], []
    for item in (para_scores or []):
        i, e_sc = int(item[0]), float(item[1])
        txt = paras[i] if 0 <= i < len(paras) else ""
        pl = analyze([txt], "para") if txt.strip() else None
        if pl is not None and pl.sufficient:
            comb = fuse(e_sc, pl, 0.4).combined
            out.append(ParaResult(i, pl.score, comb, pl.evidence[:3]))
            if pl.score >= 62 and e_sc < HIGH:
                layer_only.append(i)
            if pl.words >= 40:
                combined_vals.append(comb)
        else:
            out.append(ParaResult(i, None, None, []))
    hybrid = None
    if len(combined_vals) >= 4:
        hi = sum(1 for v in combined_vals if v >= HIGH)
        lo = sum(1 for v in combined_vals if v < LOW)
        t = len(combined_vals)
        if hi / t >= .25 and lo / t >= .25:
            hybrid = {"high": hi, "low": lo, "total": t}
    return Enhanced(doc, fusion, out, hybrid, hy, layer_only)
