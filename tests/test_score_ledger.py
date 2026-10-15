"""Arithmetic contracts, independent of target percentages or user documents."""
import math
from types import SimpleNamespace

import pytest

import backend_adapter as b
import tech_signals
from tests.test_detection_layers import _docx, LLM_PARA, HUMAN_PARA


def test_container_review_is_idempotent_and_updates_its_feature_ledger():
    features = {"meta_human": (1, -2.2, -2.2), "fmt_incons": (1, -1.8, -1.8)}
    r = SimpleNamespace(features=features, z=-2.0, score=12.0, confidence=1.0)
    assert b._recalibrate_container(r, 1.0)
    first = (r.z, r.score, dict(r.features))
    assert sum(row[2] for row in r.features.values()) == pytest.approx(-1.0)
    assert not b._recalibrate_container(r, 1.0)
    assert (r.z, r.score, r.features) == first
    # Lowering the requested strength recomputes from the same original
    # evidence, rather than relaxing the already relaxed contribution again.
    b._recalibrate_container(r, 0.0)
    assert r.z == pytest.approx(-2.0)
    assert r.features == features


def test_feature_changes_replay_fusion_instead_of_treating_fused_score_as_logit():
    layer = SimpleNamespace(score=85.0, confidence=0.8)
    r = SimpleNamespace(z=-2.0, confidence=1.0,
                        score_fusions=[("technical", layer)])
    b._rescore(r)
    before = r.score
    r.z += 1.0
    b._rescore(r)
    expected = tech_signals.fuse(100.0 / (1.0 + math.exp(1.0)), layer)
    wrong = 100.0 / (1.0 + math.exp(-(math.log(before / (100.0 - before)) + 1.0)))
    assert r.score == pytest.approx(expected)
    assert abs(r.score - wrong) > 5.0


@pytest.mark.parametrize("text", [LLM_PARA, HUMAN_PARA])
def test_every_stage_keeps_the_feature_sum_equal_to_its_logit(text):
    chat = '<w:rFonts w:ascii="Söhne" w:hAnsi="Söhne"/><w:color w:val="0D0D0D"/>'
    r = b.analyze_uploaded_bytes("ledger.docx", _docx([text] * 40, run_props=chat))
    assert all(abs(stage["ledger_error"]) < 1e-9 for stage in r.score_stages)


def test_html_export_uses_published_score_and_matching_evidence(monkeypatch):
    r = b.analyze_uploaded_bytes("export.docx", _docx([LLM_PARA] * 30))
    r.score = 1.0
    r.features = {"diagnostic_only": (1, 1, 1)}
    observed = []
    monkeypatch.setattr(b.get_engine(), "report_to_html", lambda view: observed.append(view) or "report")
    assert b.html_report_bytes(r) == b"report"
    assert observed[0].score == r.final_score
    assert observed[0].features == r.balanced.features
    assert r.score == 1.0 and r.features == {"diagnostic_only": (1, 1, 1)}
