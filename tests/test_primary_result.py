from types import SimpleNamespace
import csv
import io

import backend_adapter as b
import nebras_report as nr
from tests.test_detection_layers import _docx, LLM_PARA


def test_final_result_uses_main_engine_and_keeps_diagnostics_separate():
    r = b.analyze_uploaded_bytes("test.docx", _docx([LLM_PARA] * 24))
    assert r.final_score == r.score_engine == b.primary_score(r)
    assert r.diagnostic_score == r.score
    assert r.score_source == "main_engine"
    r.score = 1.0
    assert b.primary_score(r) == r.score_engine
    plan = nr.compute_plan(r, filename="test.docx", data=_docx([LLM_PARA] * 24))
    assert plan.engine_index == r.score_engine
    rows = list(csv.reader(io.StringIO(b.batch_csv_bytes([("test.docx", r)]).decode("utf-8-sig"))))
    assert rows[1][2] == "%.0f" % r.score_engine
    assert rows[1][3] == r.final_verdict


def test_legacy_report_without_engine_snapshot_still_has_a_result():
    assert b.primary_score(SimpleNamespace(score=35.0)) == 35.0
