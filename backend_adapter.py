# -*- coding: utf-8 -*-
"""Transport adapter. Never calculates or changes an AI Text Inspector score.

Every analysis is delegated to the unmodified AI_Text_Inspector.pyw v2.1.
The SHA-256 guard catches accidental edits to the analysis engine.

The ONE thing done before the engine sees a text is math_guard: equations (LaTeX, Word OMML,
formula-only paragraphs) are removed, because the prose-only engine reads them as human typos
and fake sentence-length variance and under-reports AI text (measured 44 -> 5 on one manuscript).
The removal is recorded on `report.math` (a math_guard.MathMap) and shown to the user.
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
PROSE_ONLY_EXCLUDE = {"meta_human", "meta_gen", "fmt_incons", "fmt_perfect", "mixed_conv", "err_density", "clean", "ortho"}


def _stabilize_meta(report):
    feats = dict(getattr(report, "features", None) or {})
    delta = 0.0
    for k, lo, hi in (("meta_human", META_HUMAN_MIN, 0.0), ("meta_gen", 0.0, META_GEN_MAX)):
        if k in feats:
            v, w, c = feats[k]
            nc = max(lo, min(hi, c))
            if nc != c:
                delta += nc - c
                feats[k] = (v, w, nc)
    if delta:
        report.features = feats
        report.z = float(report.z) + delta
        _rescore(report)
    return delta


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


def _file_evidence_to_paragraphs(report) -> bool:
    feats = getattr(report, "features", None) or {}
    cont = getattr(report, "container_eff", None)
    if cont is None:
        cont = sum(feats[k][2] for k in CONTAINER_KEYS if k in feats and feats[k][2] < 0)
    other = sum(c for k, (v, w, c) in feats.items() if k in PROSE_ONLY_EXCLUDE and k not in CONTAINER_KEYS and c < 0)
    shift = (min(0.0, cont) + other) * float(getattr(report, "confidence", 1.0) or 0.0)
    if shift > -0.05 or not report.para_scores:
        return False
    out = []
    for i, sc, n in report.para_scores:
        p = min(0.995, max(0.005, sc / 100.0))
        out.append((i, round(100.0 / (1.0 + math.exp(-(math.log(p / (1 - p)) + shift))), 0), n))
    report.para_scores = out
    report.file_shift = shift
    return True


def _enhance(report):
    """Layers that run AFTER the unchanged engine (the engine file itself is never touched):
      1. container recalibration (file-handling traces may not veto the prose evidence),
      2. technical-paper layer (tech_signals), 3. academic-prose layer (academic_signals).
    2 and 3 can only RAISE the score, and only when >= 3 independent habits converge. Score, verdict and paragraph
    scores (which drive the PDF shading) are updated together so every report shows the same percentage."""
    eng = get_engine()
    report.score_engine = float(report.score)
    report.tech = None
    report.academic = None
    lines = []

    if getattr(report, "is_docx", False):
        _stabilize_meta(report)
    if getattr(report, "from_pdf", False):
        _pdf_neutralize(report)
    deck = getattr(report, "deck", None)
    deck_mode = deck is not None and deck.is_deck
    if deck_mode:
        before = float(report.score)
        delta = _deck_layout_fix(report)
        lines.append("■ عرض تقديمي (%s): يُحلَّل شريحةً شريحة، ولا يُحتسب غياب النقطة في آخر العناوين والنقاط خطأً بشريًا%s."
                     % (deck.reason, (" (المؤشر %.0f ← %.0f)" % (before, report.score)) if abs(delta) > 1e-6 else ""))

    try:
        import academic_signals as acad
        topic = acad.topic_terms([getattr(q, "text", "") for q in (getattr(report, "doc_paras", None) or [])
                                  if getattr(q, "role", "") == "heading"])
        acad_layer = acad.analyze([t for t in report.texts if t and t.strip()], topic)
    except Exception:
        acad, acad_layer, topic = None, None, None
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
    paste_fired = pa is not None and pa.total >= PASTE_MIN
    if paste_fired:
        before = float(report.score)
        report.z = float(report.z) + min(PASTE_MAX_LOGIT, PASTE_LOGIT * pa.total)
        conf = float(getattr(report, "confidence", 1.0) or 0.0)
        report.score = 100.0 / (1.0 + math.exp(-report.z * conf)) if conf else report.score
        lines.append("▲ آثار نسخ/لصق في تنسيق ملف Word (طبقة مستقلة) رفعت المؤشر من %.0f إلى %.0f:" % (before, report.score))
        lines += list(pa.evidence)
    strength = 1.0 if (paste_fired or (deck_mode and deck.confidence > 0)) else float(getattr(acad_layer, "strength", 0.0) or 0.0)
    if (prose_fired or paste_fired) and _recalibrate_container(report, strength):
        lines.append("◆ معايرة آثار الملف: آثار التحرير/التنسيق/الاقتباس في ملف Word تدل على أن إنسانًا تعامل مع الملف، "
                     "لا على أنه كتب النص؛ فخُفِّف أثرها المجتمع من %.1f إلى %.1f بنسبة تقارب القرائن الأسلوبية المستقلة %.0f%% (المؤشر %.0f ← %.0f)."
                     % (report.container_raw, report.container_eff, 100 * strength, report.score_engine, report.score))

    try:
        import tech_signals as ts
        # Section headings ("Two Axes of Equivalence: Linguistic and Legal") are structure, not prose: a "Title: Subtitle"
        # heading is not the chat-style "Term: explanation" list item that the labelled-items habit looks for.
        _paras = getattr(report, "doc_paras", None) or []
        _head = {i for i, q in enumerate(_paras) if getattr(q, "role", "") == "heading"} if len(_paras) == len(report.texts) else set()
        texts = [t for i, t in enumerate(report.texts) if t and t.strip() and i not in _head and not ts._REFLINE.match(t)]
        layer = ts.analyze(texts)
        report.tech = layer
        before = float(report.score)
        fused = ts.fuse(before, layer)
        if fused > before + 0.5:
            report.score = float(fused)
            _lift_paras(report, ts, layer.confidence)
            lines.append("▲ طبقة الأوراق التقنية رفعت المؤشر من %.0f إلى %.0f لاجتماع بصمات أسلوبية:" % (before, report.score))
            for x in layer.signals:
                if x.z >= 0.35 and x.key != "friction":
                    lines.append("   +%.1f  %s (%.3g %s)" % (x.weight * x.z, x.label, x.value, x.unit))
    except Exception:
        pass

    try:
        layer = acad_layer
        if acad is None or layer is None:
            raise RuntimeError
        before = float(report.score)
        fused = acad.fuse(before, layer)
        if fused > before + 0.5:
            report.score = float(fused)
            _lift_paras(report, acad, layer.confidence, topic=topic)
            lines.append("▲ طبقة النثر الأكاديمي رفعت المؤشر من %.0f إلى %.0f لاجتماع %d بصمات أسلوبية:" % (before, report.score, layer.fired))
            for x in layer.signals:
                if x.weight * x.z >= 0.05:
                    lines.append("   +%.1f  %s (%.3g %s)" % (x.weight * x.z, x.label, x.value, x.unit))
    except Exception:
        pass

    # v2.4: paragraph-level reading of the document (segment_signals). The document index becomes the estimated share
    # of generated text, so the index, the paragraph map and the PDF shading describe the same thing.
    report.segmentation = None
    report.friction = None
    shifted = False
    seg = None
    if not deck_mode:
        try:
            seg = _segment(report, topic, pa)
        except Exception:
            seg = None
    if seg is not None and seg.units and seg.words >= SEG_MIN_WORDS and \
            sum(u.words for u in seg.units if not u.arabic) >= SEG_MIN_ENGLISH * seg.words:
        _apply_segmentation(report, seg, lines)
    else:
        # Paragraph map = wording evidence only. The evidence about the FILE (human editing history, hand formatting,
        # typing slips) applies to every paragraph of that file exactly as it applies to the document score.
        if not deck_mode:
            shifted = _file_evidence_to_paragraphs(report)

        # "A human always slips": a long text with (almost) no human typing / formatting slip was not typed by a person.
        report.friction = None
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
                # score only: by design this rule is not written into the interface or the reports
            except Exception:
                pass

        if shifted:      # the engine's own "mixed document" line was computed before the file evidence reached the map
            report.reasons = [r for r in report.reasons if not str(r).startswith("⚠ مستند مختلط محتمل")]
            _tot = sum(n for _, _, n in report.para_scores)
            _hi = sum(n for _, sc, n in report.para_scores if sc >= 55)
            if _tot >= 150 and _hi / _tot >= 0.30 and report.score < 65:
                lines.insert(0, "⚠ مستند مختلط محتمل: %.0f%% من كلمات المتن في فقرات درجتها ≥ 55 (راجع تبويب «خريطة الفقرات»)" % (100 * _hi / _tot))
    if seg is not None and getattr(report, "segmentation", None) is not None:
        report.verdict, report.color = eng.make_verdict(report.score, report.words)
        report.reasons = list(report.reasons) + lines
        ff = getattr(report, "format_forensics", None)
        if ff is not None:
            report.reasons = list(report.reasons) + ff.lines()
        return report
    if abs(report.score - report.score_engine) > 0.5 or deck_mode or shifted or getattr(report, "friction", None) is not None:
        report.verdict, report.color = eng.make_verdict(report.score, report.words)
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
# v2.4  paragraph-level segmentation (segment_signals)
# ------------------------------------------------------------------------------------------------------------------
SEG_MIN_WORDS = 150
SEG_META_GEN = 0.6          # programmatic generator in the file metadata -> applies to every paragraph of the file
SEG_FRICTION_MAX = 1.2      # log-odds added to suspected paragraphs when the suspected text is slip-free
SEG_FRICTION_MIN_WORDS = 800
# When the whole-document reading of the earlier stages is itself far above anything measured on human files
# (bench maximum 66), every paragraph's prior is raised in proportion (0 at SEG_DOC_GATE, SEG_DOC_LOGIT at 100).
# Paragraphs that are clearly human stay human; only the undecided ones follow the document.
SEG_DOC_GATE, SEG_DOC_LOGIT = 80.0, 2.0
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
    import segment_signals as sg
    before = float(report.score)
    report.segmentation = seg
    report.score_document = before          # whole-document probability from the earlier stages (kept for diagnosis)
    if before > SEG_DOC_GATE:
        sg.raise_prior(seg, SEG_DOC_LOGIT * min(1.0, (before - SEG_DOC_GATE) / (100.0 - SEG_DOC_GATE)))
    share = seg.share
    report.ai_share = seg.share
    report.score = round(100.0 * share, 1)
    words_of = {i: len((report.texts[i] or "").split()) for u in seg.units for i in u.idx}
    report.para_scores = [(i, round(100.0 * seg.para_post[i], 0), words_of[i]) for i in sorted(seg.para_post)]
    report.reasons = [r for r in report.reasons if not str(r).startswith("⚠ مستند مختلط محتمل")]
    if seg.blocks:
        spans = "، ".join(("الفقرة %d" % (a + 1)) if a == b else ("الفقرات %d–%d" % (a + 1, b + 1)) for a, b, _ in seg.blocks[:8])
        lines.append("◆ قراءة المستند فقرةً فقرة: نحو %.0f%% من كلمات المتن تحمل عادات النص المولَّد، في %d كتلة (%s)."
                     % (100 * seg.share, len(seg.blocks), spans))
    else:
        lines.append("◆ قراءة المستند فقرةً فقرة: لا توجد كتلة نصية تحمل عادات النص المولَّد.")


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
    fast = not mm.active and not st.removed and not st.trimmed and not st.fonts_unified and not gen and not refs_fixed
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
    return _attach(report, mm, ff, pa, paras=clean)


_MIME_EXT = {"application/pdf": ".pdf",
             "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
             "text/plain": ".txt", "text/markdown": ".md", "application/zip": ".zip",
             "application/x-zip-compressed": ".zip"}


def normalize_upload_name(name: str, mime: str = "") -> str:
    """Phone browsers often hand over files without an extension (content:// URIs, WhatsApp, Drive) or with an upper-case
    one. Restore the extension from the MIME type so the file is not rejected as unsupported."""
    name = filename_only(name or "upload")
    suffix = Path(name).suffix
    if suffix.lower() in SUPPORTED | {".zip"}:
        return name[: len(name) - len(suffix)] + suffix.lower()
    ext = _MIME_EXT.get((mime or "").split(";")[0].strip().lower())
    return name + ext if ext else name


def analyze_pasted(text: str):
    """The pasted-text route. Identical to the original engine route unless the text contains maths."""
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
