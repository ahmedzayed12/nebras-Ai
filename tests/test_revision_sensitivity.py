"""Editing sensitivity without user documents or target-specific weights."""
from types import SimpleNamespace

import pytest
import backend_adapter as b
from tests.test_detection_layers import _docx, LLM_PARA, HUMAN_CLEAN_VARIED


def test_low_scoring_prose_dilutes_document_evidence_by_word_count():
    short = SimpleNamespace(score=99.0, para_scores=[(0, 90.0, 100), (1, 10.0, 100)])
    long = SimpleNamespace(score=99.0, para_scores=[(0, 90.0, 100), (1, 10.0, 900)])
    b._aggregate_paragraph_index(short)
    b._aggregate_paragraph_index(long)
    assert short.score == 50.0
    assert long.score == 18.0
    assert short.score_pooled == long.score_pooled == 99.0


def test_local_evidence_cannot_raise_a_low_document_score():
    r = SimpleNamespace(score=5.0, para_scores=[(0, 90.0, 100)])
    assert not b._aggregate_paragraph_index(r)
    assert r.score == 5.0


def test_empty_map_and_slide_deck_keep_their_scores():
    for r in (SimpleNamespace(score=60.0, para_scores=[]),
              SimpleNamespace(score=60.0, para_scores=[(0, 5.0, 100)],
                              deck=SimpleNamespace(is_deck=True))):
        assert not b._aggregate_paragraph_index(r)
        assert r.score == 60.0


def test_rewriting_progressively_reduces_suspicion_and_keeps_detection():
    scores = []
    for replaced in (0, 6, 12, 18, 24):
        paras = [LLM_PARA] * (24 - replaced)
        paras += [HUMAN_CLEAN_VARIED[i % len(HUMAN_CLEAN_VARIED)] for i in range(replaced)]
        r = b.analyze_uploaded_bytes("revision.docx", _docx(paras))
        scores.append(r.score)
        assert r.score_stages[-1]["score"] == pytest.approx(r.score)
        # v2.5: with the paragraph reading the index is the share of attributed words (it may exceed the pooled score
        # of a mixed file); without it the paragraph aggregation may only lower the pooled score.
        if getattr(r, "segmentation", None) is not None:
            assert r.score == pytest.approx(round(100.0 * r.ai_share, 1))
        else:
            assert r.score <= r.score_pooled
    assert all(a > c for a, c in zip(scores, scores[1:]))
    assert scores[0] >= 65
    assert scores[-1] < 52
