# -*- coding: utf-8 -*-
"""Cloudflare Turnstile widget and mandatory server-side validation."""
from __future__ import annotations

from pathlib import Path

import requests
import streamlit as st
import streamlit.components.v1 as components

_COMPONENT = components.declare_component(
    "nebras_turnstile",
    path=str(Path(__file__).with_name("turnstile_frontend") / "dist"),
)
_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def render_turnstile(sitekey: str) -> None:
    """Display the widget and retain a newly solved token for one action."""
    response = _COMPONENT(
        sitekey=sitekey,
        reset=st.session_state.get("_turnstile_reset", 0),
        key="nebras_turnstile",
        default=None,
    )
    if not isinstance(response, dict):
        return
    token = response.get("token")
    if token and token != st.session_state.get("_turnstile_consumed"):
        st.session_state["_turnstile_token"] = str(token)


def _reset(token: str) -> None:
    st.session_state["_turnstile_consumed"] = token
    st.session_state["_turnstile_token"] = None
    st.session_state["_turnstile_reset"] = st.session_state.get("_turnstile_reset", 0) + 1


def validate_token(token: str, secret: str, expected_hostname: str) -> tuple[bool, str]:
    """Validate one token with Cloudflare and bind it to the configured host."""
    try:
        response = requests.post(
            _VERIFY_URL,
            data={"secret": secret, "response": token},
            timeout=10,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError):
        return False, "unavailable"
    if result.get("success") and result.get("hostname") == expected_hostname:
        return True, "ok"
    return False, "invalid"


def allow_analysis(expected_hostname: str) -> bool:
    """Consume one verified token before an expensive analysis action."""
    token = st.session_state.get("_turnstile_token")
    if not token:
        st.warning("أكمل التحقق الأمني أولًا ثم أعد الضغط على زر الفحص.")
        return False
    try:
        secret = str(st.secrets["TURNSTILE_SECRET_KEY"]).strip()
    except Exception:
        secret = ""
    if not secret:
        st.error("لم تُضبط كلمة Turnstile السرية في أسرار التطبيق.")
        return False

    valid, reason = validate_token(token, secret, expected_hostname)
    if reason != "unavailable":
        _reset(token)
    if valid:
        return True
    if reason == "unavailable":
        st.error("تعذّر الاتصال بخدمة التحقق. حاول مرة أخرى.")
    else:
        st.warning("انتهت صلاحية التحقق أو لم يعد صالحًا. أكمله مرة أخرى.")
    return False
