# -*- coding: utf-8 -*-
"""Fairness + sensitivity tests for the 'reasons, not pressure' fixes (synthetic documents only; no user file used).

Each fix has two tests: it must HELP the human case it was made for, and it must NOT blind the detector on generated text."""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import academic_signals as acad   # noqa: E402
import backend_adapter as b       # noqa: E402
from tests.test_detection_layers import _docx, LLM_PARA, HUMAN_PARA   # noqa: E402

# a human-looking thesis chapter ABOUT a framework: the topic words are forced by the subject
TOPIC_PARA = ("The framework proposed in Chapter 4 separates terminology errors from errors of legal function. Each framework "
              "dimension was piloted on 12 contracts in 2024, and the framework was revised twice after the supervisor's comments. "
              "A second framework, built by Lee in 2021, was kept only as a baseline; its nuances are discussed in Section 2.3.")


def test_topic_words_are_not_counted_as_llm_vocabulary():
    paras = [TOPIC_PARA] * 40
    plain = acad.measure(paras)["lex"]
    topic = acad.topic_terms(["A Hybrid Evaluation Framework for Legal Translation", "Cultural Nuances in Legal Texts"])
    with_topic = acad.measure(paras, topic)["lex"]
    assert plain < 8 and with_topic == 0           # a word repeated 120 times is the subject: it counts at most 3 times anyway


def test_topic_exclusion_does_not_blind_the_detector():
    topic = acad.topic_terms(["A Comprehensive Framework for Transformation"])   # even if the title names two lexicon words...
    layer = acad.analyze([LLM_PARA] * 30, topic)
    assert layer.confidence > 0 and layer.fired >= 3 and layer.score > 70            # ...the other habits still converge


def _stub(raw=-3.1, z=-3.0, conf=1.0):
    class R:
        features = {"meta_human": (1, -1.7, -1.7), "fmt_incons": (1, -1.0, -1.0), "mixed_conv": (1, -0.4, -0.4)}
    r = R()
    r.features = {"meta_human": (1, raw * 0.5, raw * 0.5), "fmt_incons": (1, raw * 0.3, raw * 0.3), "mixed_conv": (1, raw * 0.2, raw * 0.2)}
    r.z, r.confidence, r.score = z, conf, 100 / (1 + 2.718281828 ** (-z))
    return r


def test_container_relaxation_is_proportional_and_monotone():
    scores = []
    for s in (0.0, 0.25, 0.5, 0.75, 1.0):
        r = _stub()
        b._recalibrate_container(r, s)
        scores.append(r.score)
    assert all(x <= y + 1e-9 for x, y in zip(scores, scores[1:]))        # more convergence never lowers the index
    assert len(set(round(x, 6) for x in scores)) == 5                    # no fixed steps / cliffs
    full = _stub(); b._recalibrate_container(full, 1.0)
    assert abs(full.z - (-3.0 + 3.1 - 1.0)) < 1e-9                       # strength 1 == the old behaviour exactly
    none = _stub(); assert not b._recalibrate_container(none, 0.0) and none.z == -3.0


def test_two_academic_habits_cannot_erase_most_human_evidence():
    two = acad.AcadLayer(60.0, 0.3, 2, 5000, strength=0.3)
    three = acad.AcadLayer(90.0, 1.0, 3, 5000, strength=1.0)
    assert two.strength < three.strength
    r2, r3 = _stub(), _stub()
    b._recalibrate_container(r2, two.strength); b._recalibrate_container(r3, three.strength)
    assert r2.z < r3.z - 1.0


class _P:       # paragraph map stub
    def __init__(self, scores):
        self.para_scores = [(i, s, 100) for i, s in enumerate(scores)]
        self.features = {"fmt_incons": (1, -1.7, -1.7), "meta_human": (1, -0.5, -0.5)}
        self.confidence = 1.0


def test_file_evidence_shifts_paragraph_map_down_only():
    r = _P([95, 85, 70, 60, 40])
    assert b._file_evidence_to_paragraphs(r)
    new = [s for _, s, _ in r.para_scores]
    assert all(n <= o for n, o in zip(new, [95, 85, 70, 60, 40])) and new == sorted(new, reverse=True)
    assert new[0] >= 55 and new[3] < 55            # very strong wording evidence survives, borderline paragraphs need support
    clean = _P([95, 60]); clean.features = {}
    assert not b._file_evidence_to_paragraphs(clean) and [s for _, s, _ in clean.para_scores] == [95, 60]


def test_generated_text_stays_high_with_and_without_human_file_traces():
    plain = b.analyze_uploaded_bytes("a.docx", _docx([LLM_PARA] * 30))
    assert plain.score >= 65
    z = zipfile.ZipFile(io.BytesIO(_docx([LLM_PARA] * 30)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for it in z.infolist():
            out.writestr(it, z.read(it.filename))
        out.writestr("docProps/app.xml", '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/'
                     '2006/extended-properties"><TotalTime>2400</TotalTime><Application>Microsoft Office Word</Application></Properties>')
        out.writestr("word/comments.xml", '<?xml version="1.0"?><w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    edited = b.analyze_uploaded_bytes("e.docx", buf.getvalue())
    assert edited.score >= 65                      # a human opened the file; the wording is still generated
    ps = edited.para_scores
    assert sum(n for _, s, n in ps if s >= 55) / sum(n for _, _, n in ps) >= 0.9


def test_human_wording_is_not_flagged_in_docx():
    r = b.analyze_uploaded_bytes("h.docx", _docx([HUMAN_PARA] * 12))
    assert r.score < 52


def test_headings_are_not_labelled_items():
    """Real alignment check: headings of a real .docx never reach the technical layer, body paragraphs always do."""
    import docx as _d
    import tech_signals as ts
    d = _d.Document()
    heads = ["Two Axes of Equivalence: Linguistic and Legal", "Skopos Theory: Purpose and Function", "Register Transfer: English and Arabic"]
    body = ("The committee reviewed the draft in May and asked for two changes to the budget table, which we made "
            "before the second round of comments arrived from the department.")
    for h in heads:
        d.add_heading(h, 1)
        d.add_paragraph(body)
    buf = io.BytesIO(); d.save(buf)
    r = b.analyze_uploaded_bytes("heads.docx", buf.getvalue())
    kept = b._tech_input_texts(r, ts)
    assert kept and all(h not in kept for h in heads) and body in kept
    assert ts.measure(heads)["label"] == 0 or ts.measure(["\n".join(heads)])["label"] >= 0   # sanity: helper runs on raw text too


# ----- internal-evidence fixes: subject words, technical lists, single habits ---------------------------------------
def test_one_repeated_word_cannot_fire_the_vocabulary_habit():
    para = "The framework handles robust inputs, and the framework keeps the robust design simple. " * 3
    assert acad.measure([para] * 60)["lex"] < 8


def test_many_different_marker_words_still_count():
    para = ("This pivotal study delves into the intricate interplay of crucial factors, showcasing a multifaceted, holistic "
            "and comprehensive landscape that underscores noteworthy insights and fosters seamless, transformative outcomes.")
    assert acad.measure([para] * 20)["lex"] > 14


def test_technical_enumerations_are_not_rhetorical_triads():
    tech = "Devices speak protocols such as MQTT, CoAP, and Zigbee, and layers use 128, 64, and 32 neurons, on ARM, MIPS, or RISC-V cores."
    assert acad.count_triads(tech) == 0
    rhetorical = "The study fosters collaboration, resilience, and innovation across institutions, communities, and sectors."
    assert acad.count_triads(rhetorical) >= 1


def test_a_single_technical_habit_never_lifts_the_index():
    import tech_signals as ts
    para = ("The module normalises the traffic, allowing the model to adapt quickly. The tuner adjusts the rate, making training "
            "stable. The loader batches inputs, reducing memory use. The queue buffers packets, preserving their order. " * 3)
    layer = ts.analyze([para] * 12)
    assert sum(1 for x in layer.signals if x.z >= 0.35 and x.key != "friction") <= 1
    assert ts.fuse(10.0, layer) == 10.0
