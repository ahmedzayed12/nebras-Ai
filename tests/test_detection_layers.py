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


def test_friction_floor_only_for_long_clean_word_files():
    long_clean = b.analyze_uploaded_bytes("c.docx", _docx([CLEAN_LONG] * 40))       # ~2000 words, no slip
    assert long_clean.friction is not None and long_clean.friction.floor is not None and long_clean.score >= 58
    human = b.analyze_uploaded_bytes("h.docx", _docx([HUMAN_SLIPPY] * 40))          # same length, typical slips
    assert human.friction is not None and human.friction.floor is None
    short = b.analyze_uploaded_bytes("s.docx", _docx([CLEAN_LONG] * 5))             # short: rule not applied
    assert short.friction is None or short.friction.floor is None


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
