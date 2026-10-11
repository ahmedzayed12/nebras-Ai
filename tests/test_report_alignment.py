# -*- coding: utf-8 -*-
"""Regression: the PDF report must align its shading plan with the SOURCE document paragraphs even
when non-prose paragraphs (title/author block, table cells, code) were removed before analysis and
NO equation was present.  This used to raise
'تعذّر مطابقة فقرات Word مع نتيجة التحليل' because the plan stayed indexed on the shortened analysed
list while the report builder walked the full original Word XML.
Run:  python -m pytest tests -q
"""
import io
import sys
import zipfile
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import backend_adapter as ba          # noqa: E402
import nebras_report as nr            # noqa: E402

PROSE = [
    "Multiorgan toxicity is among the most lethal pathological changes following exposure to environmental chemicals, industrial toxicants, drug overdoses, and heavy metals (Hassan et al., 2023). In contrast to single-organ damage, multiorgan toxicity involves functional impairment of two or more vital organs, namely the lungs, liver, heart, and kidneys.",
    "As the primary organ for xenobiotic biotransformation and detoxification, the liver is particularly vulnerable to toxic insult. Hepatocellular damage is characterized by elevated serum alanine aminotransferase, aspartate aminotransferase, alkaline phosphatase, and bilirubin.",
    "The heart, being an energy-intensive tissue rich in mitochondria, is highly susceptible to aerobic metabolic disruption. Elevated ROS production disrupts mitochondrial respiration and impairs ATP synthesis.",
    "Metformin, a biguanide derivative prescribed for type 2 diabetes mellitus, exhibits pleiotropic biological functions independent of glycemic control.",
    "Curcumin, the primary polyphenolic compound in turmeric, is widely investigated for its antioxidant and anti-inflammatory properties (Mojtabavi et al., 2023).",
    "While the individual benefits of metformin and curcumin are well-established, their combined co-administration requires rigorous evaluation to assess additive protective efficacy across multiple organs.",
] * 3

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def _p(text):
    return '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % text


def _docx(body_paras):
    root = etree.fromstring('<w:document xmlns:w="%s" xmlns:m="%s"><w:body>%s</w:body></w:document>'
                            % (W, M, "".join(body_paras)))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True))
    return out.getvalue()


def _shade_ok(data):
    """Reproduce exactly what build_report_pdf selects before DOCX->PDF conversion (no LibreOffice needed)."""
    rep = ba.analyze_uploaded_bytes("a.docx", data)
    paras, _kind, mm = nr._paras_for(rep, "a.docx", data, None)
    plan = nr.balance_to_engine_index(rep, paras, nr.plan_highlights(rep, paras))
    orig_texts, shade = nr._source_shading(rep, paras, plan, mm)
    body, _removed = nr.apply_highlights(data, shade, orig_texts)
    return rep, paras, orig_texts, body


def test_docx_front_matter_without_equations_aligns():
    data = _docx([_p("Protective Effects of Metformin and Curcumin"),
                  _p("Jane Doe, Department of Mathematics, University X; jane@x.edu"),
                  _p("Abstract")] + [_p(x) for x in PROSE])
    rep, paras, orig_texts, body = _shade_ok(data)
    assert not rep.math.active                 # no equations at all
    assert rep.docx_clean.removed >= 1         # non-prose paragraphs were removed before analysis
    assert len(orig_texts) > len(paras)        # plan must target the FULL source, not the analysed list
    assert body


def test_docx_table_cells_without_equations_aligns():
    table = '<w:tbl><w:tr><w:tc>%s</w:tc></w:tr></w:tbl>' % _p("0.91")
    data = _docx([_p(x) for x in PROSE] + [table])
    rep, paras, orig_texts, body = _shade_ok(data)
    assert len(orig_texts) > len(paras) and body


def test_docx_equations_still_align():
    eq = ('<w:p xmlns:m="%s"><m:oMathPara><m:oMath><m:r><m:t>E=mc^2</m:t></m:r></m:oMath></m:oMathPara></w:p>' % M)
    data = _docx([_p(x) for x in PROSE[:3]] + [eq] + [_p(x) for x in PROSE[3:]])
    rep, paras, orig_texts, body = _shade_ok(data)
    assert rep.math.active and body


def test_pasted_text_still_aligns():
    rep = ba.analyze_pasted("\n\n".join(PROSE))
    plan = nr.compute_plan(rep, text="\n\n".join(PROSE))
    assert plan.qualifying_words > 0
