"""v3: text-only global/local evidence, with file provenance kept separate.

This is a transparent heuristic index, not a calibrated authorship probability.
The frozen v2.1 engine remains available as the baseline. No user document or
claimed target percentage sets a threshold or weight here.
"""
from dataclasses import dataclass, field
import math
import re

VERSION = "3.2"
# Human p97 after correlated wording features were pooled. Measured only on
# the 52 development-calibration PEPs, not user documents or validation sets.
# See benchmarks/v3.1-wording-calibration.json and tools/calibrate_wording.py.
WORDING_THRESHOLD = 2.11
SOURCE = "balanced_v3"
# These properties describe editing/exporting a FILE, not writing its prose.
FILE_FEATURES = frozenset({"photos", "paste_trace", "meta_human", "meta_gen",
                           "fmt_incons", "fmt_perfect", "mixed_conv", "clean"})


@dataclass
class Result:
    score: float
    global_score: float
    local_score: float
    baseline_score: float
    words: int
    source: str = SOURCE
    features: dict = field(default_factory=dict)
    excluded: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    paragraphs: list = field(default_factory=list)
    global_logit: float = 0.0
    slip_discount: float = 0.0
    segmentation: object = None
    head_logit: float = 0.0
    head_lift: float = 0.0


def _sigmoid(z):
    return 100.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))


def analyze(report):
    import backend_adapter as b
    import segment_signals as sg
    from matching_text import matching_text

    roles, _ = b._roles_for(report)
    texts = list(report.texts)
    prose = "\n".join(t for t, role in zip(texts, roles) if role in ("body", "list"))
    letters = sum(c.isalpha() for c in prose)
    latin = len(re.findall(r"[A-Za-z]", prose))
    words = len(re.findall(r"[A-Za-z][A-Za-z'\-]*", prose))
    baseline = float(report.score_engine)
    # No English calibration is claimed for other scripts or slide layouts.
    deck = getattr(report, "deck", None)
    if words < 150 or latin / max(1, letters) < 0.7 or (deck is not None and deck.is_deck):
        return Result(baseline, baseline, baseline, baseline, words,
                      source="main_engine_fallback",
                      warnings=["مسار v3 الإنجليزي غير مدعوم لهذا الإدخال؛ عُرض المحرك الأساسي."])

    features = dict(report.text_evidence_features)
    active = {k: row for k, row in features.items() if k not in FILE_FEATURES}
    excluded = {k: row[2] for k, row in features.items() if k in FILE_FEATURES}
    bias = b.get_engine().load_weights()["bias"]
    z = bias + sum(c for _, _, c in active.values())
    global_score = _sigmoid(z * report.confidence)
    # Technical/academic habits can contribute only with their existing
    # independent convergence gates. File fingerprints never open these gates.
    import tech_signals
    import academic_signals
    global_score = tech_signals.fuse(global_score, getattr(report, "tech", None))
    global_score = academic_signals.fuse(global_score, getattr(report, "academic", None))

    error_kinds = getattr(report, "text_error_kinds", set())
    findings = [f for f in report.findings if f.kind in error_kinds]
    topic = b._academic_for(report)[2] or frozenset()
    # Read the wording independently of Word history, fonts, paste fingerprints,
    # cleanliness floors, and unusual Unicode spaces. Confusable letters and
    # zero-width characters are deliberately left unchanged.
    seg = sg.segment([matching_text(t) for t in texts], roles, findings,
                     getattr(report, "wording_map", ()), topic, docx=False,
                     local_wording=getattr(report, "local_technical_support", {}),
                     artifact_evidence=False, wording_threshold=WORDING_THRESHOLD)
    sg.own_evidence_veto(seg)
    sg.preserve_standalone_support(seg)
    if not seg.units:
        return Result(baseline, baseline, baseline, baseline, words,
                      source="main_engine_fallback", warnings=["لا توجد وحدات نثرية كافية."])

    engine = b.get_engine()
    weights = {i: len(engine.WORD_RE.findall(texts[i])) for u in seg.units for i in u.idx}
    total = sum(weights.values()) or 1
    local_score = 100.0 * sum(seg.para_post[i] * n for i, n in weights.items()) / total
    # Two views on the SAME text, not independent probabilities: an equal
    # arithmetic mean avoids multiplying correlated likelihoods or letting
    # either the global pool or the paragraph reader replace the other.
    score = (global_score + local_score) / 2.0
    # v3.2: a learned read of the same evidence, trained on external essays
    # only. It is gated above every training human and can only raise.
    import expository_head
    head = expository_head.decision(expository_head.inputs(
        {k: row[0] for k, row in active.items()}, [(u.words, u.x) for u in seg.units], local_score, z))
    lifted = expository_head.lift(score, head)
    head_lift, score = lifted - score, lifted
    warnings = []
    if abs(global_score - local_score) >= 30.0:
        warnings.append("اختلاف كبير بين القراءة العامة وقراءة الفقرات؛ النتيجة تحتاج مراجعة.")
    warnings.append("المؤشر يقيس القرائن الأسلوبية، ولا يثبت مصدر النص بعد إعادة الصياغة.")
    return Result(round(score, 1), global_score, local_score, baseline, words,
                  features=active, excluded=excluded, warnings=warnings,
                  paragraphs=[(i, round(100.0 * seg.para_post[i], 1), weights[i]) for i in sorted(weights)],
                  global_logit=z, head_logit=head, head_lift=head_lift,
                  slip_discount=sum(u.slip_discount * u.words for u in seg.units) / max(1, seg.words),
                  segmentation=seg)


def reasons(result):
    import backend_adapter as b
    labels = b.get_engine().FEATURE_LABELS
    rows = ["■ محرك v3: قراءة عامة %.1f، وقراءة فقرات %.1f؛ المؤشر يجمع القراءتين دون تغيير الأوزان لإرضاء نسبة مستهدفة."
            % (result.global_score, result.local_score)]
    if result.head_lift > 0:
        rows.append("▲ رأس النثر التفسيري (v3.2) تجاوز أعلى قيمة بشرية في التدريب؛ رفع المؤشر %.1f نقطة." % result.head_lift)
    positive = sorted(((k, c) for k, (_, _, c) in result.features.items() if c >= 0.2), key=lambda x: -x[1])[:5]
    negative = sorted(((k, c) for k, (_, _, c) in result.features.items() if c <= -0.2), key=lambda x: x[1])[:5]
    if positive:
        rows += ["▲ قرائن أسلوبية مرصودة:"] + ["   %+.2f  %s" % (c, labels.get(k, k)) for k, c in positive]
    if negative:
        rows += ["▼ قرائن أسلوبية تخفض الاشتباه:"] + ["   %+.2f  %s" % (c, labels.get(k, k)) for k, c in negative]
    rows.append("■ تاريخ التحرير والخطوط وآثار اللصق لا تثبت جهة تأليف النص، ولا تُضاف إلى هذا المؤشر أو تُخصم منه.")
    # Keep disagreement visible even in compact reports that show seven rows.
    return rows[:1] + ["⚠ " + w for w in result.warnings] + rows[1:]


def verdict(result):
    if result.source != SOURCE:
        return None
    if abs(result.global_score - result.local_score) >= 30.0:
        return "اختلاف بين مسارات التحليل — غير حاسم", "#f08c00"
    if result.score >= 65:
        return "قرائن أسلوبية آلية مرتفعة", "#e03131"
    if result.score >= 40:
        return "قرائن متعارضة — غير حاسم", "#f08c00"
    return "قرائن أسلوبية آلية منخفضة — لا تثبت بشرية النص", "#2b8a3e"
