import json
import math
from pathlib import Path

import pytest

import backend_adapter as b
import expository_head as eh
from tools.benchmark_balanced import docx
from tests.test_detection_layers import HUMAN_CLEAN_VARIED

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = json.loads((ROOT / "benchmarks/v3.2-expository-head.json").read_text())


def test_runtime_parameters_match_the_training_artifact():
    p = ARTIFACT["params"]
    assert list(eh.INPUTS) == p["inputs"]
    for key in ("mean", "scale", "coef", "intercept", "gate"):
        assert eh.PARAMS[key] == p[key]
    assert all(s > 0 for s in eh.PARAMS["scale"])


@pytest.mark.parametrize("score", [0.0, 20.0, 64.9, 99.0])
def test_lift_is_upward_only_and_closed_at_the_gate(score):
    gate = eh.PARAMS["gate"]
    assert eh.lift(score, gate - 5) == score
    assert eh.lift(score, gate) == score
    assert score < eh.lift(score, gate + 1) < eh.lift(score, gate + 3) < 100.0


def test_lift_keeps_evidence_order_when_saturated():
    assert eh.lift(94.0, 50.0) < eh.lift(98.0, 50.0)


def test_coefficients_keep_the_engine_prior_sign():
    weights = b.get_engine().load_weights()
    for name, c in zip(eh.INPUTS, eh.PARAMS["coef"]):
        assert c * weights.get(name, 1.0) >= 0, name


def test_neutralized_feature_does_not_reach_the_head():
    rows = {"cite_gap": (1.0, 2.0, 0.0), "frame": (0.5, 1.0, 0.5)}
    x = eh.inputs(rows, [(100, 0.5)], 10.0, 0.0, 1.0)
    assert x[eh.INPUTS.index("cite_gap")] == 0.0
    assert x[eh.INPUTS.index("frame")] == 0.5


def test_short_text_logit_is_shrunk_by_length_confidence():
    x = eh.inputs({}, [(100, 3.0)], 90.0, 4.0, 1.0)
    assert eh.decision(x, 0.5) == pytest.approx(0.5 * eh.decision(x, 1.0))


def test_clean_human_text_is_not_lifted():
    r = b.analyze_uploaded_bytes("human.docx", docx("\n\n".join(HUMAN_CLEAN_VARIED)))
    assert r.balanced.head_lift == 0.0


@pytest.mark.parametrize("n", [2, 3, 4])
def test_short_human_excerpts_are_not_lifted(n):
    r = b.analyze_uploaded_bytes("short.docx", docx("\n\n".join(HUMAN_CLEAN_VARIED[:n])))
    assert r.balanced.head_lift == 0.0


def test_heldout_humans_stay_below_published_thresholds():
    rows = [r for r in ARTIFACT["heldout_test"]["rows"] if r["label"] == "human"]
    assert len(rows) > 400
    assert max(r["v3.2"] for r in rows) < 52


def test_model_fingerprint_covers_the_head():
    import hashlib
    status = b.engine_status()
    files = ("balanced_engine.py", "expository_head.py", "segment_signals.py", "academic_signals.py",
             "tech_signals.py", "matching_text.py", "backend_adapter.py")
    assert status["published_model_fingerprint"] == hashlib.sha256(
        b"".join((ROOT / p).read_bytes() for p in files)).hexdigest()
