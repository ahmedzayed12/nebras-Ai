import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

import academic_signals as academic
import balanced_engine as balanced
import segment_signals as sg
from tools.calibrate_wording import quantile


def para(text, role="heading", style=""):
    return SimpleNamespace(text=text, role=role, style_name=style)


def test_single_subsection_does_not_exempt_rhetoric_from_entire_document():
    topic = academic.document_topic([para("A comprehensive framework"), para("Research questions")])
    assert not (academic.topic_terms(["comprehensive framework"]) & topic)


def test_duplicate_numbered_heading_is_not_independent_topic_support():
    assert not academic.document_topic([para("1. Robust frameworks"), para("2. Robust frameworks")])


def test_recurring_topics_explicit_title_and_keywords_remain_protected():
    topic = academic.document_topic([para("1. Database governance"), para("2. Database design"),
                                     para("Strategic vision", "body", "Title"),
                                     para("Keywords: computerized systems", "keywords")])
    assert academic.topic_terms(["database strategic vision computerized systems"]) <= topic
    assert not academic.topic_terms(["governance design"]) & topic


def test_neighbours_cannot_erase_positive_standalone_evidence():
    unit = sg.Unit(idx=[0], words=100, L=1.5, own_L=1.5, post=0.05)
    seg = sg.Segmentation(units=[unit])
    sg.preserve_standalone_support(seg)
    expected = 1 / (1 + math.exp(-(math.log(sg.PRIOR / (1 - sg.PRIOR)) + 1.5)))
    assert unit.post == pytest.approx(expected)
    assert seg.para_post[0] == unit.post


@pytest.mark.parametrize("own", [-2, 0])
def test_context_safeguard_never_raises_negative_or_neutral_own_evidence(own):
    unit = sg.Unit(idx=[0], words=100, L=2, own_L=own, post=0.01)
    sg.preserve_standalone_support(sg.Segmentation(units=[unit]))
    assert unit.post == 0.01


def test_context_safeguard_does_not_use_document_boost():
    unit = sg.Unit(idx=[0], words=100, L=4, own_L=0.1, post=0.01)
    sg.preserve_standalone_support(sg.Segmentation(units=[unit]))
    assert unit.post < 0.3


def test_v31_threshold_reproduces_fixed_development_p97():
    artifact = json.loads((Path(__file__).resolve().parents[1] /
                           "benchmarks/v3.1-wording-calibration.json").read_text())
    scores = [x for row in artifact["per_document"] for x in row["scores"]]
    assert artifact["documents"] == 52
    assert artifact["units"] == len(scores) == 1068
    assert quantile(scores, .97) == pytest.approx(artifact["p97"])
    assert balanced.WORDING_THRESHOLD == round(quantile(scores, .97), 2)
    # The archived diagnostic channel retains its own historical threshold.
    assert sg._to_logodds(sg.X0, 90) == 0
    assert sg._to_logodds(balanced.WORDING_THRESHOLD, 90, balanced.WORDING_THRESHOLD) == 0
