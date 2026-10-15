"""Independent channel transfer and bounded editing effects; no user fixtures."""
import pytest

import backend_adapter as b
import segment_signals as sg
from tests.test_detection_layers import _docx, LLM_PARA, HUMAN_CLEAN_VARIED

TECHNICAL = (
    "The approximation remains rigorous within the evaluated framework, yielding a stable solution under matched conditions. "
    "The solver reduces the residual, ensuring robust convergence across the tested range. "
    "Each configuration uses the same boundary conditions so that differences can be compared against the baseline."
)


def test_technical_evidence_survives_a_generic_channel_that_misses_it(monkeypatch):
    monkeypatch.setattr(sg, "wording_score", lambda *args, **kwargs: 0.0)
    r = b.analyze_uploaded_bytes("technical.docx", _docx([TECHNICAL] * 40))
    assert r.tech.confidence > 0
    assert len(r.local_technical_support) == 40
    assert all(u.technical_L is not None and u.wording_L > 0 for u in r.segmentation.units)
    assert r.score > 65


def test_technical_document_does_not_transfer_support_into_human_paragraphs():
    r = b.analyze_uploaded_bytes("mixed.docx", _docx([TECHNICAL] * 40 + HUMAN_CLEAN_VARIED))
    assert all(i < 40 for i in r.local_technical_support)
    assert all(u.technical_L is None for u in r.segmentation.units if min(u.idx) >= 40)


def test_typing_errors_reduce_strong_evidence_without_erasing_it():
    edited = LLM_PARA.replace("The ", "the ", 1).replace("the ", "the the ", 1).replace("and ", "and and ", 1)
    clean = b.analyze_uploaded_bytes("clean.docx", _docx([LLM_PARA] * 16))
    errors = b.analyze_uploaded_bytes("errors.docx", _docx([edited] * 16))
    assert 65 < errors.score < clean.score
    assert all(0 <= u.slip_discount <= sg.SLIP_CAP for u in errors.segmentation.units)
    assert sum(u.slips for u in errors.segmentation.units) > 0


def test_audit_records_every_stage_without_mutating_the_engine():
    r = b.analyze_uploaded_bytes("audit.docx", _docx([TECHNICAL] * 40))
    assert r.score_stages[0]["score"] == pytest.approx(r.score_engine)
    assert r.score_stages[-1]["score"] == pytest.approx(r.score)
    assert all(0 <= stage["wording_only"] <= 100 for stage in r.score_stages)
