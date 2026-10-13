# -*- coding: utf-8 -*-
"""Layers added after v2.1 (academic prose, paste artefacts, container cap, format forensics).
Synthetic documents only: no real user file is used as a reference."""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import academic_signals as acad   # noqa: E402
import backend_adapter as b       # noqa: E402
import format_forensics as ffm    # noqa: E402

LLM_PARA = ("This study adopts a comprehensive framework to examine how institutional, organizational, and cultural "
            "factors shape the implementation of sustainable practices. The findings highlight the crucial interplay "
            "between leadership, collaboration, and accountability, underscoring the need for holistic and "
            "context-sensitive strategies. Additionally, the analysis provides valuable insights for policymakers, "
            "practitioners, and researchers seeking to enhance resilience, foster innovation, and ensure equitable outcomes.")
HUMAN_PARA = ("I spent most of the spring of 2016 in two villages north of the river, and what struck me first was "
              "how little the elders cared about the questionnaire. We sat under the mango tree; they talked about "
              "the 1998 floods, about a cousin who went to Accra, about prices. My notes from 14 April are a mess. "
              "Still, three things came up again and again: land, school fees and the new road.")


def _docx(paras, run_props="", ppr=""):
    body = "".join('<w:p><w:pPr>%s</w:pPr><w:r><w:rPr>%s</w:rPr><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
                   % (ppr, run_props, p) for p in paras)
    doc = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main"><w:body>%s<w:sectPr/></w:body></w:document>' % body)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def test_academic_layer_fires_on_llm_prose_only():
    llm = acad.analyze([LLM_PARA] * 30)
    hum = acad.analyze([HUMAN_PARA] * 30)
    assert llm.confidence > 0 and llm.fired >= 3 and llm.score > 70
    assert hum.confidence == 0
    assert acad.fuse(20.0, hum) == 20.0          # never lowers / never moves without convergence
    assert acad.fuse(20.0, llm) > 20.0


def test_paste_artifacts_detected():
    chat = '<w:rFonts w:ascii="Söhne" w:hAnsi="Söhne"/><w:color w:val="0D0D0D"/>'
    pa = ffm.paste_artifacts(_docx([HUMAN_PARA] * 10, run_props=chat))
    assert pa.chat_color == 1.0 and pa.chat_font == 1.0 and pa.total >= b.PASTE_MIN
    clean = ffm.paste_artifacts(_docx([HUMAN_PARA] * 10, run_props='<w:rFonts w:ascii="Times New Roman"/>'))
    assert clean.total == 0.0


def test_uniform_typed_indent_is_mechanical():
    paras = ["        " + HUMAN_PARA] * 10
    pa = ffm.paste_artifacts(_docx(paras))
    assert pa.space_indent_uniform >= 0.9


def test_docx_route_end_to_end():
    human = b.analyze_uploaded_bytes("h.docx", _docx([HUMAN_PARA] * 12))
    pasted = b.analyze_uploaded_bytes("p.docx", _docx([HUMAN_PARA] * 12,
                                      run_props='<w:rFonts w:ascii="Söhne" w:hAnsi="Söhne"/><w:color w:val="0D0D0D"/>'))
    assert pasted.score > human.score
    llm = b.analyze_uploaded_bytes("l.docx", _docx([LLM_PARA] * 30))
    assert llm.score >= 65


def test_container_cap_needs_independent_evidence():
    class R:  # engine report stub
        features = {"meta_human": (1, -2.2, -2.2), "fmt_incons": (1, -1.8, -1.8), "mixed_conv": (1, -1.2, -1.2)}
        z, confidence, score = -3.0, 1.0, 4.7
    r = R()
    assert b._recalibrate_container(r) and abs(r.z - (-3.0 + 5.2 - 1.0)) < 1e-9


# ---------------------------------------------------------------- "a human always slips" (friction.py), synthetic only
HUMAN_SLIPPY = ("We  measured the soil twice , once in May and again in June.The second sample was wetter than the "
                "first one and and we think the rain explains it ( at least partly ). Next year we will add a third site, "
                "probably near the river, because the farmers there asked us to.")
CLEAN_LONG = ("The second sample was wetter than the first one, and the rain probably explains the difference. "
              "Next year a third site will be added near the river, because the farmers there requested it. "
              "Soil moisture was recorded twice, once in May and again in June, at every plot in the study area.")


HUMAN_CLEAN_VARIED = [
    "I spent the first week of March in Tamale, mostly waiting for permits. The district office was closed on Monday, so I sat in the market and counted bicycles, which turned out to be more useful than I expected: 214 bicycles passed the corner in two hours, and only nine of them carried passengers.",
    "My supervisor did not like the first questionnaire. She said it was too long, and she was right; the pilot took forty minutes per household. We cut it to eighteen questions and dropped the section on remittances altogether, which I still regret.",
    "Prices were the main complaint. A bag of maize cost 310 cedis in April and 420 in June. Several women told me they now buy in smaller quantities from the same trader, paying more per kilo, because they cannot raise the full amount at once.",
    "The second village was different. There is a borehole, a clinic that opens twice a week, and a teacher who has lived there for eleven years. People talked about the teacher more than about the clinic. Nobody mentioned the borehole until I asked.",
    "Not everything worked. Two recorders failed in the heat, and I lost most of the interviews from 12 May. I reconstructed what I could from my notebook that evening, but the quotations in Chapter 5 from that day are paraphrases, not transcripts.",
    "Why did the cooperative collapse? The official answer is mismanagement. The farmers I spoke to blamed the 2019 floods, a disputed loan, and a chairman who moved to Kumasi. I suspect all three mattered, in that order.",
    "On the last day the chief invited us to his compound. He asked what the study was for and whether it would bring a road. I said honestly that it would not. He laughed, gave us groundnuts, and asked us to send him a copy anyway.",
    "Looking back, the sample is small and the seasons are uneven. I visited the northern sites in the dry season and the southern ones after the rains began. Any comparison between them in the following chapters should be read with that in mind.",
]


def test_clean_human_wording_is_never_lifted_by_cleanliness_alone():
    clean_human = b.analyze_uploaded_bytes("c.docx", _docx(HUMAN_CLEAN_VARIED * 4))   # ~1900 words, no slip, human wording
    assert clean_human.friction is None or clean_human.friction.floor is None
    assert clean_human.score < 52
    human = b.analyze_uploaded_bytes("h.docx", _docx([HUMAN_SLIPPY] * 40))
    assert human.friction is None or human.friction.floor is None
    short = b.analyze_uploaded_bytes("s.docx", _docx([CLEAN_LONG] * 5))
    assert short.friction is None or short.friction.floor is None


def test_clean_llm_wording_stays_high_and_drops_when_rewritten():
    full = b.analyze_uploaded_bytes("a.docx", _docx([LLM_PARA] * 30))
    half = b.analyze_uploaded_bytes("r.docx", _docx([LLM_PARA] * 15 + [HUMAN_SLIPPY] * 15))
    assert full.score >= 65 and half.score < full.score


def test_edit_history_reset_moves_the_score_only_slightly():
    import re as _re
    plain = _docx([LLM_PARA] * 20 + [HUMAN_SLIPPY] * 20)
    z = zipfile.ZipFile(io.BytesIO(plain))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for it in z.infolist():
            out.writestr(it, z.read(it.filename))
        out.writestr("docProps/app.xml", '<?xml version="1.0"?><Properties xmlns="http://schemas.openxmlformats.org/'
                     'officeDocument/2006/extended-properties"><TotalTime>2400</TotalTime><Application>Microsoft Office Word'
                     '</Application></Properties>')
        out.writestr("docProps/core.xml", '<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/'
                     'package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Student'
                     '</dc:creator><cp:lastModifiedBy>Student</cp:lastModifiedBy><cp:revision>14</cp:revision></cp:coreProperties>')
        out.writestr("word/comments.xml", '<?xml version="1.0"?><w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    a = b.analyze_uploaded_bytes("a.docx", plain).score
    h = b.analyze_uploaded_bytes("h.docx", buf.getvalue()).score
    assert abs(a - h) <= 8.0


def test_friction_never_applies_to_pasted_text_or_reports():
    r = b.analyze_pasted("\n".join([CLEAN_LONG] * 40))
    assert getattr(r, "friction", None) is None
    d = b.analyze_uploaded_bytes("c.docx", _docx([CLEAN_LONG] * 40))
    assert not any("الإنسان يخطئ" in x for x in d.reasons)                         # score only, never shown


def test_friction_floor_is_continuous_no_fixed_steps():
    import friction
    vals = [friction.floor_for(r / 100.0) for r in range(0, 200)]
    assert all(a > b for a, b in zip(vals, vals[1:]))          # every change in slip rate changes the result
    assert friction.floor_for(2.5) == 0.0


def test_pdf_extraction_artifacts_are_neutral():
    class R:   # engine report stub: "human slips" and quote mixing that came from PDF extraction
        features = {"err_density": (0.5, -2.4, -1.4), "clean": (0.1, 0.25, 0.03), "mixed_conv": (1, -1.2, -1.2),
                    "fmt_incons": (0.2, -1.8, -0.36), "contrast": (0.3, 1.5, 0.45)}
        z, confidence, score = -2.0, 1.0, 11.9
    r = R()
    b._pdf_neutralize(r)
    assert all(r.features[k][2] == 0 for k in b.PDF_EXTRACTION_FEATURES)
    assert r.features["contrast"][2] == 0.45 and abs(r.z - (-2.0 + 1.4 - 0.03 + 1.2 + 0.36)) < 1e-9


def test_academic_layer_has_no_cliff():
    import academic_signals as acad
    two = acad.analyze([LLM_PARA] * 30)
    assert two.confidence > 0
    assert acad.analyze([HUMAN_PARA] * 30).confidence == 0
