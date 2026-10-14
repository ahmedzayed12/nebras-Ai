# -*- coding: utf-8 -*-
"""Paragraph localisation (provenance.py + nebras_report.plan_highlights). Synthetic documents only.

The properties that matter, each checked on its own:
  * pasted material is located as WHOLE paragraphs, and only those;
  * honest text is never shaded because of its file traces (a human web paste with chat-ish formatting);
  * shading is never stretched to reach the document index (the old allocation painted human paragraphs);
  * a clean document gets no shading at all.
"""
import html
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backend_adapter as ba   # noqa: E402
import nebras_report as nr     # noqa: E402
import provenance as pv        # noqa: E402

HUMAN = [
    "I spent most of the spring of 2016 in two villages north of the river, and what struck me first was how little the elders cared about the questionnaire. We sat under the mango tree; they talked about the 1998 floods, about a cousin who went to Accra, about prices.",
    "My notes from 14 April are a mess. Still, three things came up again and again: land, school fees and the new road. Nobody mentioned the clinic , which surprised me  a lot.",
    "The second village was different. The headman was away, so his nephew (a boy of maybe nineteen) took me round the fields and argued about the price of fertiliser the whole way.",
    "By June the rains had washed out the track and I stayed in town for three weeks, re-reading my notebook and cursing my own handwriting. I rewrote the interview guide twice.",
]
AI = [
    "This study adopts a comprehensive framework to examine how institutional, organizational, and cultural factors shape the implementation of sustainable practices. The findings highlight the crucial interplay between leadership, collaboration, and accountability, underscoring the need for holistic and context-sensitive strategies.",
    "Additionally, the analysis provides valuable insights for policymakers, practitioners, and researchers seeking to enhance resilience, foster innovation, and ensure equitable outcomes. Furthermore, it is important to note that these dynamics play a pivotal role in shaping long-term development trajectories.",
    "Moreover, the integration of participatory approaches fosters a nuanced understanding of community needs, thereby facilitating robust and inclusive decision-making processes. Overall, this underscores the multifaceted nature of rural development and the need for adaptive governance.",
]
TNR = '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"/><w:sz w:val="24"/>'
CHAT = '<w:rFonts w:ascii="Söhne" w:hAnsi="Söhne"/><w:color w:val="0D0D0D"/><w:sz w:val="24"/>'
UI = '<w:spacing w:beforeAutospacing="1" w:afterAutospacing="1"/>'


def _p(text, rpr=TNR, ppr="", rsid="00AA0001"):
    return ('<w:p w:rsidR="%s"><w:pPr>%s</w:pPr><w:r><w:rPr>%s</w:rPr><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % (rsid, ppr, rpr, html.escape(text, quote=False)))


def _docx(items):
    doc = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s<w:sectPr/></w:body></w:document>' % "".join(items))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def _plan(data, name="t.docx"):
    rep = ba.analyze_uploaded_bytes(name, data)
    paras, kind, mm = nr._paras_for(rep, name, data, None)
    return rep, paras, nr.balance_to_engine_index(rep, paras, nr.plan_highlights(rep, paras, nr._prov_for(kind, data, mm)))


def _cover(plan, paras, i):
    n = len(paras[i].text)
    st = sum(b - a for a, b in plan.flags.get(i, ()))
    so = sum(b - a for a, b in plan.soft_flags.get(i, ()))
    return st / n, so / n


def test_provenance_reads_ui_fingerprints_per_paragraph():
    data = _docx([_p(t) for t in HUMAN[:2]] + [_p(t, CHAT, UI, "00BB0002") for t in AI])
    prov = pv.read(data)
    assert [prov[i].has_ui_fingerprint for i in range(5)] == [False, False, True, True, True]
    assert prov[2].sig == prov[3].sig == prov[4].sig and prov[0].sig is None
    assert prov[0].bonus == 0.0 and prov[2].bonus > 1.0


def test_one_pasted_word_does_not_mark_a_paragraph():
    mixed = ('<w:p><w:r><w:rPr>%s</w:rPr><w:t xml:space="preserve">%s </w:t></w:r><w:r><w:rPr>%s</w:rPr><w:t>word</w:t></w:r></w:p>'
             % (TNR, "x" * 200, CHAT))
    prov = pv.read(_docx([mixed]))
    assert prov[0].direct == 0.0


def test_pasted_block_is_located_as_supported_whole_sentences():
    data = _docx([_p(t) for t in HUMAN[:3]] + [_p(t, CHAT, UI, "00BB0002") for t in AI] + [_p(HUMAN[3], rsid="00AA0003")])
    _rep, paras, plan = _plan(data)
    for i in (3, 4, 5):
        assert _cover(plan, paras, i)[0] > 0
        spans = ba.get_engine().split_sentences(paras[i].text)
        for a, b in plan.flags[i]:
            assert a in {x for x, _ in spans}
            assert b in {y for _, y in spans}
    for i in (0, 1, 2, 6):
        assert _cover(plan, paras, i) == (0.0, 0.0)        # human paragraphs untouched, not even the pale tint


def test_clean_document_gets_no_shading():
    _rep, paras, plan = _plan(_docx([_p(t) for t in HUMAN * 3]))
    assert not plan.flags and not plan.soft_flags


def test_human_web_paste_with_chat_formatting_is_not_shaded():
    web = "ok so basically the thing nobody tells you is that the ferry only runs when the tide allows it lol and half the time the captain just doesnt show up and you wait there for hours with nothing."
    data = _docx([_p(t) for t in HUMAN[:3]] + [_p(web, CHAT, UI, "00BB0002")] + [_p(HUMAN[3], rsid="00AA0003")])
    _rep, paras, plan = _plan(data)
    assert not plan.flags and not plan.soft_flags


def test_shading_is_not_stretched_to_the_document_index():
    """Index ~100 used to paint every human paragraph pale cyan so the shaded share equalled the index."""
    data = _docx([_p(t) for t in HUMAN[:3]] + [_p(t, CHAT, UI, "00BB0002") for t in AI] + [_p(HUMAN[3], rsid="00AA0003")])
    rep, paras, plan = _plan(data)
    assert rep.score_pooled > 90
    assert rep.score < rep.score_pooled   # mixed local evidence now limits the pooled saturation
    assert plan.soft_words == 0
    assert plan.flagged_words < plan.qualifying_words


def test_no_unit_is_cut_inside_a_sentence():
    data = _docx([_p(t) for t in HUMAN[:3]] + [_p(t, CHAT, UI, "00BB0002") for t in AI])
    rep, paras, plan = _plan(data)
    eng = ba.get_engine()
    for i, ranges in list(plan.flags.items()) + list(plan.soft_flags.items()):
        starts = {a for a, _ in eng.split_sentences(paras[i].text)}
        ends = {b for _, b in eng.split_sentences(paras[i].text)}
        for a, b in ranges:
            assert a in starts and b in ends


def test_document_wide_trace_does_not_localise():
    """Web Autospacing on every paragraph (a converted file) describes the file, not a pasted block."""
    data = _docx([_p(t, TNR, UI) for t in HUMAN * 3])
    prov = pv.read(data)
    assert all(p.sig is None and p.direct == 0.0 for p in prov.values())
    _rep, paras, plan = _plan(data)
    assert not plan.flags and not plan.soft_flags


def test_structural_slips_do_not_count_toward_ai():
    """Uncited references and unreferenced captions are human-plausible slips: findings stay, contribution is zero."""
    refs = ["[%d] Garcia, M. (2017). Unrelated topic number %d. Journal of Things, %d(1), 1-20." % (i, i, i) for i in range(1, 9)]
    items = [_p(t) for t in HUMAN * 2] + [_p("References")] + [_p(r) for r in refs]
    rep = ba.analyze_uploaded_bytes("s.docx", _docx(items))
    for k in ("cite_gap", "caption_orphan"):
        assert rep.features.get(k, (0, 0, 0))[2] <= 0.0


def test_prose_paragraph_with_heading_style_is_analysed_as_prose():
    import docx_clean
    class P:
        def __init__(self, t, h): self.text, self.is_heading = t, h
    prose = "This paragraph describes how the samples were collected from five sites and then processed in the laboratory over several weeks, and the results were then recorded in the field notebook and in the shared project database."
    ps = [P("3-1 Introduction", True), P(prose, True), P(prose, False)]
    assert docx_clean._restore_prose_headings(ps) == 1
    assert ps[0].is_heading and not ps[1].is_heading
