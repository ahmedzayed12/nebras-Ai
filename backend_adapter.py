# -*- coding: utf-8 -*-
"""Guarded analysis plus local evidence review over the original v2.1 engine.

The SHA-256 guard keeps AI_Text_Inspector.pyw unchanged. Input guards remove
non-prose artefacts; subsequent repository layers correct evidence, review
paragraphs and aggregate their word-weighted scores. Pooled and intermediate
scores remain available for diagnostics. All scores are heuristic evidence
indices rather than calibrated probabilities of authorship.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.machinery
import importlib.util
import io
import math
import os
from pathlib import Path
import sys
import tempfile
from functools import lru_cache
from typing import Iterable
import zipfile

ROOT = Path(__file__).resolve().parent
ENGINE_PATH = ROOT / "AI_Text_Inspector.pyw"
ENGINE_SHA256 = "3692e8f4f4485460e1eb3f448a9f09f58c50685ceeb8ce49bb8921fad24a57f8"
SUPPORTED = {".docx", ".txt", ".md", ".text", ".pdf"}
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_BATCH_FILES = 250
MAX_BATCH_BYTES = 120 * 1024 * 1024


@lru_cache(maxsize=1)
def get_engine():
    actual = hashlib.sha256(ENGINE_PATH.read_bytes()).hexdigest()
    if actual != ENGINE_SHA256:
        raise RuntimeError(
            "تغيّرت بصمة محرك التحليل الأصلي. توقف التحليل لحماية تطابق النتائج. "
            "استعد ملف AI_Text_Inspector.pyw الأصلي (v2.1) من الحزمة."
        )
    name = "ai_text_inspector_v21_unchanged"
    if name in sys.modules:
        return sys.modules[name]
    loader = importlib.machinery.SourceFileLoader(name, str(ENGINE_PATH))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None:
        raise RuntimeError("تعذّر تحميل المحرك الأصلي.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses uses sys.modules while classes are created
    try:
        loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def filename_only(name: str) -> str:
    # Uploaded files / ZIP entries must never dictate an output directory.
    name = str(name).replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if c.isprintable() and c not in "/\\\x00")
    if not name or name in {".", ".."}:
        raise ValueError("اسم الملف غير صالح.")
    return name


# Photographs placed in the body. A real photograph (JPEG, not a PNG chart / diagram / vector) that sits in the document
# is first-hand material: somebody stood in front of something and photographed it. That is evidence of human involvement
# in producing the document, NOT proof of who typed its sentences, so it is small, bounded, one-directional (no photos
# says nothing) and is withheld when the file itself names a generator. Fixed constants, not fitted to any file.
PHOTO_MIN_BYTES = 20_000
PHOTO_EACH, PHOTO_CAP = -0.1, -0.4


def photo_count(content: bytes) -> int:
    """Distinct JPEG photographs actually referenced by the body of a DOCX (0 when unreadable)."""
    try:
        import hashlib
        zf = zipfile.ZipFile(io.BytesIO(content))
        rels = zf.read("word/_rels/document.xml.rels").decode("utf8", "ignore")
        body = zf.read("word/document.xml").decode("utf8", "ignore")
        used = set(_re.findall(r'<a:blip[^>]*?r:embed="([^"]+)"', body))
        seen = set()
        for m in _re.finditer(r"<Relationship\b[^>]*>", rels):
            tag = m.group(0)
            rid = _re.search(r'\bId="([^"]+)"', tag)
            tgt = _re.search(r'\bTarget="([^"]+)"', tag)
            if not rid or not tgt or rid.group(1) not in used:
                continue
            path = "word/" + tgt.group(1).lstrip("/") if not tgt.group(1).startswith("/") else tgt.group(1).lstrip("/")
            if not path.lower().endswith((".jpg", ".jpeg")):
                continue
            data = zf.read(path)
            if len(data) >= PHOTO_MIN_BYTES and data[:3] == b"\xff\xd8\xff":
                seen.add(hashlib.sha1(data).hexdigest())
        return len(seen)
    except Exception:
        return 0


def _photo_evidence(report):
    """Document-level human-side evidence from embedded photographs. Returns the log-odds change (<= 0)."""
    n = int(getattr(report, "photos_n", 0) or 0)
    report.photo_note = None
    if n <= 0 or explicit_generator(getattr(report, "meta", None)) > 0:
        return 0.0
    d = max(PHOTO_CAP, PHOTO_EACH * n)
    feats = dict(getattr(report, "features", None) or {})
    report.z = float(report.z) + d
    feats["photos"] = (min(1.0, n / 4.0), PHOTO_CAP, d)
    report.features = feats
    _rescore(report)
    report.photo_note = (n, d)
    return d


def _attach(report, mm, forensics=None, paste=None, deck=None, paras=None):
    report.math = mm
    report.doc_paras = paras
    report.format_forensics = forensics
    report.paste = paste
    report.deck = deck
    return _enhance(report)


# Word "edit history" (edit time, revisions, comments, rsid sessions) is reset by a plain Save-As / another editor.
# Its swing must not exceed what the TEXT can do: both history features are bounded.
META_HUMAN_MIN, META_GEN_MAX = -0.25, 0.1
PROSE_ONLY_EXCLUDE = {"photos", "meta_human", "meta_gen", "fmt_incons", "fmt_perfect", "mixed_conv", "err_density", "clean", "ortho"}


# Explicit generator traces (a code library or an AI product named as the author, or the python-docx template date).
# Pandoc / "unnamed" are NOT listed: researchers use them for ordinary human work.
EXPLICIT_GEN_NAMES = ("python-docx", "openpyxl", "docx4j", "aspose", "apache poi", "chatgpt", "openai", "anthropic")
EXPLICIT_GEN_TEMPLATE_DATE = "2013-12-23"
EXPLICIT_GEN_FLOOR = 0.5     # an explicit trace is first-hand provenance (the file says a program wrote it): at least this share counts
                             # even when the wording is neutral; the rest is earned only by wording that agrees (up to 100%)


def explicit_generator(meta) -> float:
    """0..1: how explicitly the file's own data says a program (not a person in Word) produced it."""
    meta = meta or {}
    names = (str(meta.get("creator") or "") + " " + str(meta.get("last_modified_by") or "")).lower()
    v = 0.55 if any(g in names for g in EXPLICIT_GEN_NAMES) else 0.0
    if str(meta.get("created") or "").startswith(EXPLICIT_GEN_TEMPLATE_DATE):
        v += 0.35
    return min(1.0, v)


def _academic_for(report):
    """(module, layer, topic) of the academic-prose layer; computed once per report. It depends only on the text."""
    cached = getattr(report, "_acad_cache", None)
    if cached is not None:
        return cached
    try:
        import academic_signals as acad
        _dp = getattr(report, "doc_paras", None) or []
        if not _dp:
            eng = get_engine()
            _dp = [eng.Para(text=t) for t in report.texts]
            eng.assign_roles(_dp, bool(getattr(report, "is_docx", False)))
        topic = acad.topic_terms([getattr(q, "text", "") for q in _dp if getattr(q, "role", "") == "heading"],
                                 extra=[getattr(q, "text", "") for q in _dp if getattr(q, "role", "") == "keywords"])
        out = (acad, acad.analyze([t for t in report.texts if t and t.strip()], topic), topic)
    except Exception:
        out = (None, None, None)
    report._acad_cache = out
    return out


def wording_corroboration(report) -> float:
    """0..1: how far the WORDING alone, judged by the academic-prose layer (habits calibrated on external human and LLM
    corpora, never on the file's history), points the same way as a generator trace. The engine's own wording features are
    not used here: they reach +2..+3 log-odds on ordinary human prose too, so they cannot say whether the text agrees."""
    layer = _academic_for(report)[1]
    return float(getattr(layer, "strength", 0.0) or 0.0) if layer is not None else 0.0


def _stabilize_meta(report):
    """Edit-history traces are bounded (Save-As resets them). A generator trace keeps its small bound too, EXCEPT an
    explicit one (python-docx, template date...) when independent wording evidence agrees with it: then it counts in
    proportion to that agreement. Returns the change in log-odds."""
    feats = dict(getattr(report, "features", None) or {})
    delta = 0.0
    report.meta_gen_note = None
    for k, lo, hi in (("meta_human", META_HUMAN_MIN, 0.0), ("meta_gen", 0.0, META_GEN_MAX)):
        if k in feats:
            v, w, c = feats[k]
            if k == "meta_gen":
                expl = explicit_generator(getattr(report, "meta", None))
                if expl > 0 and c > hi:
                    corr = wording_corroboration(report)
                    share = EXPLICIT_GEN_FLOOR + (1.0 - EXPLICIT_GEN_FLOOR) * corr
                    allowed = hi + share * max(0.0, w * expl - hi)
                    hi = max(hi, min(c, allowed))
                    report.meta_gen_note = (share, getattr(_academic_for(report)[1], "fired", 0), hi, corr)
            nc = max(lo, min(hi, c))
            if nc != c:
                delta += nc - c
                feats[k] = (v, w, nc)
    if delta:
        report.features = feats
        report.z = float(report.z) + delta
        _rescore(report)
        _relabel_meta_reasons(report, feats)
    return delta


def _relabel_meta_reasons(report, feats):
    """The engine printed the file-history lines at their raw size; show the size that actually counted."""
    out = []
    for r in (report.reasons or []):
        t = str(r)
        for key, frag in (("meta_gen", "بيانات الملف تدل على توليد برمجي"), ("meta_human", "آثار تحرير بشري حقيقية")):
            if frag in t and key in feats and t.lstrip()[:1] in "+-":
                c = feats[key][2]
                if abs(c) < 0.05:
                    t = None
                else:
                    t = "   %+.1f  %s" % (c, t.split("  ", 2)[-1].strip())
                break
        if t is not None:
            out.append(t)
    report.reasons = out


def prose_only_score(report) -> float:
    """AI score from the wording alone: engine features minus file history, formatting and typing slips."""
    try:
        W = get_engine().load_weights()
        z = W["bias"] + sum(c for k, (v, w, c) in (report.features or {}).items() if k not in PROSE_ONLY_EXCLUDE)
        conf = float(getattr(report, "confidence", 1.0) or 0.0)
        return 100.0 / (1.0 + math.exp(-z * conf))
    except Exception:
        return 0.0


def _rescore(report):
    conf = float(getattr(report, "confidence", 1.0) or 0.0)
    if conf:
        report.score = 100.0 / (1.0 + math.exp(-float(report.z) * conf))


def _spacing_insensitive_style(report, paras):
    """Use the original style rules on a matching view, then restore source offsets."""
    from dataclasses import replace
    from matching_text import matching_text_map
    eng = get_engine()
    views = [matching_text_map(p.text) for p in paras]
    if all(text == p.text for p, (text, _) in zip(paras, views)):
        return
    match_paras = [replace(p, text=text) for p, (text, _) in zip(paras, views)]

    class MatchSink:
        def __init__(self):
            self.items = []

        def add(self, kind, i, text, start, end, note=""):
            positions = views[i][1]
            original_start = positions[start]
            original_end = positions[end - 1] + 1 if end > start else original_start
            self.items.append(eng._mk(kind, i, paras[i].text, original_start, original_end, note))

    sink = MatchSink()
    sentences = {i: eng.split_sentences(p.text) for i, p in enumerate(match_paras)
                 if p.role in eng.PROSE_ROLES and p.text.strip()}
    counts, _ = eng.check_style_markers(match_paras, sink, sentences)
    words = sum(len(eng.WORD_RE.findall(p.text)) for p in match_paras if p.role in eng.PROSE_ROLES)
    kw = max(words, 1) / 1000.0
    body_sentences = sum(1 for i, p in enumerate(match_paras) if p.role == "body"
                         for a, b in sentences.get(i, []) if eng.WORD_RE.findall(p.text[a:b]))
    tri_share = counts["tricolon_sents"] / body_sentences if body_sentences >= 8 else 0.0
    tune = eng.TUNE
    values = {
        "ai_lexicon": eng._sat(counts["lex"] / kw, tune["lex_scale"]),
        "ai_vocab": eng._sat((counts["voc_s"] + 0.25 * counts["voc_m"]) / kw, tune["vocab_scale"]),
        "contrast": eng._sat(counts["contrast"] / kw, tune["contrast_scale"]),
        "vague_attr": eng._sat(counts["vague"] / kw, tune["vague_scale"]),
        "signpost": eng._sat(counts["signpost"] / kw, tune["signpost_scale"]),
        "tricolon": eng._clamp((tri_share - tune["tricolon_lo"]) / (tune["tricolon_hi"] - tune["tricolon_lo"])),
        "generic_phr": eng._clamp((counts["gen"] / kw - tune["gen_lo"]) / (tune["gen_hi"] - tune["gen_lo"])) if words >= 120 else 0.0,
    }
    feats = dict(report.features)
    delta = 0.0
    for key, value in values.items():
        _, weight, previous = feats[key]
        feats[key] = (value, weight, value * weight)
        delta += value * weight - previous
    report.features = feats
    report.z += delta
    report.findings = [f for f in report.findings if f.kind not in eng._PARA_W] + sink.items
    report.complete_style_findings = True
    report.ctx.update({"tricolon_share": tri_share, "lex": counts["lex"], "contrast": counts["contrast"],
                       "vague": counts["vague"], "signpost": counts["signpost"], "gen_density": counts["gen"] / kw,
                       "voc_density": (counts["voc_s"] + 0.25 * counts["voc_m"]) / kw})
    _rescore(report)
    # Replace existing feature reasons; no diagnostic section is added to the UI/PDF.
    report.reasons = eng.build_reasons(report, report.ctx)
    report.matching_style_counts = dict(counts)


TEXT_SOURCE_ERRORS = {"typo_en", "ar_orth", "repeated_word", "lower_start", "unbalanced", "repeat_punct", "mixed_punct_script"}


def _text_error_review(report, paras):
    """Spacing is document hygiene, not independent evidence of human authorship.

    Retain quality findings, but derive the existing human-error features from
    language/typing findings in authored prose. Local traces stay local.
    """
    from collections import Counter, defaultdict
    eng = get_engine()
    stored = Counter(f.kind for f in report.findings if f.kind in TEXT_SOURCE_ERRORS)
    saturated = {k for k, n in stored.items() if n >= eng.CAP_PER_KIND}
    if saturated:
        class ErrorSink:
            def __init__(self):
                self.items = []

            def add(self, kind, i, text, start, end, note=""):
                if kind in saturated:
                    if kind == "lower_start":
                        match = _re.search(r"([A-Za-z]+)\.\s*$", text[:start])
                        if match and match.group(1).lower() in _ABBREV:
                            return
                    self.items.append(eng._mk(kind, i, text, start, end, note))

        sink = ErrorSink()
        for i, p in enumerate(paras):
            if p.role in ("body", "list"):
                eng.check_paragraph(i, p.text, p, sink)
        report.findings = [f for f in report.findings if f.kind not in saturated] + sink.items
    counts = defaultdict(Counter)
    word_counts = {i: len(eng.WORD_RE.findall(p.text)) for i, p in enumerate(paras)
                   if p.role in ("body", "list") and p.text.strip()}
    for f in report.findings:
        if f.para in word_counts and f.kind in TEXT_SOURCE_ERRORS:
            counts[f.para][f.kind] += 1
    words = sum(word_counts.values())
    kw = max(words, 1) / 1000.0
    total = sum(eng.KINDS[k][2] * count for cs in counts.values() for k, count in cs.items())
    orth = sum(cs["ar_orth"] for cs in counts.values())
    feats = dict(report.features)
    values = {"err_density": eng._sat(total / kw, 5.0), "ortho": eng._sat(orth / kw, 5.0), "clean": 0.0}
    delta = 0.0
    for key, value in values.items():
        _, weight, previous = feats[key]
        feats[key] = (value, weight, value * weight)
        delta += value * weight - previous
    report.features = feats
    report.z += delta
    report.text_error_shifts = {}
    for i, n in word_counts.items():
        density = sum(eng.KINDS[k][2] * count for k, count in counts[i].items()) / (max(n, 1) / 1000.0)
        ar_density = counts[i]["ar_orth"] / (max(n, 1) / 1000.0)
        report.text_error_shifts[i] = (eng._sat(density, 5.0) * feats["err_density"][1]
                                     + eng._sat(ar_density, 5.0) * feats["ortho"][1])
    report.text_source_error_counts = dict(sum(counts.values(), Counter()))
    report.text_error_kinds = TEXT_SOURCE_ERRORS
    report.ctx["d_err"] = total / kw
    report.stats["أخطاء مرجّحة لكل 1000 كلمة"] = round(total / kw, 1)
    _rescore(report)
    report.reasons = eng.build_reasons(report, report.ctx)


import re as _re
_APA_DECIMAL = _re.compile(r"[ \t\u00A0]\.\d")                       # "p < .001", "r = .34": a decimal without leading zero
_BRACED_ITEM = _re.compile(r"[\{\[\(<][^\s\{\}\[\]\(\)<>]{1,14}[\}\]\)>]")   # "{h,c}", "(i,j)", "[x,y]": notation, not prose


def _lists_neutral(report):
    """The engine counts every sentence containing "A, B and C" as a balanced rhetorical triad, without checking that it is
    balanced. A list that NAMES things (taxa, places, acronyms, numbers) is forced by the subject, and a comma-separated stretch of
    clauses is not a triad; those sentences leave the count and the feature is re-derived."""
    feats = dict(getattr(report, "features", None) or {})
    if "tricolon" not in feats or not getattr(report, "findings", None):
        return 0.0
    eng = get_engine()
    texts = list(getattr(report, "texts", None) or [])
    tri = [f for f in report.findings if f.kind == "ai_tricolon" and 0 <= f.para < len(texts)]
    share = float((getattr(report, "ctx", None) or {}).get("tricolon_share", 0.0) or 0.0)
    if not tri or share <= 0 or (len(tri) >= eng.CAP_PER_KIND
                               and not getattr(report, "complete_style_findings", False)):
        return 0.0
    try:
        import academic_signals as acad
    except Exception:
        return 0.0
    sent_of, named = {}, set()
    spans_cache = {}
    for f in tri:
        spans = spans_cache.setdefault(f.para, eng.split_sentences(texts[f.para]))
        k = next((j for j, (a, b) in enumerate(spans) if a <= f.start < b), -1)
        key = (f.para, k)
        sent_of.setdefault(key, []).append(f)
    for key, fs in sent_of.items():
        if not any(acad.rhetorical_triad(texts[f.para][f.start:f.end], _academic_for(report)[2]) for f in fs):
            named.add(key)
    if not named:
        return 0.0
    n_tri = len(sent_of)
    nsent = n_tri / share
    new_share = (n_tri - len(named)) / nsent
    lo, hi = 0.10, 0.40
    v2 = max(0.0, min(1.0, (new_share - lo) / (hi - lo)))
    v, w, c = feats["tricolon"]
    feats["tricolon"] = (v2, w, v2 * w)
    delta = v2 * w - c
    drop = {id(f) for key in named for f in sent_of[key]}
    report.findings = [f for f in report.findings if id(f) not in drop]
    report.features = feats
    report.z = float(report.z) + delta
    report.lists_removed = len(named)
    _rescore(report)
    out = []
    for r in (report.reasons or []):
        t = str(r)
        if "قوائم ثلاثية متوازنة" in t and t.lstrip()[:1] in "+-":
            if v2 * w < 0.05:
                continue
            t = "   %+.1f  %.0f%% من الجمل تحوي قوائم ثلاثية متوازنة (أ، ب، وج)" % (v2 * w, 100 * new_share)
        out.append(t)
    report.reasons = out
    return delta


_ABBREV = frozenset(("spp", "sp", "var", "subsp", "ssp", "cf", "al", "vs", "etc", "approx", "ca", "fig", "figs", "eq", "eqs", "no", "nos",
                     "vol", "pp", "ed", "eds", "sensu", "syn", "st", "dr", "prof", "mr", "mrs", "inc", "ltd", "co", "viz", "ie", "eg"))


def _notation_neutral(report):
    """Statistics and formula notation is not a typing slip: APA decimals (" .001") and subscript lists ("{h,c}") were read as
    spacing errors, i.e. as HUMAN evidence. Those findings are removed and the error-density features re-derived."""
    feats = dict(getattr(report, "features", None) or {})
    if "err_density" not in feats or not getattr(report, "findings", None):
        return 0.0
    eng = get_engine()
    texts = list(getattr(report, "texts", None) or [])
    drop_w = 0.0
    dropped = []
    for f in report.findings:
        if f.kind not in ("space_before_punct", "no_space_after_punct", "lower_start") or not (0 <= f.para < len(texts)):
            continue
        t = texts[f.para]
        if f.kind == "space_before_punct":
            hit = bool(_APA_DECIMAL.match(t[f.start:f.end + 1]))
        elif f.kind == "lower_start":
            m = _re.search(r"([A-Za-z]+)\.\s*$", t[:f.start])
            hit = bool(m) and m.group(1).lower() in _ABBREV      # "Fusarium spp. and ...": not the end of a sentence
        else:
            hit = any(m.start() <= f.start < m.end() for m in _BRACED_ITEM.finditer(t))
        if hit:
            drop_w += eng.KINDS[f.kind][2]
            dropped.append(f)
    if not dropped:
        return 0.0
    err_w = sum(eng.KINDS[k][2] * n for k, n in report.count_by_kind().items() if eng.KINDS[k][1] in eng.ERR_CATS)
    d_old = float((getattr(report, "ctx", None) or {}).get("d_err", 0.0) or 0.0)
    if err_w <= 0 or d_old <= 0:
        return 0.0
    kw = err_w / d_old
    d_new = max(0.0, err_w - drop_w) / kw
    new_v = {"err_density": 1.0 - math.exp(-d_new / 5.0), "clean": math.exp(-d_new / 3.0)}
    delta = 0.0
    for k, v2 in new_v.items():
        if k in feats and feats[k][2] != 0:
            v, w, c = feats[k]
            feats[k] = (v2, w, v2 * w)
            delta += v2 * w - c
    if abs(delta) < 1e-9:
        return 0.0
    report.findings = [f for f in report.findings if f not in dropped]
    report.features = feats
    report.z = float(report.z) + delta
    report.notation_removed = len(dropped)
    _rescore(report)
    out = []
    for r in (report.reasons or []):
        t = str(r)
        if "أخطاء مسافات/ترقيم/إملاء بشرية الطابع" in t and t.lstrip()[:1] in "+-":
            c = feats["err_density"][2]
            if abs(c) < 0.05:
                continue
            t = "   %+.1f  أخطاء مسافات/ترقيم/إملاء بشرية الطابع (%.1f لكل 1000 كلمة)" % (c, d_new)
        out.append(t)
    report.reasons = out
    return delta


# In a PDF, spacing / punctuation "slips", quote styles and blank lines are produced by the PDF writer and by text
# extraction (line breaks, hyphenation, ligatures, columns) -- not by the writer's hands. They are neutral evidence.
PDF_EXTRACTION_FEATURES = ("err_density", "clean", "mixed_conv", "fmt_incons")


def _pdf_neutralize(report):
    feats = dict(getattr(report, "features", None) or {})
    removed = 0.0
    for k in PDF_EXTRACTION_FEATURES:
        if k in feats and feats[k][2] != 0:
            v, w, c = feats[k]
            removed += c
            feats[k] = (0.0, w, 0.0)
    if abs(removed) < 1e-9:
        return 0.0
    report.features = feats
    report.z = float(report.z) - removed
    _rescore(report)
    return -removed


_YEAR = __import__("re").compile(r"\b(?:19|20)\d\d[a-z]?\b")
GATE_MIN_UNCITED = 5      # fewer than this: the citation gap is not a decisive signal anyway
GATE_MALFORMED = 0.35     # share of complained-about entries that are not whole references
_ENTRY = __import__("re").compile(r"^\W{0,2}(?:\d{1,3}[.\]\)]\s*)?[A-Z\u00C0-\u024F][\w'\u2019\-]+")


def _whole_reference(t: str) -> bool:
    """A whole entry starts with a name (or a number) and carries a year; a fragment of a wrapped entry starts in the
    middle ('(2019). Title...', 'and Park, S.') or lacks the year."""
    t = (t or "").strip()
    return bool(_ENTRY.match(t)) and bool(_YEAR.search(t))


def _pdf_reference_gate(report) -> bool:
    """PDF text can split one reference over several lines. Each fragment then looks like a reference nobody cites
    ('cite_gap'), which is an extraction artefact, not evidence. Before trusting the signal, check the entries it
    complains about: if many of them have no year, the list was fragmented, so the signal is set to neutral."""
    feats = dict(getattr(report, "features", None) or {})
    if "cite_gap" not in feats or feats["cite_gap"][2] <= 0:
        return False
    uncited = [f for f in (getattr(report, "findings", None) or []) if f.kind == "ev_uncited"]
    if len(uncited) < GATE_MIN_UNCITED:
        return False
    texts = list(getattr(report, "texts", None) or [])
    entries = [texts[f.para] for f in uncited if 0 <= f.para < len(texts)]
    if not entries:
        return False
    malformed = sum(1 for t in entries if not _whole_reference(t)) / len(entries)
    report.pdf_ref_quality = 1.0 - malformed
    if malformed < GATE_MALFORMED:
        return False
    v, w, c = feats["cite_gap"]
    feats["cite_gap"] = (0.0, w, 0.0)
    report.features = feats
    report.z = float(report.z) - c
    _rescore(report)
    return True


# Structural slips: a reference list nobody cites, a table/figure nobody mentions. They are the ordinary mistakes of a
# hurried human writer (and a pasted AI text carries the same gap), so by themselves they say nothing about WHO wrote
# the prose. They are kept visible in the findings, but they may not push the score toward AI.
STRUCTURAL_SLIPS = ("cite_gap", "caption_orphan")


def _structural_neutral(report):
    feats = dict(getattr(report, "features", None) or {})
    removed = 0.0
    for k in STRUCTURAL_SLIPS:
        if k in feats and feats[k][2] > 0:
            v, w, c = feats[k]
            removed += c
            feats[k] = (v, w, 0.0)
    if removed <= 1e-9:
        return 0.0
    report.features = feats
    report.z = float(report.z) - removed
    _rescore(report)
    report.structural_removed = removed
    report.score_neutral = float(report.score)       # the index the later layers start from
    note = "   0.0  %s (بنيوي: قد يكون خطأً بشريًا فلا يُحتسب مؤيدًا للذكاء الاصطناعي)"
    out = []
    for r in (report.reasons or []):
        t = str(r)
        if t.lstrip().startswith(("+", "-")) and ("قائمة المراجع فيها" in t or "تسميات الجداول/الأشكال" in t):
            out.append(note % t.split("  ", 2)[-1].strip())
        else:
            out.append(r)
    report.reasons = out
    return -removed


def _pdf_verdict(report, eng):
    """A PDF carries the final text only: no edit history, no hand formatting, no typing slips. Its verdict is therefore
    worded as a text-only estimate, and it never uses the two categorical wordings ('likely generated' /
    'strongly leans to AI') that only the full Word file can justify. The number itself is not changed."""
    base, color = eng.make_verdict(report.score, report.words)
    if report.score >= 65:
        base = "قرائن أسلوبية مرتفعة نحو الذكاء الاصطناعي"
    return base + " (PDF: من النص وحده)", color


def _deck_layout_fix(report):
    """Inside a slide deck, a bullet without a full stop or a blank line between text boxes is layout, not a human
    slip. Remove exactly that part of the engine's 'human error' credit (other typos still count)."""
    eng = get_engine()
    feats = dict(getattr(report, "features", None) or {})
    ctx = getattr(report, "ctx", {}) or {}
    removed = 0.0
    err_w = sum(eng.KINDS[f.kind][2] for f in report.findings if f.kind in eng.KINDS and eng.KINDS[f.kind][1] in eng.ERR_CATS)
    layout_w = sum(eng.KINDS[f.kind][2] for f in report.findings if f.kind in ("no_final_punct",))
    d_err = float(ctx.get("d_err", 0.0) or 0.0)
    if err_w > 0 and layout_w > 0 and "err_density" in feats:
        d_new = d_err * max(0.0, err_w - layout_w) / err_w
        for k, fv in (("err_density", 1.0 - math.exp(-d_new / 5.0)), ("clean", math.exp(-d_new / 3.0))):
            if k in feats:
                v, w, c = feats[k]
                removed += c - w * fv
                feats[k] = (fv, w, w * fv)
    if "fmt_incons" in feats and feats["fmt_incons"][2] < 0:        # blank lines between slide boxes
        v, w, c = feats["fmt_incons"]
        removed += c
        feats["fmt_incons"] = (0.0, w, 0.0)
    if abs(removed) < 1e-9:
        return 0.0
    report.features = feats
    report.z = float(report.z) - removed
    _rescore(report)
    return -removed


# "Container" features: they describe the Word FILE (edit time, tracked changes, template formatting, quote styles),
# not the prose. A text drafted by an LLM, pasted into a university template and then reviewed with a supervisor
# carries all of them. In v2.1 they can add up to -5.2 logits and silently veto any amount of stylistic evidence.
# When (and only when) the independent academic-prose layer fires, their combined weight is capped at CONTAINER_CAP.
CONTAINER_KEYS = ("meta_human", "fmt_incons", "mixed_conv")
CONTAINER_CAP = -1.0
# Paste artefacts (format_forensics.paste_artifacts): logit added per unit of evidence, its ceiling, and the minimum
# evidence needed (one strong chat-UI trace on >= 30% of paragraphs, or several weaker ones together).
PASTE_LOGIT = 1.2
PASTE_MAX_LOGIT = 3.0
PASTE_MIN = 0.8


# Paste layer, scaled by what the wording says. Fixed a-priori constants: below 10% of the text reading as LLM prose is noise
# (ordinary human prose has a few such paragraphs); at 50% or more, half the document is LLM prose and the formatting traces are
# fully corroborated. Direct chat-window fingerprints keep at least half their weight whatever the wording says.
WORDING_HOT_SCORE = 50
WORDING_NOISE, WORDING_FULL = 0.10, 0.50
PASTE_DIRECT_FLOOR = 0.5


def wording_share(report) -> float:
    """0..1: how much of the text reads as LLM prose, judged by the independent academic-prose layer (never by the engine's own
    paragraph scores, which run high on ordinary human prose). Two parts multiplied: the share of words sitting in paragraphs
    that layer scores >= 50 (continuous, so rewriting part of a document lowers it part of the way), and the layer's own
    convergence strength (0 when fewer than two independent habits converge on the document as a whole)."""
    acad, layer, topic = _academic_for(report)
    if acad is None or layer is None:
        return 0.0
    strength = float(getattr(layer, "strength", 0.0) or 0.0)
    if strength <= 0:
        return 0.0
    ps = list(getattr(report, "wording_map", None) or [])
    texts = list(getattr(report, "texts", None) or [])
    tot = hot = 0.0
    for i, _, n in ps:
        if not (0 <= i < len(texts)):
            continue
        t = acad.para_score(texts[i], topic=topic)
        tot += n
        if t is not None and t >= WORDING_HOT_SCORE:
            hot += n
    if tot <= 0:
        return 0.0
    return strength * max(0.0, min(1.0, (hot / tot - WORDING_NOISE) / (WORDING_FULL - WORDING_NOISE)))


def _paste_effective(report, pa):
    """(effective total, wording share, [lines], raw total). Each trace counts in proportion to how much it says about the
    words: direct fingerprints at least half, text-level marks fully, paragraph formatting only as far as the wording agrees."""
    parts = getattr(pa, "parts", None)
    if not parts:
        return float(pa.total), 1.0, list(pa.evidence), float(pa.total)
    import format_forensics as _ff
    share = wording_share(report)
    eff, rows = 0.0, []
    for k, v in parts.items():
        kind = _ff.KIND.get(k, "format")
        f = 1.0 if kind == "text" else max(PASTE_DIRECT_FLOOR, share) if kind == "direct" else share
        eff += v * f
        rows.append((k, v, f))
    lines = ["   +%.1f  %s%s" % (v * f, _ff.LABELS[k], "" if f >= 0.995 else " (تُحتسب بنسبة %.0f%% لأن الصياغة تتفق معها بهذا القدر)" % (100 * f))
             for k, v, f in rows if v * f >= 0.05]
    return eff, share, lines, float(pa.total)


def _recalibrate_container(report, strength: float = 1.0):
    """Relax the human-trace (container) evidence toward CONTAINER_CAP in PROPORTION to how strongly the independent
    wording evidence converges (strength 0..1). Full convergence (3 habits) = the full cap, as before; two habits
    relax it only partly, so partial wording evidence cannot erase most of the human evidence in one step."""
    feats = getattr(report, "features", None) or {}
    raw = sum(feats[k][2] for k in CONTAINER_KEYS if k in feats and feats[k][2] < 0)
    report.container_raw = raw
    strength = max(0.0, min(1.0, strength))
    report.container_eff = raw
    if raw >= CONTAINER_CAP - 1e-9 or strength <= 0:
        return False
    target = raw + strength * (CONTAINER_CAP - raw)
    report.container_eff = target
    z = float(report.z) - raw + target
    conf = float(getattr(report, "confidence", 1.0) or 0.0)
    report.z = z
    report.score = 100.0 / (1.0 + math.exp(-z * conf)) if conf else report.score
    return True


def _lift_paras(report, mod, conf, **kw):
    w = 0.9 * conf
    new_ps = []
    for i, sc, n in report.para_scores:
        tp = mod.para_score(report.texts[i], **kw) if 0 <= i < len(report.texts) else None
        if tp is not None and tp > sc:
            sc = round(sc + w * (tp - sc), 0)
        new_ps.append((i, sc, n))
    report.para_scores = new_ps


def _tech_input_texts(report, ts):
    """Prose for the technical layer: headings are structure, not prose."""
    paras = getattr(report, "doc_paras", None) or []
    # prose only: headings, captions, table cells, the keywords line and front matter are not sentences the author drafted
    skip = {i for i, q in enumerate(paras) if getattr(q, "role", "") in ("heading", "caption", "table", "keywords", "front", "reference")} \
        if len(paras) == len(report.texts) else set()
    return [t for i, t in enumerate(report.texts) if t and t.strip() and i not in skip and not ts._REFLINE.match(t)]


def _generator_to_paragraphs(report, logit: float) -> bool:
    """An explicit generator trace is about the whole file (the script put EVERY paragraph there), so it is a prior shared by
    all paragraphs: their log-odds rise by the share of it that counted. The map is anchored to the index afterwards."""
    if logit <= 0.05 or not report.para_scores:
        return False
    out = []
    for i, sc, n in report.para_scores:
        p = min(0.995, max(0.005, sc / 100.0))
        out.append((i, round(100.0 / (1.0 + math.exp(-(math.log(p / (1 - p)) + logit))), 0), n))
    report.para_scores = out
    return True


def _file_evidence_to_paragraphs(report, paras=None) -> bool:
    feats = getattr(report, "features", None) or {}
    cont = getattr(report, "container_eff", None)
    if cont is None:
        cont = sum(feats[k][2] for k in CONTAINER_KEYS if k in feats and feats[k][2] < 0)
    local_text = getattr(report, "text_error_shifts", None)
    local_keys = {"err_density", "ortho"} if local_text is not None else set()
    other = sum(c for k, (v, w, c) in feats.items()
                if k in PROSE_ONLY_EXCLUDE and k not in CONTAINER_KEYS and k not in local_keys and c < 0)
    shift = (min(0.0, cont) + other) * float(getattr(report, "confidence", 1.0) or 0.0)
    if paras is not None and getattr(report, "is_docx", False) and len(paras) == len(report.texts):
        import friction
        # Metadata is a bounded file prior. Formatting differences are local:
        # a repeated template or pasted block cannot make every paragraph human.
        blocks = friction._block_index_set(getattr(report, "format_forensics", None))
        runs = friction._paste_runs(paras)
        blocks.update(set().union(*runs.values()) if runs else set())
        fmt, convention = set(), set()
        font_ranges = {}
        eng = get_engine()
        for f in report.findings:
            if not (0 <= f.para < len(paras)) or paras[f.para].role != "body":
                continue
            if f.kind in {"fmt_font", "fmt_size"}:
                font_ranges.setdefault(f.para, []).append((f.start, f.end))
            elif (f.kind in friction.FORMAT_SLIPS | friction.ALWAYS_HUMAN_FORMAT
                    and f.para not in blocks and not friction._is_heading_like(paras[f.para])):
                fmt.add(f.para)
            if eng.KINDS.get(f.kind, ("", "", 0))[1] == "convention":
                convention.add(f.para)
        # A small symbol or superscript in another font/size does not mean that
        # the author manually reformatted the surrounding paragraph.
        for i, ranges in font_ranges.items():
            end, covered = -1, 0
            for a, b in sorted(ranges):
                covered += max(0, b - max(a, end))
                end = max(end, b)
            if (covered >= 0.5 * len(paras[i].text) and i not in blocks
                    and not friction._is_heading_like(paras[i])):
                fmt.add(i)
        raw = sum(feats[k][2] for k in CONTAINER_KEYS if k in feats and feats[k][2] < 0)
        scale = min(1.0, max(0.0, cont / raw)) if raw < 0 else 0.0
        contribution = lambda key: min(0.0, feats.get(key, (0, 0, 0))[2]) * scale
        conf = float(getattr(report, "confidence", 1.0) or 0.0)
        container_shifts = {i: contribution("meta_human")
                            + (contribution("fmt_incons") if i in fmt else 0.0)
                            + (contribution("mixed_conv") if i in convention else 0.0)
                  for i, _, _ in report.para_scores}
        shifts = {i: conf * (other + container_shifts[i] + (local_text or {}).get(i, 0.0))
                  for i, _, _ in report.para_scores}
        report.file_shifts = shifts
        report.file_shift_uniform = len(set(shifts.values())) <= 1
        words = sum(n for _, _, n in report.para_scores)
        report.file_shift = sum(n * shifts[i] for i, _, n in report.para_scores) / max(words, 1)
        # Use the same eligible locations for the pooled Word evidence. The old
        # pooled score still charged every paragraph for one local difference,
        # then vetoed the corrected map through min(pooled, local).
        if words:
            effective = sum(n * container_shifts[i] for i, _, n in report.para_scores) / words
            delta = effective - min(0.0, cont)
            if delta > 1e-9:
                report.z += delta
                p = min(1 - 1e-12, max(1e-12, report.score / 100.0))
                report.score = 100.0 / (1.0 + math.exp(-(math.log(p / (1 - p)) + delta * conf)))
                report.container_eff = effective
                for key, locations in (("meta_human", None), ("fmt_incons", fmt), ("mixed_conv", convention)):
                    if key in feats:
                        v, w, _ = feats[key]
                        eligible = 1.0 if locations is None else sum(n for i, _, n in report.para_scores if i in locations) / words
                        report.features[key] = (v, w, contribution(key) * eligible)
                report.reasons = eng.build_reasons(report, report.ctx)
    else:
        conf = float(getattr(report, "confidence", 1.0) or 0.0)
        shifts = {i: shift + conf * (local_text or {}).get(i, 0.0) for i, _, _ in report.para_scores}
    if not report.para_scores or not any(d < -0.05 for d in shifts.values()):
        return False
    out = []
    for i, sc, n in report.para_scores:
        p = min(0.995, max(0.005, sc / 100.0))
        out.append((i, round(100.0 / (1.0 + math.exp(-(math.log(p / (1 - p)) + shifts[i]))), 0), n))
    report.para_scores = out
    if not hasattr(report, "file_shifts"):
        report.file_shift = shift
    return True


def _anchor_map_to_index(report) -> bool:
    """The paragraph map says WHERE in the text the suspicion is; the document index says HOW MUCH. Their levels must
    agree: the word-weighted mean of the paragraph scores is the expected share of suspicious text, so it cannot
    sit far above the document index (a "leans human" verdict beside "70% suspected paragraphs" contradicts itself).
    When it does, every paragraph moves down by the same log-odds amount (found by bisection) until the mean equals the
    index. The ORDER of paragraphs is untouched, strongly flagged paragraphs stay flagged, and the map is never raised."""
    ps = list(getattr(report, "para_scores", None) or [])
    tot = float(sum(n for _, _, n in ps))
    if not ps or tot <= 0:
        return False

    def lg(sc):
        p = min(0.995, max(0.005, sc / 100.0))
        return math.log(p / (1.0 - p))

    def mean(d):
        return sum(n * 100.0 / (1.0 + math.exp(-(lg(sc) + d))) for _, sc, n in ps) / tot

    target = float(report.score)
    if mean(0.0) <= target + 1.0:
        return False
    lo, hi = -12.0, 0.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        if mean(mid) > target:
            hi = mid
        else:
            lo = mid
    d = (lo + hi) / 2.0
    report.para_scores = [(i, round(100.0 / (1.0 + math.exp(-(lg(sc) + d))), 0), n) for i, sc, n in ps]
    report.map_anchor = d
    return True


def _score_stage(report, key, label):
    """Keep an observable audit trail without changing the scoring model."""
    stages = report.score_stages
    previous = stages[-1]["score"] if stages else float(report.score)
    ps = [(sc, n) for _, sc, n in getattr(report, "para_scores", []) if n > 0]
    words = sum(n for _, n in ps)
    stages.append({"key": key, "label": label, "score": float(report.score),
                   "delta": float(report.score) - previous,
                   "paragraph_mean": sum(sc * n for sc, n in ps) / words if words else None,
                   "paragraph_count": len(ps)})


def _complete_style_findings(report, paras):
    """A display limit must not suppress local evidence in the rest of a long file.

    The engine counts every match for its pooled features but stores only the
    first CAP_PER_KIND findings. Rescan saturated wording kinds with the same
    engine rules, before neutralization, so local scores and highlighting see
    all paragraphs. No engine globals, patterns or weights are changed.
    """
    from collections import Counter
    eng = get_engine()
    stored = Counter(f.kind for f in report.findings if f.kind in eng._PARA_W)
    saturated = {kind for kind, count in stored.items() if count >= eng.CAP_PER_KIND}
    if not saturated:
        return

    class WordingSink:
        def __init__(self):
            self.items = []

        def add(self, kind, i, text, start, end, note=""):
            if kind in saturated:
                self.items.append(eng._mk(kind, i, text, start, end, note))

    sink = WordingSink()
    spans = {i: eng.split_sentences(p.text) for i, p in enumerate(paras)
             if p.role in eng.PROSE_ROLES and p.text.strip()}
    eng.check_style_markers(paras, sink, spans)
    recovered = Counter(f.kind for f in sink.items)
    report.findings = [f for f in report.findings if f.kind not in saturated] + sink.items
    report.complete_style_findings = True
    report.style_storage_recovered = {kind: (stored[kind], recovered[kind]) for kind in saturated}


def _refresh_paragraph_evidence(report):
    """Local scores must use the findings that survived evidence corrections."""
    eng = get_engine()
    paras = getattr(report, "doc_paras", None)
    if paras is None:
        paras = [eng.Para(text=t) for t in report.texts]
        eng.assign_roles(paras, report.is_docx)
    # Punctuation, numbered steps and markup are valid human writing too;
    # they cannot create local authorship evidence by themselves.
    local_findings = [f for f in report.findings
                      if f.kind not in {"ai_enum", "ai_emdash", "ai_dash", "ai_markdown"}]
    report.para_scores = eng.paragraph_scores(paras, local_findings)
    report.wording_map = list(report.para_scores)
    note = getattr(report, "meta_gen_note", None)
    if note:
        _generator_to_paragraphs(report, note[2])
    return paras


def _topic_vocab_neutral(report, paras):
    """Topic vocabulary is required by the subject, not an independent style trace."""
    import academic_signals as acad
    eng = get_engine()
    topic = acad.topic_terms([p.text for p in paras if p.role == "heading"],
                             extra=[p.text for p in paras if p.role == "keywords"])
    if not topic:
        return
    strong = medium = prose_words = 0
    for p in paras:
        if p.role in eng.PROSE_ROLES:
            prose_words += len(eng.WORD_RE.findall(p.text))
        if p.role in ("body", "list"):
            strong += sum(acad._stem(m.group()) not in topic for m in eng.RX_VOC_STRONG.finditer(p.text))
            medium += sum(acad._stem(m.group()) not in topic for m in eng.RX_VOC_MED.finditer(p.text))
    feats = dict(report.features)
    v, w, old = feats["ai_vocab"]
    density = (strong + 0.25 * medium) / (max(prose_words, 1) / 1000.0)
    new_v = min(v, eng._sat(density, eng.TUNE["vocab_scale"]))
    feats["ai_vocab"] = (new_v, w, new_v * w)
    report.features = feats
    report.z += new_v * w - old
    report.ctx["voc_density"] = density
    report.findings = [f for f in report.findings if not (
        f.kind == "ai_vocab" and 0 <= f.para < len(report.texts)
        and acad._stem(report.texts[f.para][f.start:f.end]) in topic)]
    _rescore(report)
    # Keep the existing reason format, but use the contribution actually counted.
    reasons = []
    for reason in report.reasons:
        if "مفردات أكاديمية-مولَّدة شائعة" in str(reason):
            if new_v * w < 0.2:
                continue
            reason = "   %+.1f  %s" % (new_v * w, eng.REASON_TXT["ai_vocab"](report.ctx))
        reasons.append(reason)
    report.reasons = reasons


def _corroborated_style(layer):
    """Require wording convergence; layout, cleanliness and repetition cannot open this gate."""
    if not layer.sufficient or layer.score is None:
        return False
    semantic = {s.key for s in layer.signals if s.key in {"stock", "frames", "connectors"}
                and s.z is not None and s.z >= 0.5}
    return len(semantic) >= 2 and layer.score >= 55


def _local_style_review(report, paras):
    """Reuse the repository's bilingual, local stylometry with explicit evidence gates."""
    import ai_signals
    eng = get_engine()
    prose = [p.text for p in paras if p.role in ("body", "list")]
    topic = _academic_for(report)[2]
    layer = ai_signals.analyze(prose, topic=topic)
    report.local_style = layer
    report.style_spans = {}
    if not _corroborated_style(layer):
        return
    # Local evidence is evaluated even when the pooled score is already above
    # this layer's estimate. Otherwise paragraph aggregation loses the very
    # evidence that made the document score high.
    report.score = max(report.score, ai_signals.fuse(report.score, layer).combined)
    revised = []
    for i, sc, n in report.para_scores:
        pl = ai_signals.analyze([report.texts[i]], "para", topic=topic)
        if _corroborated_style(pl):
            new = ai_signals.fuse(sc, pl).combined
            if new > sc:
                for a, b in eng.split_sentences(report.texts[i]):
                    sl = ai_signals.analyze([report.texts[i][a:b]], "para", topic=topic)
                    if _corroborated_style(sl):
                        report.style_spans.setdefault(i, []).append((a, b))
                sc = new
        revised.append((i, sc, n))
    report.para_scores = revised


def _aggregate_paragraph_index(report):
    """Bound the heuristic document index by its word-weighted local evidence.

    The legacy logistic score pools markers across the entire document and can
    saturate near 100 even when many paragraphs carry weak evidence. For a
    mixed document, that pooled score must not overrule the local analysis.
    This is an evidence index, not a calibrated probability of authorship.
    Keep the pooled score for diagnostics; do not rescale paragraph scores to
    force a desired result. Slide decks have their own unit of analysis.
    """
    deck = getattr(report, "deck", None)
    if deck is not None and deck.is_deck:
        return False
    ps = list(getattr(report, "para_scores", None) or [])
    total = sum(n for _, _, n in ps if n > 0)
    if total <= 0:
        return False
    mean = sum(n * sc for _, sc, n in ps if n > 0) / total
    report.score_pooled = float(report.score)
    report.score_local = mean
    report.score = min(report.score_pooled, mean)
    return report.score < report.score_pooled - 1e-9


def _enhance(report):
    """Layers that run AFTER the unchanged engine (the engine file itself is never touched):
      1. container recalibration (file-handling traces may not veto the prose evidence),
      2. technical-paper layer (tech_signals), 3. academic-prose layer (academic_signals).
    2 and 3 can only RAISE the score, and only when >= 3 independent habits converge. Score, verdict and paragraph
    scores (which drive the PDF shading) are updated together so every report shows the same percentage."""
    eng = get_engine()
    report.score_engine = float(report.score)
    report.score_stages = []
    _score_stage(report, "engine", "المحرك الأصلي")
    report.wording_map = list(getattr(report, "para_scores", None) or [])
    report.tech = None
    report.academic = None
    lines = []
    paras = getattr(report, "doc_paras", None)
    if paras is None:
        paras = [eng.Para(text=t) for t in report.texts]
        eng.assign_roles(paras, report.is_docx)
    _complete_style_findings(report, paras)

    if getattr(report, "is_docx", False):
        _stabilize_meta(report)
        note = getattr(report, "meta_gen_note", None)
        if note:
            lines.append("■ بصمة المولّد الصريحة في بيانات الملف (أداة برمجية لا مستخدم Word) احتُسبت بنسبة %.0f%%: نصفها لأنها شهادة مباشرة بأن الملف لم يُكتب في Word، "
                         "والباقي بقدر اتفاق طبقة النثر الأكاديمي المستقلة معها (%d بصمات أسلوبية، اتفاق %.0f%%)." % (100 * note[0], note[1], 100 * note[3]))
            _generator_to_paragraphs(report, note[2])
    _score_stage(report, "metadata", "ضبط أثر بيانات الملف")
    _spacing_insensitive_style(report, paras)
    _score_stage(report, "matching_view", "مطابقة العبارات مع حفظ النص الأصلي")
    _notation_neutral(report)
    _text_error_review(report, paras)
    _score_stage(report, "text_errors", "قرائن الأخطاء النصية الموضعية")
    _lists_neutral(report)
    if getattr(report, "is_docx", False) and _photo_evidence(report) < 0:
        n_, d_ = report.photo_note
        lines.append("▼ %.1f  %d صورة فوتوغرافية (JPEG) موضوعة في المتن: مادة ميدانية/مخبرية مباشرة تدل على تدخل إنسان في إنتاج الملف. "
                     "وهي لا تثبت من كتب الجمل، لذلك أثرها صغير ومحدود، وغيابها لا يدل على شيء." % (d_, n_))
    pdf_gated = False
    if getattr(report, "from_pdf", False):
        _pdf_neutralize(report)
        pdf_gated = _pdf_reference_gate(report)
        if pdf_gated:
            lines.append("■ PDF: قائمة المراجع مقطّعة في الاستخراج (مداخل ناقصة)، فعُطِّلت قرينة «مراجع بلا إحالات» ولم تُحتسب.")
    _structural_neutral(report)
    _topic_vocab_neutral(report, paras)
    _refresh_paragraph_evidence(report)
    _score_stage(report, "neutralization", "استبعاد قرائن القياسات والقوائم والبنية")
    deck = getattr(report, "deck", None)
    deck_mode = deck is not None and deck.is_deck
    if deck_mode:
        before = float(report.score)
        delta = _deck_layout_fix(report)
        lines.append("■ عرض تقديمي (%s): يُحلَّل شريحةً شريحة، ولا يُحتسب غياب النقطة في آخر العناوين والنقاط خطأً بشريًا%s."
                     % (deck.reason, (" (المؤشر %.0f ← %.0f)" % (before, report.score)) if abs(delta) > 1e-6 else ""))

    acad, acad_layer, topic = _academic_for(report)
    report.academic = acad_layer

    # The container veto is relaxed ONLY when the prose itself independently converges on LLM habits.
    # Without that, a human-edited Word file keeps the full v2.1 human evidence (no new false positives).
    prose_fired = acad_layer is not None and acad_layer.confidence > 0
    if deck_mode:
        import slide_mode
        before = float(report.score)
        fused = slide_mode.fuse(before, deck)
        if fused > before + 0.5:
            report.score = float(fused)
            lines.append("▲ طبقة بنية الشرائح رفعت المؤشر من %.0f إلى %.0f لاجتماع %d بصمات قالبية:" % (before, report.score, deck.fired))
        elif deck.score is not None:
            lines.append("■ بنية الشرائح: %d بصمات قالبية من 3 مطلوبة؛ لا رفع للمؤشر." % deck.fired)
        for x in deck.signals:
            if x.weight * x.z >= 0.05:
                lines.append("   +%.1f  %s (%s)" % (x.weight * x.z, x.label,
                             ("CV=%.2f" % x.value) if x.key == "desc_cv" else "%.0f%% من الشرائح" % (100 * x.value)))
        prose_fired = prose_fired or deck.confidence > 0
    pa = getattr(report, "paste", None)
    paste_eff, paste_share, paste_lines, paste_raw = _paste_effective(report, pa) if pa is not None else (0.0, 0.0, [], 0.0)
    paste_fired = pa is not None and paste_eff >= PASTE_MIN
    if paste_fired:
        before = float(report.score)
        report.z = float(report.z) + min(PASTE_MAX_LOGIT, PASTE_LOGIT * paste_eff)
        conf = float(getattr(report, "confidence", 1.0) or 0.0)
        report.score = 100.0 / (1.0 + math.exp(-report.z * conf)) if conf else report.score
        lines.append("▲ آثار نسخ/لصق في تنسيق ملف Word (طبقة مستقلة) رفعت المؤشر من %.0f إلى %.0f:" % (before, report.score))
        lines += paste_lines
    elif pa is not None and paste_raw >= PASTE_MIN:
        lines.append("■ في الملف آثار تنسيق نُسخ من ويب (%d أثرًا)، لكن %.0f%% فقط من النص تتفق صياغته معها، فلم تُحتسب. "
                     "التنسيق يبقى في الملف بعد إعادة كتابة الكلمات، فلا يُعتدّ به وحده دليلًا على النص." % (len(getattr(pa, "parts", {}) or pa.evidence), 100 * paste_share))
    _score_stage(report, "layout", "بنية الشرائح وآثار اللصق")
    strength = 1.0 if (deck_mode and deck.confidence > 0) else max(float(getattr(acad_layer, "strength", 0.0) or 0.0), paste_share if paste_fired else 0.0)
    if (prose_fired or paste_fired) and _recalibrate_container(report, strength):
        lines.append("◆ معايرة آثار الملف: آثار التحرير/التنسيق/الاقتباس في ملف Word تدل على أن إنسانًا تعامل مع الملف، "
                     "لا على أنه كتب النص؛ فخُفِّف أثرها المجتمع من %.1f إلى %.1f بنسبة تقارب القرائن الأسلوبية المستقلة %.0f%% (المؤشر %.0f ← %.0f)."
                     % (report.container_raw, report.container_eff, 100 * strength, getattr(report, "score_neutral", report.score_engine), report.score))
    _score_stage(report, "container", "معايرة آثار التحرير والتنسيق")

    try:
        import tech_signals as ts
        # Section headings ("Two Axes of Equivalence: Linguistic and Legal") are structure, not prose: a "Title: Subtitle"
        # heading is not the chat-style "Term: explanation" list item that the labelled-items habit looks for.
        texts = _tech_input_texts(report, ts)
        layer = ts.analyze(texts)
        report.tech = layer
        before = float(report.score)
        fused = ts.fuse(before, layer)
        if layer.confidence > 0:
            _lift_paras(report, ts, layer.confidence)
        if fused > before + 0.5:
            report.score = float(fused)
            lines.append("▲ طبقة الأوراق التقنية رفعت المؤشر من %.0f إلى %.0f لاجتماع بصمات أسلوبية:" % (before, report.score))
            for x in layer.signals:
                if x.z >= 0.35 and x.key != "friction":
                    lines.append("   +%.1f  %s (%.3g %s)" % (x.weight * x.z, x.label, x.value, x.unit))
    except Exception:
        pass

    _score_stage(report, "technical", "طبقة النثر التقني")
    try:
        layer = acad_layer
        if acad is None or layer is None:
            raise RuntimeError
        before = float(report.score)
        fused = acad.fuse(before, layer)
        if layer.confidence > 0:
            _lift_paras(report, acad, layer.confidence, topic=topic)
        if fused > before + 0.5:
            report.score = float(fused)
            lines.append("▲ طبقة النثر الأكاديمي رفعت المؤشر من %.0f إلى %.0f لاجتماع %d بصمات أسلوبية:" % (before, report.score, layer.fired))
            for x in layer.signals:
                if x.weight * x.z >= 0.05:
                    lines.append("   +%.1f  %s (%.3g %s)" % (x.weight * x.z, x.label, x.value, x.unit))
    except Exception:
        pass

    _score_stage(report, "academic", "طبقة النثر الأكاديمي")
    _local_style_review(report, paras)
    _score_stage(report, "local_style", "مراجعة الأنماط العربية والإنجليزية المحلية")
    # v2.5: paragraph-level reading of the document (segment_signals). It is computed here, from the evidence gathered so
    # far; the v2.3 steps below still run (their diagnostics stay on the report), and the segmentation then sets the index.
    report.segmentation = None
    seg = None
    seg_before = float(report.score)
    if not deck_mode:
        try:
            seg = _segment(report, topic, pa)
        except Exception:
            seg = None
    use_seg = (seg is not None and seg.units and seg.words >= SEG_MIN_WORDS and
               sum(u.words for u in seg.units if not u.arabic) >= SEG_MIN_ENGLISH * seg.words)

    # Paragraph map = wording evidence only. The evidence about the FILE (human editing history, hand formatting,
    # typing slips) applies to every paragraph of that file exactly as it applies to the document score, so it is
    # added to each paragraph's log-odds (human evidence only, never raising). Without this the map and the
    # document score describe different things and a human-edited thesis shows "76% suspected" beside a low index.
    shifted = False
    if not deck_mode:
        shifted = _file_evidence_to_paragraphs(report, paras)
    _score_stage(report, "paragraph_file_evidence", "أثر قرائن Word على خريطة الفقرات")

    # "A human always slips": a long text with (almost) no human typing / formatting slip was not typed by a person.
    report.friction = None
    # Word files only: pasted text and PDF extraction collapse double spaces and lose formatting, so a human text
    # would look 'clean' there. Applying the rule outside DOCX would wrong human writers.
    if not deck_mode and getattr(report, "is_docx", False) and getattr(report, "doc_paras", None) is not None:
        try:
            import friction
            pa_ = getattr(report, "paste", None)
            fr = friction.measure(report, getattr(report, "doc_paras", None), getattr(report, "format_forensics", None),
                                  uniform_indent=bool(pa_ is not None and pa_.space_indent_uniform > 0),
                                  prose_score=max(prose_only_score(report),
                                                  acad_layer.score if (acad_layer is not None and acad_layer.confidence > 0
                                                                       and acad_layer.score is not None) else 0.0))
            report.friction = fr
            before = float(report.score)
            if fr.floor is not None and fr.floor > before:
                report.score = fr.floor
        except Exception:
            pass

    _score_stage(report, "friction", "قاعدة قلة الهفوات")
    _aggregate_paragraph_index(report)
    _score_stage(report, "paragraph_aggregation", "تجميع قرائن الفقرات موزونة بالكلمات")
    if not deck_mode and _anchor_map_to_index(report):
        shifted = True

    if use_seg:
        report.score = seg_before
        _apply_segmentation(report, seg, lines)
        _score_stage(report, "segmentation", "قراءة المستند فقرةً فقرة")
        if getattr(report, "from_pdf", False):
            lines.insert(0, "■ PDF: ملف PDF يحمل النص النهائي وحده - بلا تاريخ تحرير ولا تنسيق يدوي ولا هفوات كتابة - فالتقدير من الصياغة وحدها (النص وحده).")
        report.verdict, report.color = (_pdf_verdict(report, eng) if getattr(report, "from_pdf", False)
                                        else eng.make_verdict(report.score, report.words))
        report.reasons = [r for r in report.reasons if not str(r).startswith("⚠ مستند مختلط محتمل")] + lines
        ff = getattr(report, "format_forensics", None)
        if ff is not None:
            report.reasons = list(report.reasons) + ff.lines()
        return report

    if shifted:      # the engine's own "mixed document" line was computed before the file evidence reached the map
        report.reasons = [r for r in report.reasons if not str(r).startswith("⚠ مستند مختلط محتمل")]
        _tot = sum(n for _, _, n in report.para_scores)
        _hi = sum(n for _, sc, n in report.para_scores if sc >= 55)
        if _tot >= 150 and _hi / _tot >= 0.30 and report.score < 65:
            lines.insert(0, "⚠ مستند مختلط محتمل: %.0f%% من كلمات المتن في فقرات درجتها ≥ 55 (راجع تبويب «خريطة الفقرات»)" % (100 * _hi / _tot))
    pdf_text_only = getattr(report, "from_pdf", False) and not deck_mode
    if pdf_text_only:
        lines.insert(0, "■ PDF: ملف PDF يحمل النص النهائي وحده - بلا تاريخ تحرير ولا تنسيق يدوي ولا هفوات كتابة - فالتقدير من الصياغة وحدها (النص وحده).")
    if abs(report.score - report.score_engine) > 0.5 or deck_mode or shifted or pdf_text_only or getattr(report, "friction", None) is not None:
        report.verdict, report.color = (_pdf_verdict(report, eng) if pdf_text_only else eng.make_verdict(report.score, report.words))
        tot = sum(n for _, _, n in report.para_scores)
        hi = sum(n for _, sc, n in report.para_scores if sc >= 55)
        if deck_mode and deck.suspect_share is not None:
            if deck.suspect_share >= 0.30 and report.score < 65:
                report.verdict += " — مع شرائح مشتبه بها (%.0f%% من نص العرض)" % (100 * deck.suspect_share)
        elif tot >= 150 and tot and hi / tot >= 0.30 and report.score < 65:
            report.verdict += " — مع فقرات مشتبه بها (%.0f%% من النص)" % (100 * hi / tot)
        report.reasons = list(report.reasons) + lines
    ff = getattr(report, "format_forensics", None)
    if ff is not None:
        report.reasons = list(report.reasons) + ff.lines()
    return report


# ------------------------------------------------------------------------------------------------------------------
# v2.5  paragraph-level segmentation (segment_signals)
# ------------------------------------------------------------------------------------------------------------------
SEG_MIN_WORDS = 150
SEG_META_GEN = 0.6          # programmatic generator in the file metadata -> applies to every paragraph of the file
SEG_FRICTION_MAX = 1.2      # log-odds added to suspected paragraphs when the suspected text is slip-free
SEG_FRICTION_MIN_WORDS = 800
# When the whole-document reading of the earlier stages is itself far above anything measured on human files
# (bench maximum 66), every paragraph's prior is raised in proportion (0 at SEG_DOC_GATE, SEG_DOC_LOGIT at 100).
# Paragraphs that are clearly human stay human; only the undecided ones follow the document.
SEG_DOC_GATE, SEG_DOC_LOGIT = 80.0, 0.0   # v2.6: off -- a whole-document push made the index stick at 100 and ignore edits
SEG_MIN_ENGLISH = 0.6       # the paragraph habits are English habits: Arabic documents keep the v2.3 document reading


def _roles_for(report):
    paras = getattr(report, "doc_paras", None)
    texts = list(report.texts)
    if paras is not None and len(paras) == len(texts):
        return [getattr(p, "role", "body") for p in paras], paras
    eng = get_engine()
    ps = [eng.Para(text=t) for t in texts]
    eng.assign_roles(ps, False)
    return [p.role for p in ps], None


def _segment(report, topic, pa):
    import segment_signals as sg
    roles, paras = _roles_for(report)
    docx = bool(getattr(report, "is_docx", False)) and not getattr(report, "from_pdf", False)
    pasted = set()
    if docx and paras is not None:
        try:
            import friction
            runs = friction._paste_runs(paras)
            pasted = set().union(*runs.values()) if runs else set()
            ff = getattr(report, "format_forensics", None)
            pasted |= friction._block_index_set(ff)
        except Exception:
            pass
    feats = getattr(report, "features", None) or {}
    g = 0.0
    if "meta_gen" in feats and feats["meta_gen"][2] > 0:
        g += SEG_META_GEN * feats["meta_gen"][2]
    traces = dict(getattr(pa, "trace_texts", {}) or {}) if pa is not None else {}
    if pa is not None and pa.total >= PASTE_MIN and not traces:
        g += 0.3 * min(PASTE_MAX_LOGIT, PASTE_LOGIT * pa.total)     # file-wide paste trace (e.g. typed-space indents)
    seg = sg.segment(report.texts, roles, report.findings, report.para_scores, topic or frozenset(), docx=docx,
                     paste_texts=traces, pasted_idx=frozenset(pasted), global_logit=g)
    # "A human always slips" (Word files): a long suspected text without a single slip confirms the suspicion.
    if docx and seg.units:
        import friction
        sus = [u for u in seg.units if u.post >= sg.SUSPECT]
        sw = sum(u.words for u in sus)
        if sw >= SEG_FRICTION_MIN_WORDS:
            rate = sum(u.slips for u in sus) / (sw / 1000.0)
            f = friction.floor_for(rate)
            if f > 50.0:
                sg.strengthen_clean_suspects(seg, SEG_FRICTION_MAX * (f - 50.0) / 40.0)
    return seg


def _apply_segmentation(report, seg, lines):
    """The index = the share of prose words in the paragraphs the engines attribute to generation (probability >= 0.5).
    The paragraph map and the PDF shading are built from exactly the same paragraphs, so they always equal the index."""
    import segment_signals as sg
    eng = get_engine()
    before = float(report.score)
    report.segmentation = seg
    report.score_document = before          # whole-document reading of the earlier stages (kept for diagnosis)
    if before > SEG_DOC_GATE:
        sg.raise_prior(seg, SEG_DOC_LOGIT * min(1.0, (before - SEG_DOC_GATE) / (100.0 - SEG_DOC_GATE)))
    sg.own_evidence_veto(seg)
    words_of = {i: len(eng.WORD_RE.findall(report.texts[i] or "")) for u in seg.units for i in u.idx}
    tot = sum(words_of.values()) or 1
    hit = sum(words_of[i] for u in seg.units if u.post >= sg.SUSPECT for i in u.idx)
    report.ai_share = hit / tot
    report.ai_share_expected = seg.share
    report.score = round(100.0 * hit / tot, 1)
    report.para_scores = [(i, round(100.0 * seg.para_post[i], 0), words_of[i]) for i in sorted(seg.para_post)]
    report.reasons = [r for r in report.reasons if not str(r).startswith("⚠ مستند مختلط محتمل")]
    if getattr(seg, "spun", 0):
        lines.append("▲ %d عبارة «معذَّبة» تبدّل فيها مصطلح علمي ثابت بمرادفات حرفية (مثل «counterfeit consciousness» بدل artificial intelligence). "
                     "هذه بصمة أدوات إعادة الصياغة الآلية. (معلومة؛ لا تغيّر النسبة)" % seg.spun)
    if getattr(seg, "evasion", 0):
        lines.append("▲ %d مسافة من نوع غير عادي (مثل U+2002) مزروعة بين كلمات إنجليزية. لوحة المفاتيح لا تكتب هذه المسافة؛ "
                     "تزرعها أدوات «أنسنة» النص للتحايل على أجهزة الكشف. نُظّفت إلى مسافات عادية، واحتُسبت قرينةً متوسطة في فقراتها." % seg.evasion)
    if seg.blocks:
        spans = "، ".join(("الفقرة %d" % (a + 1)) if a == b else ("الفقرات %d–%d" % (a + 1, b + 1)) for a, b, _ in seg.blocks[:8])
        lines.append("◆ قراءة المستند فقرةً فقرة: %.0f%% من كلمات المتن في فقرات تحمل عادات النص المولَّد، في %d كتلة (%s)."
                     % (report.score, len(seg.blocks), spans))
    else:
        lines.append("◆ قراءة المستند فقرةً فقرة: لا توجد فقرة تحمل عادات النص المولَّد.")

def analyze_text_guarded(text: str, source: str = "نص ملصق", deck=None, from_pdf: bool = False):
    """Text route (paste / TXT / MD / PDF text): equations out, then the unchanged engine."""
    import math_guard
    clean, mm = math_guard.sanitize_text(text)
    rep = get_engine().analyze_text(text if not mm.active else clean, source=source)
    rep.from_pdf = bool(from_pdf)
    return _attach(rep, mm, deck=deck)


def _read_deck(content: bytes):
    """Slide-deck recognition for PDFs (slide_mode). Any failure simply means 'not a deck'."""
    try:
        import slide_mode
        meta, slides = slide_mode.read_pdf(content)
        return slide_mode.analyze(meta, slides)
    except Exception:
        return None


def prepare_docx(path: str, content: bytes):
    """ONE place that turns a .docx into the paragraphs the engine analyses. Both the analysis and the report builder
    (nebras_report._paras_for) call it, so the report can always rebuild exactly what was analysed.
    Returns (clean_paras, meta, math_map, clean_stats, generator_note)."""
    import math_guard
    import docx_clean
    eng = get_engine()
    paras, meta = eng.parse_docx(path)
    flags = math_guard.omml_layout(content, [p.text for p in paras])   # equation counts AND their exact offsets
    if flags is None:
        flags = math_guard.omml_flags(content, len(paras))
    clean, mm = math_guard.sanitize_docx_paras(paras, flags)
    with open(path, "rb") as fh:
        theme = docx_clean.theme_font_table(fh.read())
    clean, st = docx_clean.clean_docx_paras(clean, mm, theme_fonts=theme)
    # Word-numbered bibliography: give the engine the numbers Word displays, so "[12]" in the text can match entry 12
    st.notes.append("refs_numbered=%d" % docx_clean.restore_reference_numbers(clean, mm, content, len(mm.orig_texts)))
    gen = docx_clean.generator_from_description(content)
    if gen:     # engine only inspects creator / lastModifiedBy; the generator is often declared in the description
        meta = dict(meta)
        meta["creator"] = "%s [%s]" % ((meta.get("creator") or "").strip(), gen)
    return clean, meta, mm, st, gen


def analyze_docx_guarded(path: str, content: bytes):
    """DOCX route: engine's own parser (keeps font/format/metadata evidence).

    Non-prose parts of a manuscript are removed first, exactly like pdf_input does for PDF: equations (math_guard), then
    code blocks, display-equation paragraphs, the title/author block and table cells (docx_clean). No score / weight /
    threshold is touched."""
    eng = get_engine()
    clean, meta, mm, st, gen = prepare_docx(path, content)
    refs_fixed = any(n.startswith("refs_numbered=") and not n.endswith("=0") for n in st.notes)
    fast = not mm.active and not st.removed and not st.trimmed and not st.fonts_unified and not gen and not refs_fixed and not st.headings_restored
    if fast:
        report = eng.analyze_docx(path)
        eng.assign_roles(clean, True)
    else:
        report = eng.core_analyze(clean, meta, os.path.basename(path))
        report.docx_clean = st
    ff, pa = None, None
    try:
        import format_forensics
        ff = format_forensics.analyze(clean)
        pa = format_forensics.paste_artifacts(content, ff)
    except Exception:
        pass
    report.photos_n = photo_count(content)
    return _attach(report, mm, ff, pa, paras=clean)


_MIME_EXT = {"application/pdf": ".pdf", "application/x-pdf": ".pdf",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
             "text/plain": ".txt", "text/markdown": ".md", "text/x-markdown": ".md", "application/zip": ".zip",
             "application/x-zip-compressed": ".zip", "application/x-zip": ".zip",
             "application/msword": ".doc"}


def sniff_extension(data: bytes) -> str:
    """Extension implied by the file's own bytes ('' when unsure). Android pickers (Drive, WhatsApp, content:// URIs) often
    hand over a file with no extension, a doubled one ('report.docx.bin', 'report.docx (1)') or MIME application/octet-stream."""
    head = bytes(data[:2048]) if data else b""
    if not head:
        return ""
    if b"%PDF-" in head[:1024]:
        return ".pdf"
    if head[:4] == b"PK\x03\x04":
        try:
            names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except Exception:
            return ""
        return ".docx" if "word/document.xml" in names else ".zip"
    if head[:8] == bytes.fromhex("D0CF11E0A1B11AE1"):
        return ".doc"
    for enc in ("utf-8-sig", "utf-16"):
        try:
            sample = head.decode(enc)
        except Exception:
            continue
        if sample and sum(c.isprintable() or c in "\r\n\t" for c in sample) / len(sample) > 0.95:
            return ".txt"
    return ""


def normalize_upload_name(name: str, mime: str = "", data: bytes | None = None) -> str:
    """Give an uploaded file a reliable name. The file's own bytes decide when they are conclusive (a PDF / DOCX / ZIP is
    recognised whatever the phone called it); otherwise the extension is kept (lower-cased) or restored from the MIME type."""
    try:
        name = filename_only(name or "upload")
    except ValueError:
        name = "upload"
    suffix = Path(name).suffix
    sniffed = sniff_extension(data) if data else ""
    if sniffed in (".pdf", ".docx", ".zip", ".doc"):
        stem = name[: len(name) - len(suffix)] if suffix.lower() in SUPPORTED | {".zip", ".doc"} else name
        return stem + sniffed
    if suffix.lower() in SUPPORTED | {".zip"}:
        return name[: len(name) - len(suffix)] + suffix.lower()
    ext = _MIME_EXT.get((mime or "").split(";")[0].strip().lower()) or sniffed
    return name + ext if ext else name


def analyze_pasted(text: str):
    """Analyze prose after math guarding and document/paragraph evidence aggregation."""
    return analyze_text_guarded(text)


def analyze_uploaded_bytes(filename: str, content: bytes):
    """Use the original engine for text and DOCX; import PDF through local text/OCR extraction."""
    name = filename_only(filename)
    if Path(name).suffix.lower() not in SUPPORTED:
        raise ValueError("الامتدادات المدعومة هي PDF / DOCX / TXT / MD / TEXT. ملف DOC القديم غير مدعوم.")
    if not content:
        raise ValueError("الملف فارغ.")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("حجم الملف يتجاوز حد الرفع في الواجهة (25 MB).")
    if Path(name).suffix.lower() == ".pdf":
        from pdf_input import extract_pdf_text
        return analyze_text_guarded(extract_pdf_text(content), source=name, deck=_read_deck(content), from_pdf=True)
    with tempfile.TemporaryDirectory(prefix="ai_inspector_") as tmp:
        path = Path(tmp) / name
        path.write_bytes(content)
        if path.suffix.lower() == ".docx":
            return analyze_docx_guarded(str(path), content)
        if path.suffix.lower() in (".txt", ".md", ".text"):
            return analyze_text_guarded(get_engine()._read_txt(str(path)), source=name)
        report = get_engine().analyze_file(str(path))
        if report is None:
            raise ValueError("تعذّر تحليل هذا النوع من الملفات.")
        return report


def analyze_batch(uploaded: Iterable[tuple[str, bytes]]):
    """Like original GUI's batch: keep successful reports + per-file errors."""
    results = []
    total = 0
    for n, (filename, content) in enumerate(uploaded):
        if n >= MAX_BATCH_FILES:
            raise ValueError("الحد الأقصى للدفعة 250 ملفًا.")
        total += len(content)
        if total > MAX_BATCH_BYTES:
            raise ValueError("إجمالي حجم الدفعة يتجاوز 120 MB.")
        try:
            result = analyze_uploaded_bytes(filename, content)
        except Exception as exc:
            result = exc
        results.append((filename, result))
    return results


def expand_uploaded_inputs(files: Iterable[tuple[str, bytes]]):
    """Accept individual documents or ZIP folder uploads without extracting to disk."""
    total = 0
    number = 0
    for original_name, data in files:
        if Path(original_name).suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for entry in archive.infolist():
                        if entry.is_dir() or entry.filename.startswith("__MACOSX/"):
                            continue
                        base = filename_only(entry.filename)
                        if base.startswith("~$") or Path(base).suffix.lower() not in SUPPORTED:
                            continue
                        number += 1
                        total += entry.file_size
                        if number > MAX_BATCH_FILES or total > MAX_BATCH_BYTES or entry.file_size > MAX_FILE_BYTES:
                            raise ValueError("تجاوزت الملفات حدود الحماية: 250 ملفًا، 25 MB لكل ملف، 120 MB إجماليًا.")
                        label = f"{filename_only(original_name)} / {entry.filename}"
                        yield label, archive.read(entry)
            except zipfile.BadZipFile as exc:
                raise ValueError(f"الملف ZIP غير صالح: {original_name}") from exc
        else:
            number += 1
            total += len(data)
            if number > MAX_BATCH_FILES or total > MAX_BATCH_BYTES:
                raise ValueError("تجاوزت الملفات حدود الحماية: 250 ملفًا، 120 MB إجماليًا.")
            if Path(original_name).suffix.lower() in SUPPORTED:
                yield filename_only(original_name), data


def analyze_batch_inputs(files: Iterable[tuple[str, bytes]]):
    """ZIP labels are for display; the original analyzer receives the real basename."""
    results = []
    for label, data in expand_uploaded_inputs(files):
        try:
            result = analyze_uploaded_bytes(filename_only(label), data)
        except Exception as exc:
            result = exc
        results.append((label, result))
    return results


def batch_csv_bytes(results) -> bytes:
    """Original Tkinter GUI CSV columns, ordering, and values (UTF-8 BOM)."""
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["file", "words", "ai_score", "verdict", "errors_per_1000", "format_inconsistency", "human_edit_traces",
                     "math_items_excluded"])
    for name, report in results:
        if isinstance(report, Exception):
            row = (name, "-", "-", "خطأ: %s" % report, "-", "-", "-", "-")
        else:
            row = (name, report.words, "%.0f" % report.score, report.verdict,
                   report.stats["أخطاء مرجّحة لكل 1000 كلمة"],
                   "%.2f" % report.fvals["fmt_incons"], "%.2f" % report.fvals["meta_human"],
                   getattr(getattr(report, "math", None), "stats", None).found
                   if getattr(report, "math", None) else 0)
        writer.writerow(row)
    return out.getvalue().encode("utf-8-sig")


def html_report_bytes(report) -> bytes:
    return get_engine().report_to_html(report).encode("utf-8")


def engine_status():
    eng = get_engine()
    weights = eng.load_weights()
    digest = hashlib.sha256(
        str(sorted(weights.items())).encode("utf-8")
    ).hexdigest()
    return {"version": eng.VERSION, "sha256": ENGINE_SHA256, "weights_fingerprint": digest,
            "custom_weights": os.path.exists(eng.WEIGHTS_FILE)}
