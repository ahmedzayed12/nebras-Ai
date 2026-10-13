# -*- coding: utf-8 -*-
"""Slide-deck mode. Synthetic slides only (no user file is used as a reference)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import slide_mode as sm  # noqa: E402

CARD = ["Integrates technical, economic, and social dimensions to ensure a comprehensive understanding of barriers.",
        "Combines survey data, field observations, and interviews to provide valuable insights into stakeholder behaviour.",
        "Aligns local initiatives with national policy goals, fostering collaboration across public and private sectors."]
HUMAN = ["survey in 3 villages, Mar-Apr 2019", "n = 212 households (38 refused)", "price is the problem for most",
         "battery dies after ~2 yrs?? need to check", "see table 4", "loan scheme - only 9 took it",
         "interviews w/ chief + 2 teachers", "pilot failed in Kisumu (flooding) so we moved the site twice"]


def _ai_slide(i):
    b = [sm.Block(40, 20, 400, 30, "%02d · SECTION %d" % (i + 1, i)), sm.Block(40, 50, 900, 90, "Why Adoption Matters Now")]
    b += [sm.Block(40 + 300 * j, 200, 300 + 300 * j, 300, CARD[j]) for j in range(3)]
    b.append(sm.Block(40, 480, 900, 500, "Key takeaway: Affordability, trust, and institutional support drive sustainable adoption."))
    return sm.Slide(i, 960, 540, b)


def _human_slide(i):
    k = [2, 5, 3, 7, 1, 4, 6, 2, 3, 5][i % 10]
    b = [sm.Block(40, 30, 600, 60, ["Intro", "What we did", "Sites", "Numbers", "Problems", "Prices"][i % 6])]
    b += [sm.Block(60, 100 + 30 * j, 900, 125 + 30 * j, HUMAN[(i + j) % len(HUMAN)]) for j in range(k)]
    return sm.Slide(i, 960, 540, b)


PPT = {"creator": "Microsoft® PowerPoint® 2019", "producer": "Microsoft® PowerPoint® 2019"}


def test_detects_deck_by_metadata_and_by_shape():
    slides = [_human_slide(i) for i in range(8)]
    assert sm.detect(PPT, slides)[0]
    assert sm.detect({}, slides)[0]                                   # no metadata: page shape alone
    assert not sm.detect({"creator": "Microsoft® Word 2019"}, slides)[0]


def test_word_prose_pdf_is_not_a_deck():
    para = " ".join(["This paragraph is long continuous prose written in a report."] * 8)
    slides = [sm.Slide(i, 595, 842, [sm.Block(50, 50, 545, 400, para)]) for i in range(5)]
    assert not sm.detect({}, slides)[0]


def test_generated_template_deck_scores_high():
    layer = sm.analyze(PPT, [_ai_slide(i) for i in range(10)])
    assert layer.is_deck and layer.fired >= 3 and layer.confidence > 0 and layer.score > 80
    assert layer.suspect_share > 0.9
    assert sm.fuse(5.0, layer) > 60


def test_irregular_human_deck_is_not_lifted():
    layer = sm.analyze(PPT, [_human_slide(i) for i in range(10)])
    assert layer.is_deck and layer.confidence == 0 and sm.fuse(5.0, layer) == 5.0
    assert (layer.suspect_share or 0) == 0
