# -*- coding: utf-8 -*-
"""v2.8: structure that every thesis has by design must not count as human OR as AI evidence."""
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import academic_signals as acad   # noqa: E402
import backend_adapter as b       # noqa: E402
from tests.test_detection_layers import HUMAN_PARA   # noqa: E402

BODY = [
    "Samples were collected from five farms during the dry season and transported to the laboratory within two hours of harvest.",
    "Each fruit was washed, surface-sterilised with ethanol and cut so that a small piece of tissue could be placed on the plate.",
    "The plates were incubated at room temperature and examined every day for a week, because several isolates grew slowly at first.",
    "We kept notes on colony colour, and when two colonies looked alike we streaked them again on a fresh plate to be sure.",
    "Honestly the third farm surprised me; nearly every fruit there carried one dominant fungus, which I had not expected at all.",
    "A few plates were contaminated by bacteria and were discarded, so the totals in the table are slightly lower than planned.",
    "Identification followed the usual keys, but where the keys disagreed I compared the microscopic fields with published figures.",
    "The results below describe what was seen on the plates and do not claim that any isolate caused the rot by itself.",
]


PARAS = [BODY[i] + " " + BODY[i + 1] for i in range(0, 8, 2)]      # 4 paragraphs of >= 25 words (real prose, not "front")


def _docx(rows):
    """rows: (text, jc, size_half_points)"""
    ps = []
    for row in rows:
        t, jc, sz = row[:3]
        style = '<w:pStyle w:val="Heading1"/>' if len(row) > 3 and row[3] else ""
        t = t.replace("&", "&amp;").replace("<", "&lt;")
        ps.append('<w:p><w:pPr>%s<w:jc w:val="%s"/><w:spacing w:after="120"/></w:pPr><w:r><w:rPr><w:sz w:val="%d"/></w:rPr>'
                  '<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (style, jc, sz, t))
    doc = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s<w:sectPr/></w:body></w:document>' % "".join(ps))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/_rels/document.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", '<?xml version="1.0"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style>'
                   '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style></w:styles>')
    return buf.getvalue()


def _fmt(report):
    return report.features.get("fmt_incons", (0, 0, 0))[2]


def _with_structure():
    rows = [("CHAPTER THREE", "center", 32, True), ("MATERIALS AND METHODS", "center", 28), ("Fungi Associated with Postharvest Tomato Fruit Rot", "center", 26)]
    for k, t in enumerate(PARAS * 3):
        rows.append((t, "both", 24))
        if k % 4 == 1:
            rows.append(("Table (3-%d). Distribution of the fruit samples among the five study sites." % (k // 4 + 1), "center", 21))
        if k % 4 == 3:
            rows.append(("Occurrence (%) = (fruits yielding the genus ÷ fruits examined) × 100     (3-1)", "center", 24))
    return rows


class _P:
    """The few attributes docx_clean reads from an engine paragraph."""
    def __init__(self, text, heading=False, table=False, drawing=False):
        self.text, self.is_heading, self.in_table, self.has_drawing = text, heading, table, drawing
        self.runs, self.align, self.after, self.before, self.line, self.first = [], "both", 0, 0, 0, 0


def test_chapter_numbered_captions_and_typed_formulas_are_recognised():
    import docx_clean as dc
    for cap in ("Table (3-1). Distribution of tomato fruit samples", "Figure (3-2). Isolation plate", "Fig. 2.1 Microscopic field",
                "Table A1. Primers", "Figure 3-4: Representative plates", "جدول (3-1) توزيع العينات"):
        assert dc._STRUCTURAL.match(cap), cap
    assert not dc._STRUCTURAL.match("The table below shows the distribution of the fruit.")
    assert dc._EQ_LINE.search("Occurrence (%) = (fruits yielding the genus ÷ fruits examined) × 100     (3-1)")
    assert not dc._EQ_LINE.search("The ratio was 3 = (1 + 2), as reported earlier in the text.")


def test_title_lines_and_cover_blanks_before_the_first_paragraph_are_front_matter():
    import docx_clean as dc
    prose = " ".join(["word"] * 40) + "."
    paras = [_P("CHAPTER THREE", heading=True), _P("MATERIALS AND METHODS, RESULTS AND DISCUSSION"), _P(""),
             _P("Fungi Associated with Postharvest Tomato Fruit Rot"), _P(prose), _P(prose)]
    ids = dc._front_matter_ids(paras)
    assert ids == {1, 2, 3}                    # the heading stays a heading; the sentence-length paragraphs stay prose
    assert dc._front_matter_ids([_P(prose), _P("Short unfinished line"), _P(prose)]) == set()


def test_real_inconsistency_in_body_prose_still_counts():
    rows = [("CHAPTER THREE", "center", 32, True)]
    for k, t in enumerate(PARAS * 3):
        rows.append((t, "center" if k % 3 == 0 else "both", 24 if k % 2 else 30))
    r = b.analyze_uploaded_bytes("d.docx", _docx(rows))
    assert _fmt(r) < -0.5


def test_taxonomic_abbreviations_are_not_lowercase_sentence_starts():
    txt = BODY[0] + " Alternaria spp. and Fusarium spp. were recovered most often, whereas Rhizopus sp. appeared on a single farm only."
    r = b.analyze_uploaded_bytes("e.docx", _docx([(t, "both", 24) for t in PARAS * 3] + [(txt, "both", 24)] * 3))
    assert not any(f.kind == "lower_start" for f in r.findings)
    # a genuine lowercase start after a full stop is still a slip
    r2 = b.analyze_uploaded_bytes("f.docx", _docx([(t, "both", 24) for t in PARAS * 3] + [("The test ended early. and then we left the room.", "both", 24)] * 3))
    assert any(f.kind == "lower_start" for f in r2.findings)


def test_named_lists_are_not_rhetorical_triads_but_abstract_ones_are():
    assert acad.technical_list("Rhizopus, Alternaria and Fusarium")
    assert acad.technical_list("Nepal, Sri Lanka, and Yemen")
    assert not acad.technical_list("leadership, collaboration, and accountability")
    assert not acad.technical_list("transparent, auditable, and flexible")
    named = [("Fungi such as Rhizopus, Alternaria and Fusarium were recovered from the fruit of the five farms we visited. Each plate was read twice by the same person, and doubtful colonies were streaked again before any name was written down.", "both", 24)] * 12
    plain = [("The framework values leadership, collaboration and accountability in every one of the five farms we visited. Each plate was read twice by the same person, and doubtful colonies were streaked again before any name was written down.", "both", 24)] * 12
    rn = b.analyze_uploaded_bytes("g.docx", _docx(named))
    rp = b.analyze_uploaded_bytes("h.docx", _docx(plain))
    assert rn.features["tricolon"][2] < rp.features["tricolon"][2]
    assert getattr(rn, "lists_removed", 0) > 0 and not getattr(rp, "lists_removed", 0)


def test_cover_page_spacing_is_not_counted_as_manual_spacing():
    rows = [("", "center", 24)] * 6 + [("A Study of Fruit Rot", "center", 32)] + [("", "center", 24)] * 4
    rows += [("Abstract", "left", 24)] + [(t, "both", 24) for t in PARAS * 3]
    r = b.analyze_uploaded_bytes("i.docx", _docx(rows))
    assert not any(f.kind == "fmt_empty_para" for f in r.findings)


def _docx_with_images(images, referenced=True):
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        rels = "".join('<Relationship Id="rId%d" Type="image" Target="media/%s"/>' % (i + 10, n) for i, (n, _) in enumerate(images))
        z.writestr("word/_rels/document.xml.rels", "<Relationships>%s</Relationships>" % rels)
        used = "".join('<a:blip r:embed="rId%d"/>' % (i + 10) for i in range(len(images))) if referenced else ""
        z.writestr("word/document.xml", "<w:document><w:body><w:p>%s</w:p></w:body></w:document>" % used)
        for n, data in images:
            z.writestr("word/media/" + n, data)
    return buf.getvalue()


def test_photographs_count_only_when_placed_in_body_and_real_jpeg():
    jpg = b"\xff\xd8\xff" + bytes(range(256)) * 200
    png = b"\x89PNG" + bytes(range(256)) * 200
    assert b.photo_count(_docx_with_images([("a.jpg", jpg), ("b.jpg", jpg + b"x"), ("c.png", png)])) == 2
    assert b.photo_count(_docx_with_images([("a.jpg", jpg)], referenced=False)) == 0      # leftover media is not content
    assert b.photo_count(_docx_with_images([("a.jpg", jpg), ("b.jpg", jpg)])) == 1        # the same picture twice is one photo
    assert b.photo_count(_docx_with_images([("a.jpg", b"\xff\xd8\xff" + b"0" * 100)])) == 0   # tiny icons / bullets


def test_photo_evidence_is_small_bounded_and_withheld_for_generators():
    class R: pass
    r = R(); r.photos_n = 16; r.z = 0.0; r.features = {}; r.confidence = 1.0; r.score = 50.0; r.meta = {}
    d = b._photo_evidence(r)
    assert b.PHOTO_CAP <= d < 0 and abs(d) <= 0.4
    g = R(); g.photos_n = 16; g.z = 0.0; g.features = {}; g.confidence = 1.0; g.score = 50.0; g.meta = {"creator": "python-docx"}
    assert b._photo_evidence(g) == 0.0
