from types import SimpleNamespace
import csv
import io

import backend_adapter as b
import nebras_report as nr
from tests.test_detection_layers import _docx, LLM_PARA


def test_final_result_uses_balanced_engine_and_keeps_baseline_separate():
    r = b.analyze_uploaded_bytes("test.docx", _docx([LLM_PARA] * 24))
    assert r.final_score == r.balanced.score == b.primary_score(r)
    baseline = r.score_engine
    assert r.diagnostic_score == r.score
    assert r.score_source == "balanced_v3"
    r.score = 1.0
    assert b.primary_score(r) == r.final_score
    assert r.score_engine == baseline
    plan = nr.compute_plan(r, filename="test.docx", data=_docx([LLM_PARA] * 24))
    assert plan.engine_index == r.final_score
    rows = list(csv.reader(io.StringIO(b.batch_csv_bytes([("test.docx", r)]).decode("utf-8-sig"))))
    assert rows[1][2] == "%.0f" % r.final_score
    assert rows[1][3] == r.final_verdict


def test_legacy_report_without_engine_snapshot_still_has_a_result():
    assert b.primary_score(SimpleNamespace(score=35.0)) == 35.0
