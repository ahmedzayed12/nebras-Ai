"""Offline UI integration. Mock only the external anti-bot service in this test."""
import json
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def test_text_form_publishes_the_v3_result_without_changing_authentication_code():
    fixture = json.loads((Path(__file__).parent / "fixtures/generated_controls.json").read_text())[0]["text"]
    with patch("turnstile_gate.require_human", return_value=True):
        app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py"), default_timeout=30).run()
        source = "\n\n".join([fixture] * 2).replace("framework", "frame\u2060work")
        app.text_area[0].set_value(source).run()
        app.button[0].click().run()
        assert not app.exception
        assert not app.error
        report = app.session_state["current_report"]
        assert report.score_source == "balanced_v3"
        assert "frame\u2060work" in "".join(report.texts)
        assert any("%.0f%%" % report.final_score in str(block.value) for block in app.markdown)
