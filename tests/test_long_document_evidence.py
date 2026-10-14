"""Long documents must retain local evidence without inflating human topic words.

All inputs are synthetic; user documents and claimed target scores are not
training data or test fixtures.
"""
import pytest
from types import SimpleNamespace

import academic_signals as acad
import ai_signals
import backend_adapter as b
import tech_signals as tech
from tests.test_detection_layers import _docx, LLM_PARA, HUMAN_CLEAN_VARIED


@pytest.mark.parametrize("module", [acad, tech])
def test_bounded_vocabulary_density_does_not_vanish_in_long_documents(module):
    short = module.measure([LLM_PARA] * 30)["lex"]
    long = module.measure([LLM_PARA] * 240)["lex"]
    assert short > 8
    assert long == pytest.approx(short)


@pytest.mark.parametrize("module", [acad, tech])
def test_two_repeated_topic_words_cannot_dominate_a_long_document(module):
    text = "The framework handles robust inputs, and the framework keeps the robust design simple. " * 3
    assert module.measure([text] * 240)["lex"] <= 6


def test_stock_phrase_density_does_not_vanish_in_a_long_chapter():
    short = ai_signals.analyze([LLM_PARA] * 30)
    long = ai_signals.analyze([LLM_PARA] * 240)
    signal = lambda layer: next(s for s in layer.signals if s.key == "stock")
    assert signal(long).z == pytest.approx(signal(short).z)


def test_late_paragraphs_keep_the_same_evidence_as_identical_early_ones():
    eng = b.get_engine()
    r = b.analyze_uploaded_bytes("long.docx", _docx([LLM_PARA] * 220))
    assert r.complete_style_findings
    assert any(after > before for before, after in r.style_storage_recovered.values())
    assert len(r.wording_map) == 220
    assert len({sc for _, sc, _ in r.wording_map}) == 1
    assert len({sc for _, sc, _ in r.para_scores}) == 1
    assert eng.CAP_PER_KIND == 400  # do not mutate the protected engine


def test_long_human_technical_lists_are_neutralized_after_the_storage_limit():
    text = ("Devices use MQTT, CoAP, and Zigbee to transmit the readings from the field to the station. "
            "The network was tested on ARM, MIPS, and RISC-V processors during the trial.")
    r = b.analyze_uploaded_bytes("technical.docx", _docx([text] * 230))
    assert r.complete_style_findings
    assert not any(f.kind == "ai_tricolon" for f in r.findings)
    assert r.features["tricolon"][2] == 0
    assert r.score < 30


def test_high_pooled_score_does_not_skip_local_bilingual_review():
    eng = b.get_engine()
    paras = [eng.Para(text=LLM_PARA) for _ in range(12)]
    for p in paras:
        p.role = "body"
    r = SimpleNamespace(score=99.0, para_scores=[(i, 20.0, 75) for i in range(12)],
                        texts=[p.text for p in paras])
    b._local_style_review(r, paras)
    assert r.score == 99.0
    assert all(sc > 20 for _, sc, _ in r.para_scores)


def test_academic_local_review_runs_when_document_fusion_adds_nothing(monkeypatch):
    reviewed = []
    original = b._lift_paras

    def observe(report, module, confidence, **kwargs):
        reviewed.append(module)
        return original(report, module, confidence, **kwargs)

    monkeypatch.setattr(b, "_lift_paras", observe)
    monkeypatch.setattr(acad, "fuse", lambda score, layer: score)
    r = b.analyze_uploaded_bytes("academic.docx", _docx([LLM_PARA] * 30))
    assert r.academic.confidence > 0
    assert acad in reviewed


@pytest.mark.parametrize("text", [
    "The researcher described the methodological characteristics and organizational responsibilities in the institutional documentation for subsequent evaluation. The terminology distinguished administrative responsibilities from organizational obligations. The definitions were listed in the appendix and the relationships were described in the accompanying documentation for the committee and its members.",
    "The chair asked the secretary to update the list, providing the revised copy to each member before the next meeting began. The note listed who would attend and where they would sit. During the review she checked every name against the paper register before she sent the copy to the other members.",
])
def test_single_local_habit_cannot_borrow_document_wide_confidence(text):
    assert len(acad._W.findall(text)) >= 40
    assert acad.para_score(text) is None
    assert tech.para_score(text) is None


def test_narrative_lists_do_not_create_rhetorical_evidence():
    assert acad.count_triads("The farmers mentioned the 2019 floods, a disputed loan, and a chairman who had left.") == 0
    assert acad.count_triads("He laughed, gave us groundnuts, and asked us to send a copy.") == 0
    assert acad.count_triads("The framework fosters collaboration, resilience, and innovation across sectors.") > 0


def test_scattered_word_formatting_only_affects_the_paragraph_that_has_it():
    from tests.test_structure_fairness import _docx as formatted_docx
    rows = [("Literature Review", "center", 28, True)]
    rows += [(LLM_PARA, "center" if i == 5 else "both", 24) for i in range(12)]
    r = b.analyze_uploaded_bytes("scattered.docx", formatted_docx(rows))
    assert r.file_shifts[6] < r.file_shifts[1] == r.file_shifts[12]
    assert r.file_shift_uniform is False


def test_repeated_word_formatting_blocks_do_not_supply_human_credit_to_every_paragraph():
    from tests.test_structure_fairness import _docx as formatted_docx
    rows = [("Literature Review", "center", 28, True)]
    rows += [(LLM_PARA, "center" if 4 <= i < 8 else "both", 24) for i in range(16)]
    r = b.analyze_uploaded_bytes("blocks.docx", formatted_docx(rows))
    assert r.features["fmt_incons"][0] > 0  # formatting differences are still observed
    assert r.features["fmt_incons"][2] == 0  # a repeated block is not human authorship evidence
    assert set(r.file_shifts.values()) == {0.0}
    assert r.score >= 65


def test_long_mixed_document_responds_to_rewriting_and_keeps_human_passages_low():
    scores = []
    for replaced in (0, 40, 80, 120):
        paragraphs = [LLM_PARA] * (120 - replaced)
        paragraphs.extend(HUMAN_CLEAN_VARIED[i % len(HUMAN_CLEAN_VARIED)] for i in range(replaced))
        r = b.analyze_uploaded_bytes("revised.docx", _docx(paragraphs))
        scores.append(r.score)
        human = [sc for i, sc, _ in r.para_scores if i >= 120 - replaced]
        assert not human or max(human) < 52
    assert all(a > c for a, c in zip(scores, scores[1:]))
    assert scores[0] >= 65 and scores[-1] < 52
