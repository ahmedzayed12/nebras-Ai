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
    assert abs(plan.total_percent - plan.engine_index) <= 3.0, (plan.total_percent, plan.engine_index)
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


def test_humanizer_spacing_marks_its_paragraphs_and_masked_letters_are_ignored():
    odd = LLM_PARA.replace("framework to", "framework\u2002to").replace("examine how", "examine\u2002how")
    assert sg.evasion_count(odd) == 2
    masked = "Thі\u2060s chаp\u2060ter pr\u2060esents the review"     # joiners / look-alike letters: not counted, not cleaned
    assert sg.evasion_count(masked) == 0
    paras = [HUMAN_PARA, HUMAN_B] * 4 + [odd.replace("These", "These\u2002")] * 6
    r = b.analyze_uploaded_bytes("ev.docx", _docx(paras))
    assert r.segmentation.evasion >= 10
    marked = [u for u in r.segmentation.units if u.tool_marked]
    assert marked and all(u.post >= sg.SUSPECT for u in marked)


def test_humanizer_spacing_counts_as_one_moderate_habit_after_cleaning():
    """v2.7: the spaces are read as ordinary spaces; the paragraph that carried them gets exactly SPACE_L, no more."""
    spaced = HUMAN_PARA.replace(" ", "\u2002", 12)
    clean = sg.segment([HUMAN_PARA] * 4, ["body"] * 4, docx=True)
    marked = sg.segment([spaced] * 4, ["body"] * 4, docx=True)
    assert marked.evasion >= 10 and all(u.tool_marked for u in marked.units)
    for a, c in zip(marked.units, clean.units):
        assert abs((a.L - c.L) - sg.SPACE_L) < 1e-6


def test_spinner_phrases_mark_their_paragraph():
    spun = ("The proposed model combines counterfeit consciousness with an irregular woodland classifier and reports "
            "the mean square mistake on each fold, which shows a clear improvement over the baseline methods.")
    assert sg.spinner_count(spun) == 3
    assert sg.spinner_count("A profound learning experience for the students in the village school.") == 0
    seg = sg.segment([spun] * 3, ["body"] * 3, docx=True)
    assert seg.spun == 9 and all(u.tool_marked for u in seg.units)


def test_scientific_notation_is_not_a_typing_slip():
    """'cobalt(II)', 'N,S-donor', '2:1', '[CoL2(NO3)2]' are notation; 'Ulysses(1922)' or 'it(see below)' are real slips."""
    assert sg.is_scientific_slip("treated with cobalt(II) chloride", 19)
    assert sg.is_scientific_slip("the same N,S-donor ligand set", 10)
    assert sg.is_scientific_slip("at 2:1 ligand ratio", 4)
    assert sg.is_scientific_slip("for [CoL2(NO3)2] the", 9)
    assert not sg.is_scientific_slip("Joyce's Ulysses(1922) is", 15)
    assert not sg.is_scientific_slip("we saw it(see below)", 9)
    assert not sg.is_scientific_slip("good,bad and ugly", 4)


def test_file_wide_push_never_shades_a_paragraph_whose_own_evidence_is_human():
    """v2.8: metadata / slip-free rule / neighbours can decide undecided paragraphs, never clearly human ones."""
    seg = sg.segment([LLM_PARA] * 3 + [HUMAN_PARA] + [LLM_PARA] * 3, ["body"] * 7, docx=True, global_logit=2.5)
    human = seg.units[3]
    assert human.own_L <= sg.OWN_HUMAN_VETO
    sg.own_evidence_veto(seg)
    assert human.post < sg.SUSPECT


def test_paragraphs_placed_outside_word_are_a_trace_but_never_shade_human_wording():
    """v2.9: in a Word-stamped file, paragraphs without Word's revision stamps were written into the XML by a tool."""
    import io, zipfile
    def p(t, stamped):
        a = ' w:rsidR="00A1B2C3" w:rsidRDefault="00A1B2C3"' if stamped else ""
        return '<w:p%s><w:r><w:t>%s</w:t></w:r></w:p>' % (a, t)
    body = "".join(p(HUMAN_PARA, True) for _ in range(20)) + "".join(p(LLM_PARA, False) for _ in range(3))
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main"><w:body>%s</w:body></w:document>' % body)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    keys = sg.outside_word_texts(buf.getvalue())
    assert len(keys) == 1 and sg._okey(LLM_PARA) in keys            # three identical unstamped paragraphs
    assert sg.outside_word_texts(buf.getvalue().replace(b"x", b"x")) == keys
    # a file with no stamps at all (Google Docs / LibreOffice / python-docx) is not "mixed": nothing is flagged
    plain = xml.replace(' w:rsidR="00A1B2C3" w:rsidRDefault="00A1B2C3"', "")
    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w") as z:
        z.writestr("word/document.xml", plain)
    assert sg.outside_word_texts(buf2.getvalue()) == frozenset()
    # the trace lifts the unit, but a paragraph whose own wording is clearly human stays unshaded
    seg = sg.segment([HUMAN_PARA] * 5, ["body"] * 5, docx=True, outside_texts=frozenset({sg._okey(HUMAN_PARA)}))
    sg.own_evidence_veto(seg)
    assert seg.outside == 5 and all(u.post < sg.SUSPECT for u in seg.units if u.own_L <= sg.OWN_HUMAN_VETO)
