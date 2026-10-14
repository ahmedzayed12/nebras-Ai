"""Server validation and app gates without production credentials or network calls."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
from pathlib import Path

import pytest
import requests
import turnstile_gate as gate


def _response(**changes):
    data = {"success": True, "action": gate.ACTION, "cdata": "challenge-a", "hostname": "nebras.streamlit.app"}
    data.update(changes)
    return NS(raise_for_status=lambda: None, json=lambda: data)


def test_validation_runs_only_on_server_with_action_session_and_hostname_checks():
    post = Mock(return_value=_response())
    assert gate.verify_token("token", "test-secret", "challenge-a", ["nebras.streamlit.app"], post=post)
    url, = post.call_args.args
    kwargs = post.call_args.kwargs
    assert url == gate.VERIFY_URL
    assert kwargs["data"] == {"secret": "test-secret", "response": "token"}
    assert kwargs["allow_redirects"] is False
    assert "verify" not in kwargs  # requests keeps TLS verification enabled


@pytest.mark.parametrize("changes", [
    {"success": False}, {"success": "true"}, {"action": "other"},
    {"cdata": "another-session"}, {"hostname": "attacker.example"}, {"hostname": None},
])
def test_mismatched_or_failed_validation_is_rejected(changes):
    assert not gate.verify_token("token", "test-secret", "challenge-a", ["nebras.streamlit.app"],
                                 post=Mock(return_value=_response(**changes)))


@pytest.mark.parametrize("token,secret", [(None, "secret"), ("", "secret"), ("x" * 2049, "secret"), ("token", "")])
def test_invalid_or_missing_input_never_calls_cloudflare(token, secret):
    post = Mock()
    assert not gate.verify_token(token, secret, "challenge-a", post=post)
    post.assert_not_called()


@pytest.mark.parametrize("failure", [requests.Timeout, requests.ConnectionError, ValueError])
def test_network_errors_and_invalid_json_fail_closed(failure):
    assert not gate.verify_token("token", "test-secret", "challenge-a", post=Mock(side_effect=failure))


def test_a_verified_token_cannot_authorize_a_second_analysis():
    state = {gate.NONCE_KEY: "challenge-a", gate.WIDGET_KEY: {"token": "token", "challenge": "challenge-a"}}
    verifier = Mock(return_value=True)
    assert gate.authorize_analysis(state, "test-secret", verifier=verifier)
    assert not gate.authorize_analysis(state, "test-secret", verifier=verifier)
    verifier.assert_called_once()


def test_browser_success_flag_is_not_authorization():
    state = {gate.NONCE_KEY: "challenge-a", gate.WIDGET_KEY: {"success": True, "challenge": "challenge-a"}}
    verifier = Mock(return_value=True)
    assert not gate.authorize_analysis(state, "test-secret", verifier=verifier)
    verifier.assert_not_called()


def test_failure_consumes_the_attempt_and_requires_a_fresh_challenge():
    state = {gate.NONCE_KEY: "challenge-a", gate.WIDGET_KEY: {"token": "spent-token", "challenge": "challenge-a"}}
    verifier = Mock(return_value=False)
    assert not gate.authorize_analysis(state, "test-secret", verifier=verifier)
    assert state[gate.NONCE_KEY] != "challenge-a"
    assert not gate.authorize_analysis(state, "test-secret", verifier=verifier)
    verifier.assert_called_once()


def test_unconfigured_streamlit_app_blocks_analysis(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from tests.test_detection_layers import LLM_PARA
    monkeypatch.setattr(gate, "_settings", lambda: (gate.SITE_KEY, "", ()))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "streamlit_app.py").run(timeout=30)
    app.text_area[0].set_value("\n".join([LLM_PARA] * 6))
    next(b for b in app.button if b.label == "حلّل النص").click().run(timeout=30)
    assert not app.exception
    assert app.error
    assert app.session_state._state.filtered_state.get("current_report") is None


def test_streamlit_app_analyzes_only_after_remote_verification(monkeypatch):
    from streamlit.testing.v1 import AppTest
    from tests.test_detection_layers import LLM_PARA
    monkeypatch.setattr(gate, "_settings", lambda: (gate.SITE_KEY, "test-secret", ("nebras.streamlit.app",)))
    # Render the actual custom component; only the Cloudflare response is mocked.
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "streamlit_app.py").run(timeout=30)
    nonce = app.session_state[gate.NONCE_KEY]
    app.session_state[gate.WIDGET_KEY] = {"token": "verified-token", "challenge": nonce}
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs["data"]["response"])
        return _response(cdata=nonce)
    monkeypatch.setattr(gate.requests, "post", post)
    app.text_area[0].set_value("\n".join([LLM_PARA] * 6))
    next(b for b in app.button if b.label == "حلّل النص").click().run(timeout=30)
    assert not app.exception
    assert not app.error and not app.warning
    assert app.session_state["current_report"].words >= 300
    assert calls == ["verified-token"]
    # The original token cannot be replayed by pressing Analyze again.
    next(b for b in app.button if b.label == "حلّل النص").click().run(timeout=30)
    assert app.warning
    assert calls == ["verified-token"]
