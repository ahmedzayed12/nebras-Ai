import json
from pathlib import Path

import pytest

import backend_adapter as b
import balanced_engine as v3
from tools.benchmark_balanced import docx, variants
from tests.test_detection_layers import HUMAN_CLEAN_VARIED

CONTROLS = json.loads((Path(__file__).parent / "fixtures/generated_controls.json").read_text())


@pytest.mark.parametrize("case", CONTROLS, ids=lambda c: c["id"])
def test_word_edit_history_cannot_decide_authorship(case):
    plain = b.analyze_uploaded_bytes("plain.docx", docx(case["text"]))
    edited = b.analyze_uploaded_bytes("edited.docx", docx(case["text"], reviewed=True))
    assert plain.score_source == edited.score_source == v3.SOURCE
    assert plain.final_score == pytest.approx(edited.final_score, abs=0.1)
    assert all(k not in edited.balanced.features for k in v3.FILE_FEATURES)


@pytest.mark.parametrize("case", CONTROLS[:3], ids=lambda c: c["id"])
def test_surface_rewrite_and_errors_reduce_cues_without_erasing_strong_ones(case):
    scores = {}
    for kind, text, reviewed in variants(case["text"]):
        r = b.analyze_uploaded_bytes("control.docx", docx(text, reviewed))
        scores[kind] = r.final_score
        assert 0 <= r.final_score <= 100
    assert 65 < scores["typing_errors"] < scores["original"]
    assert scores["surface_rewrite"] >= 65
    assert scores["original"] == pytest.approx(scores["spacing"], abs=0.1)
    assert scores["surface_rewrite"] == pytest.approx(scores["rewrite_and_review"], abs=0.1)


def test_clean_human_account_is_not_accused_for_lack_of_errors():
    r = b.analyze_uploaded_bytes("human.docx", docx("\n\n".join(HUMAN_CLEAN_VARIED)))
    assert r.final_score < 40
    assert not any("الإنسان يخطئ" in reason for reason in r.reasons)


def test_plain_generated_counterexample_is_reported_without_claiming_human_origin():
    # Known-origin synthetic plain rewriting remains a false negative. Keep it
    # visible; do not insert a floor or silently label the fixture as human.
    text = CONTROLS[-1]["text"]
    r = b.analyze_uploaded_bytes("plain_generated.docx", docx(text))
    assert r.final_score < 52
    assert "لا تثبت بشرية" in r.final_verdict
    assert r.balanced.warnings


def test_confusable_letters_and_source_text_are_not_cleaned():
    text = CONTROLS[0]["text"].replace("a", "а").replace("i", "і")
    r = b.analyze_uploaded_bytes("untouched.docx", docx(text))
    assert all(p in r.texts for p in text.split("\n\n"))
    assert "а" in "".join(r.texts) and "і" in "".join(r.texts)


def test_arabic_uses_explicit_fallback_instead_of_english_validation_claim():
    text = "يهدف هذا البحث إلى دراسة أثر التحول الرقمي في التعليم العالي، وتحليل التحديات واقتراح الحلول المناسبة. " * 30
    r = b.analyze_uploaded_bytes("arabic.docx", docx(text))
    assert r.score_source == "main_engine_fallback"
    assert r.final_score == r.score_engine
    assert r.balanced.warnings


def test_published_score_has_one_reproducible_equation_and_localisation():
    r = b.analyze_uploaded_bytes("equation.docx", docx(CONTROLS[0]["text"]))
    # Recompute the published equation independently of the engine's head_lift field.
    import math
    import expository_head as eh
    p, bl = eh.PARAMS, r.balanced
    rows = {k: v for k, v in bl.features.items()}
    values = [rows[k][2] / rows[k][1] if k in rows and rows[k][1] else 0.0 for k in eh.ENGINE_INPUTS]
    units = [(u.words, u.x) for u in bl.segmentation.units]
    xs = [x for _, x in units]
    local = min(max(bl.local_score, 0.5), 99.5) / 100
    values += [sum(w * x for w, x in units) / sum(w for w, _ in units), max(xs), sum(x > 1 for x in xs) / len(xs),
               math.log(local / (1 - local)), bl.global_logit * r.confidence]
    d = r.confidence * (p["intercept"] + sum(c * (v - m) / s for v, m, s, c in zip(values, p["mean"], p["scale"], p["coef"])))
    mean = (bl.global_score + bl.local_score) / 2
    expected = mean + (100 - mean) * 0.8 * (1 - math.exp(-(d - p["gate"]))) if d > p["gate"] else mean
    assert r.final_score == round(expected, 1)
    assert b.primary_paragraph_scores(r) == r.balanced.paragraphs
    assert b.primary_segmentation(r) is r.balanced.segmentation
    assert r.score_engine == r.score_stages[0]["score"]


def test_disagreeing_channels_abstain_from_high_confidence_verdict():
    result = v3.Result(66, 99, 33, 90, 500)
    assert v3.INCONCLUSIVE_MARK in v3.verdict(result)[0]
    assert "حاسم" not in v3.verdict(result)[0]
