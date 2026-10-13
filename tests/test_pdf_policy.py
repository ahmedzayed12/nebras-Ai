# -*- coding: utf-8 -*-
"""PDF = final text only. Policy tests that do not need pymupdf (they feed extracted text to the same entry point)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backend_adapter as b   # noqa: E402
from tests.test_detection_layers import LLM_PARA, HUMAN_PARA   # noqa: E402

BODY = ("The committee reviewed the earlier proposals (Smith, 2019) and the later revisions (Jones et al., 2021) before "
        "deciding. Costs rose in the second year, as reported in (Brown, 2020), while attendance fell by about 12 per cent. ")
REFS_OK = ["Smith, J. (2019). Public budgets and local choice. Journal of Policy Studies, 14(2), 11-30.",
           "Jones, A., Lee, K., & Park, S. (2021). Attendance after reform. Education Review, 9(1), 55-71.",
           "Brown, T. (2020). Costs of small programmes. Economic Notes, 33(4), 201-219."]


def _doc(refs):
    paras = [BODY * 3 for _ in range(14)] + ["References"] + refs
    return "\n".join(paras)


def _fragment(refs):
    out = []
    for r in refs:                       # a PDF line break inside every entry; the year ends up on the second line
        cut = r.index("(")
        out += [r[:cut].strip(), r[cut:]]
    return out


def test_pdf_verdict_is_text_only_and_never_categorical():
    r = b.analyze_text_guarded("\n".join([LLM_PARA] * 30), from_pdf=True)
    assert r.score >= 65
    assert "PDF" in r.verdict and "مولَّد بالذكاء الاصطناعي على الأرجح" not in r.verdict and "يميل بقوة" not in r.verdict
    h = b.analyze_text_guarded("\n".join([HUMAN_PARA] * 12), from_pdf=True)
    assert "PDF" in h.verdict


def test_word_and_text_verdicts_are_unchanged_by_the_pdf_policy():
    r = b.analyze_text_guarded("\n".join([LLM_PARA] * 30))
    assert "PDF" not in r.verdict and r.score >= 65


def test_pdf_score_is_not_changed_by_the_wording_policy():
    pdf = b.analyze_text_guarded("\n".join([LLM_PARA] * 30), from_pdf=True)
    txt = b.analyze_text_guarded("\n".join([LLM_PARA] * 30))
    assert abs(pdf.score - txt.score) < 8           # same text, same evidence channel => same answer within a few points


def test_fragmented_reference_list_does_not_count_as_a_citation_gap():
    refs = REFS_OK * 3
    whole = b.analyze_text_guarded(_doc(refs), from_pdf=True)
    frag = b.analyze_text_guarded(_doc(_fragment(refs)), from_pdf=True)
    gap_whole = whole.features.get("cite_gap", (0, 0, 0))[2]
    gap_frag = frag.features.get("cite_gap", (0, 0, 0))[2]
    assert gap_frag <= gap_whole + 1e-9
    assert gap_frag == 0.0


def test_real_citation_gap_survives_in_pdf_when_entries_are_whole():
    fabricated = ["Garcia, M. (2017). Unrelated topic number %d. Journal of Things, %d(1), 1-20." % (i, i) for i in range(1, 9)]
    r = b.analyze_text_guarded(_doc(fabricated), from_pdf=True)
    assert r.features["cite_gap"][2] > 0           # whole entries with years that nobody cites: still evidence
