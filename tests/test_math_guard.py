# -*- coding: utf-8 -*-
"""Regression tests for the equation guard.  Run:  python -m pytest tests -q"""
import hashlib, io, os, sys, zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import backend_adapter as ba          # noqa: E402
import math_guard as mg               # noqa: E402

PROSE = [
    "Multiorgan toxicity is among the most lethal pathological changes following exposure to environmental chemicals, industrial toxicants, drug overdoses, and heavy metals (Hassan et al., 2023). In contrast to single-organ damage, multiorgan toxicity involves functional impairment of two or more vital organs, namely the lungs, liver, heart, and kidneys.",
    "As the primary organ for xenobiotic biotransformation and detoxification, the liver is particularly vulnerable to toxic insult. Hepatocellular damage is characterized by elevated serum alanine aminotransferase, aspartate aminotransferase, alkaline phosphatase, and bilirubin. Chronic hepatic injury promotes inflammatory cell infiltration, mitochondrial dysfunction, fibrosis, oxidative stress, and apoptosis.",
    "The heart, being an energy-intensive tissue rich in mitochondria, is highly susceptible to aerobic metabolic disruption. Elevated ROS production disrupts mitochondrial respiration, impairs ATP synthesis, and triggers lipid peroxidation and apoptosis, reflected by elevated serum levels of creatine kinase-MB and lactate dehydrogenase (Yurisaldi et al., 2023).",
    "Metformin, a biguanide derivative prescribed for type 2 diabetes mellitus, exhibits pleiotropic biological functions independent of glycemic control. It protects tissues including the liver, kidney, heart, and lungs by enhancing antioxidant and anti-inflammatory responses, preserving mitochondrial integrity, and regulating AMPK signaling (Renu et al., 2022).",
    "Curcumin, the primary polyphenolic compound in turmeric, is widely investigated for its antioxidant, anti-inflammatory, anti-fibrotic, and anti-apoptotic properties (Mojtabavi et al., 2023). Curcumin scavenges ROS, enhances endogenous antioxidants via the Nrf2 pathway, inhibits pro-inflammatory NF-kappaB signaling, and suppresses fibrogenesis.",
    "While the individual benefits of metformin and curcumin are well-established, their combined co-administration requires rigorous evaluation to assess additive or combined protective efficacy across multiple organs. Therefore, this study investigates the effects of co-administering metformin and curcumin on CCl4-induced multiorgan damage in rats.",
] * 3
EQS = [
    r"$$\frac{\partial u}{\partial t} = \alpha \nabla^2 u + f(x,t)$$",
    r"\[ \sum_{i=1}^{n} (x_i - \bar{x})^2 = \sigma^2 n \]",
    r"\begin{equation} E = mc^2 \end{equation}",
    "$$\n\\int_0^1 f(x)\\,dx = 1\n$$",
]


def _interleave(with_eq=True):
    out = []
    for i, p in enumerate(PROSE):
        out.append(p)
        if with_eq:
            out.append(EQS[i % len(EQS)])
    return "\n\n".join(out)


def test_engine_file_untouched():
    assert hashlib.sha256((ROOT / "AI_Text_Inspector.pyw").read_bytes()).hexdigest() == ba.ENGINE_SHA256


@pytest.mark.parametrize("line", [
    r"$$\frac{a}{b}$$", r"\[ x^2 + y^2 = z^2 \]", r"\begin{align} a &= b \end{align}", "E = mc^2", "x_i = a^2 + b^2 (3)",
])
def test_formula_only_paragraph_dropped(line):
    assert mg.sanitize_paragraph(line)[0] is None


@pytest.mark.parametrize("line", [
    "The price is $5 and $10 per unit, which is fair to everybody involved.",
    "The dose was 200 mg/kg/day for 6 weeks (n = 10/group, p < 0.05) in all groups.",
    "Values are mean ± SD; significance was set at p < 0.05 throughout the experiment.",
    "The study¹ compared field observations with previous research² in the local district.",
    "تم توثيق الملاحظات¹ ومراجعة النتائج² في السجل الأصلي للتجربة.",
    "The trial enrolled participants (n=10/group, p<0.05) during the summer season.",
    "The sample contained H₂O and CO₂ at the station during the experiment.",
    "The treatment used Ca(OH)₂ and NH₄⁺ with O₂ in the laboratory experiment.",
    "The temperature was 25 ℃ and the volume was 10 m³ during the experiment.",
    "The office uses NEBRAS℠ and record № 12 in official correspondence.",
])
def test_prose_never_touched(line):
    new, pmap, st = mg.sanitize_paragraph(line)
    assert new == line and st.found == 0


def test_inline_math_removed_prose_kept():
    new, pmap, st = mg.sanitize_paragraph(r"where $x$ is the mean and $y$ is the variance of the sample.")
    assert new == "where § is the mean and § is the variance of the sample." and st.inline == 2  # placeholder keeps sentence shape


def test_real_unicode_math_still_excluded():
    text = "The researcher used x² and x₁ in the fitted model over the study period."
    new, pmap, stats = mg.sanitize_paragraph(text)
    assert new == "The researcher used § and § in the fitted model over the study period."
    assert stats.inline == 2
    assert [text[pmap[i]] for i, ch in enumerate(new) if ch != mg.PLACEHOLDER] == [ch for ch in new if ch != mg.PLACEHOLDER]


@pytest.mark.parametrize("text", [
    "The field worker recorded each observation in the original laboratory notebook.",
    "The study reported n=10/group and p<0.05 in the original laboratory notebook.",
    "The study¹ compared the research² with the original laboratory notebook.",
])
def test_math_font_does_not_remove_ordinary_prose(text):
    from types import SimpleNamespace
    para = SimpleNamespace(text=text, runs=[(0, len(text), "Cambria Math", 12, False, False)])
    clean, mapping = mg.sanitize_docx_paras([para])
    assert clean[0].text == text
    assert mapping.stats.found == 0
    assert not mapping.active


def test_footnote_in_separate_math_font_run_is_kept():
    from types import SimpleNamespace
    text = "The research² was compared with the original laboratory notebook."
    start = text.index("²")
    para = SimpleNamespace(text=text, runs=[(start, start + 1, "Cambria Math", 12, False, False)])
    clean, mapping = mg.sanitize_docx_paras([para])
    assert clean[0].text == text and not mapping.active


def test_math_font_removes_formula_without_erasing_surrounding_prose():
    from types import SimpleNamespace
    text = "The field worker calculated x² before checking the original laboratory notebook."
    para = SimpleNamespace(text=text, runs=[(0, len(text), "Cambria Math", 12, False, False)])
    clean, mapping = mg.sanitize_docx_paras([para])
    assert clean[0].text == text.replace("x²", mg.PLACEHOLDER)
    assert mapping.stats.inline == 1
    assert mapping.active


def test_multiline_blocks():
    t = "Intro paragraph with plenty of real words in it.\n\\begin{align}\na &= b \\\\\nc &= d\n\\end{align}\nA closing sentence follows the block here.\n$$\nx=y\n$$\nDone, finally."
    clean, mm = mg.sanitize_text(t)
    assert clean.count("\n") == 2 and "align" not in clean and mm.orig_index == [0, 5, 9]


def test_offset_map_points_back_to_original():
    t = r"Let $\alpha$ be small and the mean stays constant over the period."
    new, pmap, _ = mg.sanitize_paragraph(t)
    mm = mg.MathMap(orig_texts=[t], orig_index=[0], pos_maps=[pmap])
    a = new.index("small")
    j, a2, b2 = mm.to_original(0, a, a + 5)
    assert t[a2:b2] == "small"


def test_score_unchanged_by_equations_text():
    base = ba.analyze_pasted(_interleave(False)).score
    guarded = ba.analyze_pasted(_interleave(True)).score
    raw = ba.get_engine().analyze_text(_interleave(True)).score
    assert abs(base - guarded) < 1.0
    assert abs(base - raw) > 5          # proves the problem existed without the guard


def test_no_math_keeps_engine_input_and_pooled_score():
    t = _interleave(False)
    guarded = ba.analyze_pasted(t)
    raw = ba.get_engine().analyze_text(t)
    assert guarded.texts == raw.texts
    assert guarded.score_pooled == raw.score
    assert guarded.score <= guarded.score_pooled


def _docx_with(mode):
    from lxml import etree
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
    paras = "".join('<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % p for p in PROSE)
    root = etree.fromstring('<w:document xmlns:w="%s" xmlns:m="%s"><w:body>%s</w:body></w:document>' % (W, M, paras))
    body = root[0]
    for p in list(body):
        if mode == "omml":
            body_eq = etree.fromstring('<w:p xmlns:w="%s" xmlns:m="%s"><m:oMathPara><m:oMath><m:r><m:t>E=mc^2</m:t></m:r></m:oMath></m:oMathPara></w:p>' % (W, M))
        else:
            body_eq = etree.fromstring('<w:p xmlns:w="%s"><w:r><w:t>$$\\frac{a}{b} = \\sum_i x_i$$</w:t></w:r></w:p>' % W)
        p.addnext(body_eq)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True))
    return out.getvalue()


@pytest.mark.parametrize("mode", ["omml", "latex"])
def test_docx_equations_do_not_move_score(mode):
    clean = ba.analyze_uploaded_bytes("a.docx", _docx_plain())
    with_eq = ba.analyze_uploaded_bytes("a.docx", _docx_with(mode))
    assert abs(clean.score - with_eq.score) < 1.0 and with_eq.math.active


def _docx_plain():
    from lxml import etree
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    paras = "".join('<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % p for p in PROSE)
    root = etree.fromstring('<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W, paras))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("word/document.xml", etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True))
    return out.getvalue()


@pytest.mark.skipif(not (__import__("shutil").which("soffice") or __import__("shutil").which("libreoffice")),
                    reason="LibreOffice needed for PDF conversion")
def test_pdf_report_builds_with_equations():
    pymupdf = pytest.importorskip("pymupdf")
    import nebras_report as nr
    t = _interleave(True)
    rep = ba.analyze_pasted(t)
    pdf = nr.build_report_pdf(rep, text=t)
    assert len(pymupdf.open(stream=pdf, filetype="pdf")) >= 2
