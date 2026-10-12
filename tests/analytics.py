# -*- coding: utf-8 -*-
"""Server-side visitor / usage analytics for the Streamlit app.

WHY: Streamlit Cloud does not tell you who visited, from where, or how many times the tool
was used. This module records that itself -- no client-side JavaScript, no cookies -- by
writing one small row per event to a Supabase (Postgres + PostgREST) table, and reads back
aggregate counts for a hidden in-app admin panel.

Events:
    visit    -> once per browser session
    analyze  -> once per successful run (text / file / rescan / batch)

Privacy: only a random per-session id, the event type and country/city (derived from the
client IP) are stored. The raw IP, the analysed text and the uploaded file are never stored.

Secrets (Streamlit Cloud -> App -> Settings -> Secrets), TOML:

    ADMIN_PASSWORD = "choose-a-strong-password"
    [supabase]
    url = "https://<project-ref>.supabase.co"
    key = "<service_role key>"

Everything no-ops silently when the secrets are absent, so the app keeps working.
"""
from __future__ import annotations

import ipaddress
import uuid
from collections import Counter
from typing import Optional

import requests
import streamlit as st

_TIMEOUT = 6
_TABLE = "events"
_GEO_URL = "https://ipwho.is/{ip}"


# --------------------------------------------------------------------------- config
def _cfg():
    try:
        cfg = st.secrets["supabase"]
        url = str(cfg["url"]).rstrip("/")
        key = str(cfg["key"])
    except Exception:
        return None, None
    return (url, key) if url and key else (None, None)


def enabled() -> bool:
    url, key = _cfg()
    return bool(url and key)


def _headers(key: str, prefer: Optional[str] = None) -> dict:
    h = {"apikey": key, "Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def _insert(row: dict) -> bool:
    url, key = _cfg()
    if not url:
        return False
    try:
        r = requests.post(f"{url}/rest/v1/{_TABLE}", json=row,
                          headers=_headers(key, "return=minimal"), timeout=_TIMEOUT)
        return r.status_code in (200, 201, 204)
    except Exception:
        return False


# --------------------------------------------------------------------------- session
def _sid() -> str:
    sid = st.session_state.get("_analytics_sid")
    if not sid:
        sid = uuid.uuid4().hex
        st.session_state["_analytics_sid"] = sid
    return sid


def _client_ip() -> Optional[str]:
    try:
        ip = getattr(st.context, "ip_address", None)
        if ip:
            return str(ip)
    except Exception:
        pass
    try:
        hdrs = st.context.headers
        for k in ("X-Forwarded-For", "x-forwarded-for", "X-Real-Ip"):
            v = hdrs.get(k)
            if v:
                return v.split(",")[0].strip()
    except Exception:
        pass
    return None


def _is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved)


@st.cache_data(ttl=86400, show_spinner=False)
def _geo(ip: str):
    """IP -> (country, city). Cached for a day; failures return (None, None)."""
    if not ip or not _is_public(ip):
        return None, None
    try:
        j = requests.get(_GEO_URL.format(ip=ip), timeout=_TIMEOUT).json()
        if j.get("success"):
            return j.get("country") or None, j.get("city") or None
    except Exception:
        pass
    return None, None


def init_session() -> None:
    """Record one 'visit' per browser session. Safe to call on every rerun."""
    if not enabled() or st.session_state.get("_analytics_visit_done"):
        return
    st.session_state["_analytics_visit_done"] = True
    country, city = _geo(_client_ip() or "")
    st.session_state["_analytics_country"] = country
    st.session_state["_analytics_city"] = city
    _insert({"session_id": _sid(), "event": "visit", "country": country, "city": city})


def log_usage(kind: str = "analyze") -> None:
    """Record one successful run of the tool."""
    if not enabled():
        return
    _insert({"session_id": _sid(), "event": kind,
             "country": st.session_state.get("_analytics_country"),
             "city": st.session_state.get("_analytics_city")})


# --------------------------------------------------------------------------- read / aggregate
def _aggregate(rows) -> dict:
    """Pure aggregation over event rows (unit-testable without Supabase)."""
    visits = sum(1 for r in rows if r.get("event") == "visit")
    uses = sum(1 for r in rows if r.get("event") and r.get("event") != "visit")
    by_kind = Counter(r.get("event") for r in rows)
    sessions = {r.get("session_id") for r in rows if r.get("session_id")}
    visits_by_country = Counter()
    country_of_session = {}
    for r in rows:
        if r.get("session_id"):
            country_of_session.setdefault(r["session_id"], r.get("country"))
        if r.get("event") == "visit":
            visits_by_country[r.get("country") or "غير معروف"] += 1
    last = max((r.get("created_at") or "" for r in rows), default="")
    return {
        "events": len(rows),
        "visits": visits,
        "uses": uses,
        "by_kind": dict(by_kind),
        "unique_sessions": len(sessions),
        "by_country": visits_by_country.most_common(),
        "last_event": last,
    }


def fetch_events(limit: int = 20000):
    url, key = _cfg()
    if not url:
        return None
    try:
        r = requests.get(
            f"{url}/rest/v1/{_TABLE}",
            params={"select": "created_at,session_id,event,country,city",
                    "order": "created_at.desc", "limit": str(limit)},
            headers=_headers(key), timeout=_TIMEOUT)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def fetch_stats(limit: int = 20000) -> Optional[dict]:
    rows = fetch_events(limit)
    if rows is None:
        return None
    return _aggregate(rows)


def check_password(candidate: str) -> bool:
    import hmac
    try:
        expected = str(st.secrets.get("ADMIN_PASSWORD", ""))
    except Exception:
        expected = ""
    return bool(expected) and hmac.compare_digest(str(candidate), expected)
