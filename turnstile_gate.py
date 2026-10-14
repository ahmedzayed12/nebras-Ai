"""Cloudflare Turnstile for Streamlit Cloud. Secrets remain on the server."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import os
import secrets

import requests

SITE_KEY = "0x4AAAAAAFUeD40X5Nr1Y-ox"
ACTION = "nebras_analysis"
VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
WIDGET_KEY = "nebras_turnstile_widget"
NONCE_KEY = "nebras_turnstile_challenge"


def verify_token(token, secret, challenge, hostnames=(), *, post=None):
    """Validate each token remotely, including its action and session binding."""
    if not isinstance(token, str) or not 0 < len(token) <= 2048 or not secret or not challenge:
        return False
    try:
        response = (post or requests.post)(
            VERIFY_URL, data={"secret": secret, "response": token},
            timeout=(3, 10), allow_redirects=False,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError):
        return False
    if not isinstance(result, dict) or result.get("success") is not True:
        return False
    if result.get("action") != ACTION or result.get("cdata") != challenge:
        return False
    hostname = result.get("hostname")
    if not isinstance(hostname, str) or not hostname.strip():
        return False
    allowed = {h.strip().lower().rstrip(".") for h in hostnames if h.strip()}
    return not allowed or hostname.strip().lower().rstrip(".") in allowed


def _setting(name, default=""):
    if name in os.environ:
        return os.environ[name]
    import streamlit as st
    from streamlit.errors import StreamlitSecretNotFoundError
    try:
        return st.secrets.get(name, default)
    except (FileNotFoundError, StreamlitSecretNotFoundError):
        return default


def _settings():
    key = str(_setting("TURNSTILE_SITE_KEY", SITE_KEY)).strip()
    secret = str(_setting("TURNSTILE_SECRET_KEY")).strip()
    allowed = _setting("TURNSTILE_HOSTNAMES", ())
    if isinstance(allowed, str):
        allowed = allowed.split(",")
    if not isinstance(allowed, (list, tuple)):
        allowed = ()
    return key, secret, tuple(h for h in allowed if isinstance(h, str))


def _nonce(state):
    if NONCE_KEY not in state:
        state[NONCE_KEY] = secrets.token_hex(16)
    return state[NONCE_KEY]


def authorize_analysis(state, secret, hostnames=(), *, verifier=verify_token):
    """Never trust a browser 'success' flag; consume the challenge once."""
    challenge = _nonce(state)
    payload = state.get(WIDGET_KEY)
    if (not secret or not isinstance(payload, dict)
            or payload.get("challenge") != challenge
            or not isinstance(payload.get("token"), str) or not payload["token"]):
        return False
    # Rotate before attempting validation, including failures and timeouts.
    state[NONCE_KEY] = secrets.token_hex(16)
    return verifier(payload["token"], secret, challenge, hostnames)


def require_human():
    import streamlit as st
    _key, secret, hostnames = _settings()
    if not secret:
        st.error("خدمة التحقق غير متاحة حاليًا. يرجى المحاولة لاحقًا.")
        return False
    if not authorize_analysis(st.session_state, secret, hostnames):
        st.warning("أكمل التحقق البشري ثم أعد الضغط على زر الفحص.")
        return False
    return True


@lru_cache(maxsize=1)
def _component():
    import streamlit.components.v1 as components
    return components.declare_component(
        "nebras_turnstile", path=str(Path(__file__).parent / "components" / "turnstile"),
    )


def render_turnstile():
    import streamlit as st
    key, secret, _hostnames = _settings()
    if not key or not secret:
        st.caption("التحقق البشري غير متاح حاليًا.")
        return
    _component()(site_key=key, action=ACTION, challenge=_nonce(st.session_state),
                 key=WIDGET_KEY, default=None)
