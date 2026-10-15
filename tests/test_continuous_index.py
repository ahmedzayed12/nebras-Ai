from types import SimpleNamespace

import pytest
import backend_adapter as b
import segment_signals as sg


def report_for(posteriors, lengths):
    texts = ["word " * n for n in lengths]
    units = [sg.Unit(idx=[i], words=n, L=1.0, post=p)
             for i, (p, n) in enumerate(zip(posteriors, lengths))]
    seg = sg.Segmentation(units=units)
    sg._summarise(seg)
    r = SimpleNamespace(score=20.0, texts=texts, reasons=[])
    b._apply_segmentation(r, seg, [])
    return r


def test_below_threshold_evidence_is_not_discarded():
    r = report_for([0.1, 0.2], [100, 300])
    assert r.score == 17.5
    assert r.ai_share == 0.0  # no paragraph warrants shading


def test_crossing_threshold_does_not_jump_from_zero_to_one_hundred():
    low = report_for([0.499], [100])
    high = report_for([0.501], [100])
    assert high.score - low.score == pytest.approx(0.2)
    assert low.ai_share == 0.0 and high.ai_share == 1.0


def test_zero_evidence_can_still_produce_zero_without_artificial_floor():
    assert report_for([0.0], [100]).score == 0.0


def test_veto_preserves_local_strength_instead_of_flattening_to_49():
    seg = sg.Segmentation(units=[
        sg.Unit(idx=[i], words=100, L=L, own_L=L, post=0.9)
        for i, L in enumerate([-0.2, -1.0, -3.0])
    ])
    sg.own_evidence_veto(seg)
    assert 0.5 > seg.units[0].post > seg.units[1].post > seg.units[2].post > 0
