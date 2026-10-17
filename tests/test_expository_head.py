import json
from pathlib import Path

import pytest

import backend_adapter as b
import expository_head as eh
from tools.benchmark_balanced import docx
from tests.test_detection_layers import HUMAN_CLEAN_VARIED

ARTIFACT = json.loads((Path(__file__).resolve().parents[1] / "benchmarks/v3.2-expository-head.json").read_text())


def test_runtime_parameters_match_the_training_artifact():
    p = ARTIFACT["params"]
    assert list(eh.INPUTS) == p["inputs"]
    for key in ("mean", "scale", "coef", "intercept", "gate"):
        assert eh.PARAMS[key] == p[key]


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


def test_clean_human_text_is_not_lifted():
    r = b.analyze_uploaded_bytes("human.docx", docx("\n\n".join(HUMAN_CLEAN_VARIED)))
    assert r.balanced.head_lift == 0.0
