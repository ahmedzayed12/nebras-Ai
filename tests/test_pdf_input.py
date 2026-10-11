# -*- coding: utf-8 -*-
"""PDF extraction must not turn viewer chrome / table cells into fake 'human typing errors'."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import pymupdf
import pdf_input as P


def _pdf(pages):
    doc = pymupdf.open()
    for lines in pages:
        pg = doc.new_page()
        y = 60
        for txt, size in lines:
            pg.insert_text((72, y), txt, fontsize=size)
            y += size + 6
    return doc.tobytes()


def test_running_header_and_turnitin_chrome_removed():
    pages = []
    for i in range(1, 6):
        pages.append([(f"Page {i} of 6 - AI Writing Submission", 8), ("trn:oid:::4:123456", 8),
                      ("The proposed architecture evaluates the channel under realistic conditions.", 11),
                      ("Results show that the adaptive scheme improves throughput over the baseline.", 11)])
    text = P.extract_pdf(_pdf(pages)).text
    assert "trn:oid" not in text and "AI Writing Submission" not in text
    assert "adaptive scheme improves throughput" in text


def test_wrapped_lines_become_one_paragraph():
    text = P.extract_pdf(_pdf([[("This sentence is wrapped over", 11), ("two visual lines and ends here.", 11)]])).text
    assert "\n" not in text and "wrapped over two visual lines" in text


def test_tech_layer_never_lowers_and_stays_quiet_on_plain_prose():
    import tech_signals as ts
    plain = ["The function returns a list. If the list is empty the caller must decide what to do next. " * 8] * 40
    layer = ts.analyze(plain)
    assert ts.fuse(30.0, layer) == 30.0
    assert ts.fuse(80.0, ts.TechLayer(10.0, 1.0, True, 5000, [], [])) == 80.0
