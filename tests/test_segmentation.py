# -*- coding: utf-8 -*-
"""v2.5 paragraph-level segmentation (segment_signals). Synthetic texts only: no user file is used as a reference."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend_adapter as b        # noqa: E402
import segment_signals as sg       # noqa: E402
from test_detection_layers import _docx, LLM_PARA, HUMAN_PARA   # noqa: E402

LLM_B = ("Furthermore, the integration of digital tools into public services has fundamentally transformed how "
         "citizens interact with institutions, enhancing efficiency, transparency, and accountability. Consequently, "
         "it is essential to develop comprehensive strategies that address the technological, organizational, and "
         "social dimensions of this transformation, ensuring that its benefits are distributed equitably across "
         "diverse communities and that vulnerable groups are not left behind.")
HUMAN_B = ("The ferry was late again, so I wrote most of these notes on the quay (my handwriting is worse than usual). "
           "Two of the fishermen we interviewed in 2019 had moved to Misrata; the third, Salem, still keeps the boat "
           "his father bought in 1987. He laughed at our questions about 'livelihood strategies' -- he just fishes, "
           "and when the fish are gone he fixes engines. Perhaps that is the strategy.")


def test_mixed_document_is_read_as_a_share():
    paras = [HUMAN_PARA, HUMAN_B] * 6 + [LLM_PARA, LLM_B] * 6
    r = b.analyze_uploaded_bytes("m.docx", _docx(paras))
    assert r.segmentation is not None
    assert 25 <= r.score <= 75, r.score
    # the index and the map describe the same thing
    tot = sum(n for _, _, n in r.para_scores)
    hi = sum(n for _, sc, n in r.para_scores if sc >= 55)
    assert abs(100.0 * hi / tot - r.score) <= 15


def test_human_document_stays_low_and_generated_document_high():
    h = b.analyze_uploaded_bytes("h.docx", _docx([HUMAN_PARA, HUMAN_B] * 10))
    a = b.analyze_uploaded_bytes("a.docx", _docx([LLM_PARA, LLM_B] * 10))
    assert h.score < 20, h.score
    assert a.score > 80, a.score


def test_generated_block_is_located_in_the_map():
    paras = [HUMAN_PARA, HUMAN_B] * 5 + [LLM_PARA, LLM_B] * 4 + [HUMAN_PARA, HUMAN_B] * 5
    r = b.analyze_uploaded_bytes("loc.docx", _docx(paras))
    sc = {i: s for i, s, _n in r.para_scores}
    gen_idx = range(10, 18)
    assert sum(sc.get(i, 0) >= 55 for i in gen_idx) >= 6
    assert sum(sc.get(i, 0) >= 55 for i in list(range(0, 10)) + list(range(18, 28))) <= 2


def test_slips_inside_a_paragraph_count_for_that_paragraph_only():
    sloppy = LLM_PARA.replace(", ", " ,", 2).replace("framework to", "framework  to")
    seg_clean = sg.segment([LLM_PARA] * 3, ["body"] * 3, docx=True)
    eng = b.get_engine()
    rep = eng.analyze_text("\n".join([LLM_PARA, sloppy, LLM_PARA]))
    seg_slip = sg.segment(rep.texts, ["body"] * 3, rep.findings, docx=True)
    assert seg_slip.units[1].L < seg_clean.units[1].L
    assert abs(seg_slip.units[0].L - seg_clean.units[0].L) < 1e-9


def test_one_habit_alone_never_decides():
    # a human paragraph with long technical words only (one habit) stays below the generated range
    tech = ("Thermogravimetric characterisation of the polycrystalline specimens (see Table 3) showed decomposition "
            "between 310 and 340 degrees; I repeated the run twice because the first crucible cracked.")
    h = sg.habits(tech)
    assert sg.wording_score(h) < sg.X0


def test_arabic_documents_keep_the_document_reading():
    ar = ("يتناول هذا الفصل الإطار النظري للدراسة، ويعرض أهم المفاهيم المرتبطة بالموضوع، كما يستعرض الدراسات السابقة "
          "التي تناولت الظاهرة من جوانبها المختلفة، ويبين أوجه الاتفاق والاختلاف بينها وبين الدراسة الحالية. ") * 2
    r = b.analyze_pasted("\n\n".join([ar] * 12))
    assert r.segmentation is None


def test_report_shading_equals_the_index_and_follows_the_paragraphs():
    import nebras_report as nr
    paras = [HUMAN_PARA, HUMAN_B] * 5 + [LLM_PARA, LLM_B] * 4 + [HUMAN_PARA, HUMAN_B] * 5
    data = _docx(paras)
    r = b.analyze_uploaded_bytes("sh.docx", data)
    plan = nr.compute_plan(r, filename="sh.docx", data=data)
    assert plan.soft_words == 0
    assert abs(plan.percent - r.score) <= 3.0, (plan.percent, r.score)
    post = r.segmentation.para_post
    shaded = [i for i, (w, f) in plan.per_para.items() if f]
    assert shaded and min(post[i] for i in shaded) >= max(
        [post[i] for i, (w, f) in plan.per_para.items() if not f] or [0.0]) - 1e-9


def test_neighbours_never_attribute_a_paragraph_whose_own_evidence_is_human():
    seg = sg.Segmentation(units=[sg.Unit(idx=[0], words=100, L=3.0), sg.Unit(idx=[1], words=100, L=-1.7),
                                 sg.Unit(idx=[2], words=100, L=3.0)])
    post = sg._forward_backward([u.L for u in seg.units])
    for u, p in zip(seg.units, post):
        u.post = p
    sg.own_evidence_veto(seg)
    assert seg.units[1].post < sg.SUSPECT and seg.units[0].post >= sg.SUSPECT
    assert abs(seg.suspect_share - 2 / 3) < 1e-9
