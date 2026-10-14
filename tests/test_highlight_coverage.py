"""Coverage reconciliation needs local evidence and whole units, including pale ones."""
import io
import zipfile
from types import SimpleNamespace as NS

from lxml import etree
import pytest

import backend_adapter as ba
import nebras_report as nr
from tests.test_targeted_highlights import _finding, _report


def test_partial_wording_shades_its_complete_sentence_only():
    text = ("This holistic approach considers regional interventions across institutions and communities throughout the area during the implementation of the proposed policy. "
            "We measured eighteen millimetres on Tuesday and replaced the meter before checking each field reading against the notebook from last April.")
    rep = _report(text, [_finding(text, "holistic", "ai_vocab")],
                  {"ai_vocab": (1, 1, 1)}, score=20)
    plan = nr.plan_highlights(rep, [NS(text=text, role="body")])
    assert not plan.flags
    assert plan.soft_flags == {0: [ba.get_engine().split_sentences(text)[0]]}


@pytest.mark.parametrize("trace", ["typo_en", "repeated_word", "ar_orth"])
def test_partial_wording_does_not_override_local_human_traces(trace):
    text = "This holistic approach considers the regional intervention across institutions and communities. " * 4
    findings = [_finding(text, "holistic", "ai_vocab"), _finding(text, "regional", trace)]
    plan = nr.plan_highlights(_report(text, findings, {"ai_vocab": (1, 1, 1)}, score=20),
                              [NS(text=text, role="body")])
    assert not plan.flags and not plan.soft_flags


def _coverage_plan(score):
    # 100 words, distinct 10-word complete sentences. Evidence locates the
    # first four only; the remaining six are deliberately unsupported.
    sentences = [" ".join(["Passage" + chr(97 + i)] * 10) + "." for i in range(10)]
    text = " ".join(sentences)
    spans = ba.get_engine().split_sentences(text)
    units = [("strong", 95, 0, *spans[0])]
    units.extend(("soft", 80 - i, 0, *spans[i]) for i in range(1, 4))
    plan = nr.Plan(qualifying_words=100, per_para={0: (100, 0)}, units=units)
    return nr.balance_to_engine_index(NS(score=score), [NS(text=text)], plan), text, spans


def test_strong_and_probable_coverage_reaches_target_without_word_padding():
    plan, text, spans = _coverage_plan(30)
    assert plan.engine_index == 30 and plan.target_words == 30
    assert plan.flagged_words == 10 and plan.soft_words == 20
    assert plan.total_percent == 30
    assert plan.flags == {0: [spans[0]]}
    assert plan.soft_flags == {0: [spans[1], spans[2]]}
    assert all((a, b) in spans for ranges in (plan.flags, plan.soft_flags)
               for a, b in ranges[0])


def test_insufficient_evidence_does_not_paint_to_an_unsupported_target():
    plan, _text, spans = _coverage_plan(80)
    assert plan.engine_index == 80
    assert plan.total_percent == 40  # a visible gap is preferable to fabricated evidence
    assert plan.soft_flags == {0: spans[1:4]}


def test_strong_passages_also_stop_when_coverage_would_move_farther_from_target():
    plan, text, spans = _coverage_plan(80)
    plan.units = [("strong", 95 - i, 0, *spans[i]) for i in range(4)]
    nr.balance_to_engine_index(NS(score=15), [NS(text=text)], plan)
    # Two equal-distance choices (10% or 20%): keep the smaller coverage,
    # rather than spending the full 25% safety budget on another sentence.
    assert plan.flagged_words == 10 and plan.total_percent == 10
    assert plan.flags == {0: [spans[0]]}


def test_two_tints_are_really_applied_to_source_runs_with_same_word_counts():
    plan, text, _spans = _coverage_plan(30)
    source = nr.build_plain_docx([text])
    result, _removed = nr.apply_highlights(source, plan, [text])
    with zipfile.ZipFile(io.BytesIO(result)) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    words = {nr.HIGHLIGHT_HEX: 0, nr.SOFT_HIGHLIGHT_HEX: 0}
    for run in root.iter(nr._w("r")):
        shade = run.find(nr._w("rPr") + "/" + nr._w("shd"))
        if shade is not None:
            fill = shade.get(nr._w("fill"))
            words[fill] += len(ba.get_engine().WORD_RE.findall("".join(t for _, t in nr._run_atoms(run))))
    assert words[nr.HIGHLIGHT_HEX] == plan.flagged_words
    assert words[nr.SOFT_HIGHLIGHT_HEX] == plan.soft_words
    assert nr._paragraph_text(next(root.iter(nr._w("p")))) == text


def test_pdf_cover_reports_total_coverage_with_both_tints():
    import pymupdf
    plan, _text, _spans = _coverage_plan(30)
    rep = NS(words=100, score=30, confidence=.8)
    meta = {"name": "coverage.docx", "sha": "a" * 64, "date": "2026-10-14",
            "src_pages": 1, "engine_version": "2.1", "engine_sha": ba.ENGINE_SHA256}
    with pymupdf.open() as doc:
        page = nr._cover(doc, rep, plan, meta, None)
        cover = page.get_text()
    assert "30%" in cover
    assert "30 of 100 eligible words highlighted (strong + probable)" in " ".join(cover.split())


def test_pdf_rejected_locations_are_removed_from_cover_counts():
    plan, text, spans = _coverage_plan(30)
    nr._retain_rendered_units(plan, [NS(text=text)], None, {"soft": {0: [spans[1]]}})
    assert not plan.flags and plan.flagged_words == 0
    assert plan.soft_words == 10 and plan.total_percent == 10
    assert plan.engine_index == 30  # rendering must never rewrite the detection result


def test_pdf_renderer_records_only_complete_matched_units(monkeypatch):
    words = [(0, 10, 10, 30, 20, (0, 0, 0)), (0, 40, 10, 60, 20, (0, 0, 0))]
    monkeypatch.setattr(nr, "_pdf_word_stream", lambda *args: (
        words, {(0, 0, 0): (10, 20)}, [("complete", 0), ("sentence", 1)]))
    painted = {}
    plan = nr.Plan(soft_flags={0: [(0, 18)], 1: [(0, 19)]})
    nr._shade_pdf_pages(None, lambda n: NS(draw_rect=lambda *args, **kw: None),
                        {0: (1, 0, 0)}, ["Complete sentence.", "Missing final word."], plan,
                        painted_ranges=painted)
    assert painted == {"soft": {0: [(0, 18)]}}


def test_pdf_report_cover_uses_rendered_coverage_after_alignment(monkeypatch):
    import pymupdf
    from tests.test_detection_layers import LLM_PARA
    with pymupdf.open() as doc:
        page = doc.new_page()
        assert page.insert_textbox((60, 60, 535, 780), "\n\n".join([LLM_PARA] * 3), fontsize=10) >= 0
        data = doc.tobytes()
    rep = ba.analyze_uploaded_bytes("alignment.pdf", data)
    rep.score = 30  # fix the headline so this test isolates PDF rendering
    assert nr.compute_plan(rep, filename="alignment.pdf", data=data).total_words > 0
    # Simulate a selectable PDF whose located passages cannot be aligned
    # reliably: none may be painted, so none may be counted on the cover.
    monkeypatch.setattr(nr, "_shade_pdf_pages", lambda *args, **kw: (0, 100))
    report = nr.build_report_pdf(rep, filename="alignment.pdf", data=data)
    with pymupdf.open(stream=report, filetype="pdf") as doc:
        cover = " ".join(doc[0].get_text().split())
        assert len(doc) == 2
        assert "30%" in cover
        assert "0 of" in cover and "eligible words highlighted" in cover
