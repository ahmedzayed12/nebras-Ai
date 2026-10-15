"""Regression coverage for formal prose and recycled document evidence."""
from types import SimpleNamespace

import academic_signals as acad
import backend_adapter as b
import segment_signals as sg


def test_impersonal_formal_vocabulary_does_not_manufacture_convergence():
    text = (
        "Institutional implementation requires organizational coordination. "
        "Administrative documentation records operational procedures. "
        "The framework ensures comprehensive evaluation of organizational performance. "
        "These findings highlight the importance of comprehensive documentation."
    )
    layer = acad.analyze([text] * 120)
    assert layer.words > 2000
    assert layer.signals[-1].z == 1.0  # no first-person pronouns
    assert layer.fired == 2
    assert layer.confidence == 0.0
    assert acad.fuse(12.0, layer) == 12.0


def test_neighbouring_blocks_cannot_attribute_negative_local_evidence():
    seg = sg.Segmentation(units=[
        sg.Unit(idx=[i], words=100, L=L)
        for i, L in enumerate([3.0, 3.0, -0.2, 3.0, 3.0])
    ])
    for u, p in zip(seg.units, sg._forward_backward([u.L for u in seg.units])):
        u.post = p
    assert seg.units[2].post >= sg.SUSPECT
    sg.own_evidence_veto(seg)
    assert seg.units[2].post < sg.SUSPECT
    assert seg.units[0].post >= sg.SUSPECT


def test_pooled_wording_cannot_reenter_as_paragraph_evidence():
    results = []
    for pooled in (20.0, 95.0):
        seg = sg.segment(["The committee recorded the result in May and checked the table twice before filing the report."], ["body"])
        r = SimpleNamespace(score=pooled, texts=["The committee recorded the result in May and checked the table twice before filing the report."], reasons=[])
        b._apply_segmentation(r, seg, [])
        results.append((r.score, seg.units[0].L, seg.units[0].post))
    assert results[0] == results[1]
