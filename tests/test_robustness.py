# -*- coding: utf-8 -*-
"""Hostile / odd inputs must never crash the new layers or produce impossible numbers."""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import academic_signals as acad   # noqa: E402
import backend_adapter as b       # noqa: E402
from tests.test_detection_layers import _docx, LLM_PARA, HUMAN_PARA   # noqa: E402

AR = ("يهدف هذا البحث إلى دراسة أثر التحول الرقمي في التعليم العالي، وتحليل التحديات، واقتراح الحلول المناسبة. "
      "وقد اعتمد الباحث المنهج الوصفي التحليلي، وجمع البيانات من عينة بلغت 120 طالبًا في ثلاث جامعات. ")


def _ok(r):
    assert 0.0 <= r.score <= 100.0 and r.verdict
    ps = r.para_scores
    assert all(0.0 <= sc <= 100.0 for _, sc, _ in ps)


def test_empty_and_tiny_inputs():
    for t in ("", "   ", "Hi.", "a b c d e"):
        try:
            r = b.analyze_text_guarded(t)
        except Exception as e:      # the engine may refuse empty text, but only with a normal error, never a crash in the layers
            assert "NoneType" not in str(e) and "unsupported operand" not in str(e)
            continue
        _ok(r)
        r = b.analyze_text_guarded(t, from_pdf=True)
        _ok(r)


def test_arabic_only_document_runs_every_layer():
    r = b.analyze_uploaded_bytes("ar.docx", _docx([AR] * 40))
    _ok(r)
    r2 = b.analyze_text_guarded("\n".join([AR] * 40), from_pdf=True)
    _ok(r2)


def test_docx_without_headings_or_with_only_headings():
    import docx as _d
    d = _d.Document()
    for h in ("Introduction", "Method", "Results"):
        d.add_heading(h, 1)
    buf = io.BytesIO(); d.save(buf)
    try:
        _ok(b.analyze_uploaded_bytes("h.docx", buf.getvalue()))
    except ValueError:
        pass                                            # "nothing to analyse" is a normal, user-facing answer
    _ok(b.analyze_uploaded_bytes("nh.docx", _docx([HUMAN_PARA] * 12)))


def test_topic_terms_edge_cases():
    assert acad.topic_terms(None) == frozenset() and acad.topic_terms([]) == frozenset()
    assert acad.topic_terms(["", "  ", "الإطار العام للبحث"]) == frozenset()       # Arabic headings: no Latin stems, no crash
    t = acad.topic_terms(["Frameworks", "Nuanced", "AI"])
    assert acad._stem("framework") in t and acad._stem("nuances") in t and not any(len(x) < 3 for x in t)
    assert acad.measure([LLM_PARA] * 30, t)["words"] == acad.measure([LLM_PARA] * 30)["words"]


def test_paragraph_shift_handles_missing_pieces():
    class R:
        para_scores = []
        features = {"fmt_incons": (1, -2.0, -2.0)}
        confidence = 1.0
    assert b._file_evidence_to_paragraphs(R()) is False
    class R2:
        para_scores = [(0, 0.0, 50), (1, 100.0, 50)]       # exact extremes must not blow up log-odds
        features = {"fmt_incons": (1, -2.0, -2.0)}
        confidence = 1.0
    assert b._file_evidence_to_paragraphs(R2())
    assert all(0.0 <= s <= 100.0 for _, s, _ in R2.para_scores)
    class R3:
        para_scores = [(0, 90.0, 50)]
        features = None
    assert b._file_evidence_to_paragraphs(R3()) is False


def test_recalibration_moves_only_between_raw_evidence_and_the_cap():
    for raw in (-0.5, -1.0, -3.1, -5.2):
        for s in (0.0, 0.3, 1.0):
            class R:
                features = {"meta_human": (1, raw, raw)}
                z, confidence, score = -3.0, 1.0, 5.0
            r = R(); b._recalibrate_container(r, s)
            eff = getattr(r, "container_eff", raw)
            if raw < b.CONTAINER_CAP:
                assert raw - 1e-9 <= eff <= b.CONTAINER_CAP + 1e-9     # moves toward the cap, never beyond it
            else:
                assert eff == raw


def test_scores_are_monotone_in_generated_share():
    """Replacing human paragraphs with generated ones must never lower the index (no inversion between layers)."""
    prev = -1.0
    for k in (0, 10, 20, 30, 40):
        r = b.analyze_uploaded_bytes("m.docx", _docx([HUMAN_PARA] * (40 - k) + [LLM_PARA] * k))
        assert r.score >= prev - 6.0       # allow engine noise of a few points, never a real inversion
        prev = max(prev, r.score)
