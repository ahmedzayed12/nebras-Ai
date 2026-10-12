# -*- coding: utf-8 -*-
"""Streamlit interface "Nebras Ai detector" over the original v2.1 engine.

Equations (LaTeX / Word OMML) are removed by math_guard before the engine runs; see backend_adapter.
PRESENTATION ONLY: every score, verdict, finding, statistic, CSV and HTML report
comes from the unchanged engine through backend_adapter.py. Nothing in this file
computes, rescales, re-thresholds or re-orders an analysis result. Before handing text
to the engine the app always strips copy artefacts the engine mistakes for human typos
(zero-width characters and trailing spaces at line ends). Files are analysed by the engine
exactly as in the original desktop tool, metadata and formatting included.
"""
from __future__ import annotations

import html
from pathlib import Path

import colorsys
import math
import streamlit as st

import analytics
from backend_adapter import (
    analyze_batch_inputs, analyze_pasted, analyze_uploaded_bytes, batch_csv_bytes, normalize_upload_name,
    expand_uploaded_inputs, filename_only, get_engine,
)

st.set_page_config(
    page_title="Nebras Ai detector",
    page_icon="🔎", layout="wide", initial_sidebar_state="collapsed",
)

C = {"ai": "#FF4D8D", "human": "#35E0B4", "warn": "#FFC247"}
# Display-only bands, the same cut-offs the original UI used for paragraph colours.
BAND_HIGH, BAND_MID = 55, 30
MIN_WORDS = 300
MODE_TEXT, MODE_FILE, MODE_BATCH = "نص مباشر", "ملف واحد", "دفعة ملفات أو ZIP"


# ----------------------------------------------------------------- helpers
def _css() -> str:
    return (Path(__file__).resolve().parent / "ui.css").read_text(encoding="utf-8")


def esc(s) -> str:
    # Newlines become entities so a blank line inside a text can never end the HTML block
    # that Streamlit's markdown parser is reading. They still render as line breaks in pre-wrap.
    return (html.escape(str(s), quote=True)
            .replace("\r\n", "&#10;").replace("\n", "&#10;").replace("\r", "&#10;"))


def fmt(v) -> str:
    """Display-only number formatting. Values are never altered, only printed."""
    if isinstance(v, bool):
        return "نعم" if v else "لا"
    if isinstance(v, float):
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"
    return str(v)


_ZW = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"))


def clean_for_engine(text: str) -> str:
    """Drop copy artefacts the engine counts as human spacing/punctuation errors:
    zero-width characters and the trailing spaces at the end of each line."""
    return "\n".join(line.rstrip() for line in text.translate(_ZW).splitlines())


def md(markup: str):
    # Template markup is written indented for readability; Markdown would read 4+ spaces as code.
    flat = "\n".join(line.strip() for line in markup.splitlines() if line.strip())
    st.markdown(flat, unsafe_allow_html=True)


def section(title: str, hint: str = ""):
    small = f"<small>{esc(hint)}</small>" if hint else ""
    md(f'<h2 class="sh">{esc(title)}{small}</h2>')


def table(headers, rows, num_cols=(), wrap_cols=(), max_h=None):
    """RTL-safe HTML table. Each cell resolves its own text direction (Arabic or Latin)."""
    if not rows:
        return
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for row in rows:
        tds = []
        for i, v in enumerate(row):
            cls = " ".join(c for c in ("n" if i in num_cols else "", "wrap" if i in wrap_cols else "") if c)
            tds.append(f'<td class="{cls}">{esc(fmt(v))}</td>')
        body.append("<tr>" + "".join(tds) + "</tr>")
    style = f' style="max-height:{max_h}px"' if max_h else ""
    md(f'<div class="tbl-wrap"{style}><table class="t"><thead><tr>{head}</tr></thead>'
       f'<tbody>{"".join(body)}</tbody></table></div>')


# ----------------------------------------------------------------- page chrome
LOGO = """<svg class="logo" viewBox="0 0 64 64" role="img" aria-label="Nebras">
  <defs><radialGradient id="fl" cx="50%" cy="55%" r="55%"><stop offset="0" stop-color="#FFF6D2"/>
  <stop offset=".45" stop-color="#FFC247"/><stop offset="1" stop-color="#FF8A3D"/></radialGradient></defs>
  <path d="M26 6h12M32 6v6" stroke="#F4EFE2" stroke-width="3" stroke-linecap="round" fill="none"/>
  <path d="M20 16h24l3 6v22l-5 8H22l-5-8V22z" fill="rgba(255,194,71,.10)" stroke="#F4EFE2" stroke-width="2.6" stroke-linejoin="round"/>
  <path d="M32 24c5 6 7 9 7 13a7 7 0 0 1-14 0c0-4 2-7 7-13z" fill="url(#fl)"/>
  <path d="M22 52h20" stroke="#F4EFE2" stroke-width="3" stroke-linecap="round"/></svg>"""


def masthead():
    md(f"""
    <header class="mast">
      {LOGO}
      <div class="mast-copy">
        <h1 class="brand-wordmark" dir="ltr" aria-label="Nebras AI Detector">
          <span class="brand-name">NEBR<span class="brand-accent">A</span>S</span>
          <span class="brand-sub">AI DETECTOR</span>
        </h1>
        <p class="lede">الصق نصًا أو ارفع ملفًا لتعرف مدى احتمال أن يكون مكتوبًا بالذكاء الاصطناعي،
        وأي فقرات النص أكثر اشتباهًا.</p>
      </div>
    </header>""")


def footer():
    md("""
    <footer class="foot">
      <p>المؤشر تقدير احتمالي مبني على قرائن أسلوبية، وليس إثباتًا لمن كتب النص. لا تستخدمه وحده
      في اتهام أحد أو في قرار عالي الأثر. تحقّق من أداء الكاشف باستقلال قبل الاعتماد عليه.</p>
    </footer>""")


# ----------------------------------------------------------------- visual components
def _grad(t: float) -> str:
    """UI indicator colour by score only: green -> orange -> red."""
    stops = [(0.0, (34, 197, 94)), (0.5, (249, 158, 11)), (1.0, (239, 68, 68))]
    t = max(0.0, min(1.0, t))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            k = (t - t0) / (t1 - t0)
            return "#%02X%02X%02X" % tuple(round(a + (b - a) * k) for a, b in zip(c0, c1))
    return "#EF4444"


def _dial_soft_color(color: str) -> str:
    """Lighter shade of the exact same UI score colour (supplementary ticks only)."""
    rgb = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02X%02X%02X" % tuple(round(v * .45 + 255 * .55) for v in rgb)


def _glow(hexcolor: str) -> str:
    """Lift the engine's verdict colour so it reads on the dark canvas (display only)."""
    try:
        h = hexcolor.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
        r, g, b = colorsys.hls_to_rgb(hh, max(ll, 0.64), max(ss, 0.72))
        return "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))
    except Exception:
        return "#FFC247"


def dial_svg(score=None, strong=None, plan_ok=True) -> str:
    """Main number = unmodified engine index; lighter segments supplement strong shading."""
    cx, cy, r1, r2 = 160, 160, 116, 148
    n_lit = 0 if score is None else int(round(max(0, min(100, score))))
    n_strong = n_lit if strong is None else max(0, min(n_lit, int(round(strong))))
    dial_color = _grad(n_lit / 100)
    soft_color = _dial_soft_color(dial_color)
    ticks = []
    for i in range(100):
        ang = math.radians(45 - i / 99 * 270)
        x1, y1 = cx + r1 * math.cos(ang), cy + r1 * math.sin(ang)
        x2, y2 = cx + r2 * math.cos(ang), cy + r2 * math.sin(ang)
        if score is None:
            cls, style = "tk idle", f"--i:{i}"
        elif i < n_strong:
            head = " head" if i == n_lit - 1 else ""
            cls, style = f"tk on{head}", f"--i:{i};color:{dial_color}"
        elif i < n_lit:
            head = " head" if i == n_lit - 1 else ""
            cls, style = f"tk on weak{head}", f"--i:{i};color:{soft_color}"
        else:
            cls, style = "tk", ""
        ticks.append(f'<line class="{cls}" style="{style}" x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}"/>')
    if score is None:
        center = ('<text class="big dash" x="160" y="182" text-anchor="middle">··</text>'
                  '<text class="hint" x="160" y="222" text-anchor="middle">بانتظار النص</text>')
    elif not plan_ok:
        center = ('<text class="big dash" x="160" y="182" text-anchor="middle">—</text>'
                  '<text class="of" x="160" y="222" text-anchor="middle">النص أقصر من 40 كلمة</text>')
    else:
        center = (f'<text class="big num" x="160" y="186" text-anchor="middle">{score:.0f}%</text>'
                  '<text class="of" x="160" y="222" text-anchor="middle">مؤشر المحرك العام</text>')
    label = "بانتظار النص" if score is None else f"مؤشر المحرك العام {score:.0f}%"
    return (f'<div class="dial"><svg viewBox="0 0 320 285" role="img" aria-label="{esc(label)}">'
            f'<circle class="ring" cx="160" cy="160" r="98"/><circle class="ring" cx="160" cy="160" r="90"/>'
            f'{"".join(ticks)}{center}</svg></div>')


def shaded_plan(rep, source):
    """Same strong and supplementary visual shading allocation as the PDF report."""
    if not source:
        return None
    cache = st.session_state.setdefault("plan_cache", {})
    hit = cache.get("entry")
    if hit is None or hit[0] is not rep:          # identity, not id(): ids are reused after garbage collection
        try:
            import nebras_report as nr
            plan = nr.compute_plan(rep, filename=source.get("filename"), data=source.get("data"),
                                   text=source.get("text"))
        except Exception:
            plan = None
        cache.clear()
        cache["entry"] = (rep, plan)
        return plan
    return hit[1]


def dial_panel(rep=None, plan=None):
    if rep is None:
        md(f"""<section class="panel dial-panel" aria-label="لوحة النتيجة">{dial_svg(None)}
        <div class="ends"><span>أسلوب بشري</span><span>أسلوب مولَّد</span></div>
        <div class="verdict-pill idle">ضع نصًا ثم اضغط «حلّل»</div></section>""")
        return
    vc = _glow(rep.color)
    dial_color = _grad(max(0.0, min(100.0, rep.score)) / 100)
    soft_color = _dial_soft_color(dial_color)
    if plan is not None:
        pct = plan.percent if plan.sufficient else 0.0
        soft = 100 * plan.soft_words / plan.qualifying_words if plan.sufficient and plan.qualifying_words else 0.0
        dial = dial_svg(rep.score, pct, plan.sufficient)
        if plan.sufficient:
            legend = (f'<div class="chips" style="justify-content:center;margin:6px 0 2px">'
                      f'<span class="chip"><i style="background:{dial_color}"></i>تظليل أساسي '
                      f'<b class="num">{pct:.0f}%</b></span>'
                      f'<span class="chip"><i style="background:{soft_color};opacity:.65"></i>تظليل تكميلي فاتح '
                      f'<b class="num">+{soft:.0f}%</b></span></div>'
                      '<p class="note">اللون الفاتح امتداد بصري لمؤشر المحرك العام، وليس كشفًا مستقلًا للجمل.</p>')
        else:
            legend = '<p class="note">النص قصير جدًا؛ لا توجد نتيجة صالحة للعرض.</p>' 
    else:
        dial, legend = dial_svg(rep.score), ""
    md(f"""<section class="panel dial-panel" style="--halo:{vc}33" aria-label="لوحة النتيجة">{dial}
      {legend}
      <div class="verdict-pill" style="--vc:{vc}"><i></i><span>{esc(rep.verdict)}</span></div>
      <dl class="facts">
        <div><dt>الثقة بالحكم</dt><dd class="num">{rep.confidence*100:.0f}%</dd></div>
        <div><dt>الكلمات</dt><dd class="num">{rep.words:,}</dd></div>
        <div><dt>الجمل</dt><dd class="num">{rep.sentences:,}</dd></div>
      </dl></section>""")


def math_notice(rep):
    """Tell the reader that equations were excluded, and when the text is too maths-heavy to judge."""
    mm = getattr(rep, "math", None)
    if mm is None or not mm.active:
        return
    stt = mm.stats
    items = stt.found
    share = stt.share * 100
    msg = (f"استُبعدت <b class=\"num\">{items}</b> معادلة/صيغة رياضية "
           f"(نحو <b class=\"num\">{share:.0f}%</b> من حروف الملف) قبل التحليل، لأن الرموز "
           "لا تعكس أسلوب الكاتب وتُضلّل المحرك. النتيجة تخص النص الشرحي فقط.")
    if share >= 45 or rep.words < 250:
        msg += " النص الشرحي المتبقي قليل، فالنتيجة أقل ثقة من المعتاد."
    md(f'<p class="note" style="margin:8px 0 0;border-inline-start:3px solid {C["warn"]};padding-inline-start:10px">{msg}</p>')


def _band(score):
    return "hi" if score >= BAND_HIGH else "mid" if score >= BAND_MID else "lo"


def skyline_svg(rep) -> str:
    """One column per paragraph: width follows its word count, height follows the engine's paragraph score."""
    items = list(rep.para_scores)
    k = len(items)
    W, H, top, label_h = 1000, 270, 30, 40
    VW = W + 44
    plot_h = H - top - label_h
    base = top + plot_h
    gap = 8 if k <= 12 else 4 if k <= 40 else 1.5
    total = sum(n for _, _, n in items) or 1
    avail = W - gap * (k - 1)
    widths = [max(3.0, avail * n / total) for _, _, n in items]
    scale = avail / sum(widths)
    widths = [w * scale for w in widths]
    show_lbl = k <= 30
    y55 = base - BAND_HIGH / 100 * plot_h
    parts = ['<defs>'
             '<linearGradient id="gHi" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FF7AAE"/><stop offset="1" stop-color="#C21F63"/></linearGradient>'
             '<linearGradient id="gMid" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FFD87A"/><stop offset="1" stop-color="#C98A12"/></linearGradient>'
             '<linearGradient id="gLo" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6DF0CB"/><stop offset="1" stop-color="#16947A"/></linearGradient>'
             '</defs>']
    parts.append(f'<line class="axis" x1="0" x2="{W}" y1="{base}" y2="{base}"/>')
    x_right = W
    for j, (i, sc, n) in enumerate(items):
        w = widths[j]
        x = x_right - w
        h = max(2.0, sc / 100 * plot_h)
        g = {"hi": "gHi", "mid": "gMid", "lo": "gLo"}[_band(sc)]
        tip = f"الفقرة {i + 1} · المؤشر {sc:.0f} · الكلمات {n}"
        rx = min(8, w / 2)
        parts.append(f'<g class="col" style="--j:{j}"><title>{esc(tip)}</title>'
                     f'<rect x="{x:.2f}" y="{base - h:.2f}" width="{w:.2f}" height="{h:.2f}" rx="{rx:.1f}" fill="url(#{g})"/></g>')
        if show_lbl and w >= 22:
            parts.append(f'<text class="lbl" x="{x + w / 2:.1f}" y="{base + 24}" text-anchor="middle">{i + 1}</text>')
        if w >= 34 and k <= 30:
            parts.append(f'<text class="val" x="{x + w / 2:.1f}" y="{base - h - 8:.1f}" text-anchor="middle">{sc:.0f}</text>')
        x_right = x - gap
    parts.append(f'<line class="thr" x1="0" x2="{W + 6}" y1="{y55:.1f}" y2="{y55:.1f}"/>')
    parts.append(f'<text class="thrt" x="{W + 12}" y="{y55 + 4:.1f}" text-anchor="start">{BAND_HIGH}</text>')
    return f'<svg viewBox="0 0 {VW} {H}" role="img" aria-label="مؤشر الاشتباه لكل فقرة">{"".join(parts)}</svg>'



# ----------------------------------------------------------------- PDF report (Turnitin-style)
def report_download_panel(rep, source: dict, key: str):
    """Presentation only. The PDF is built by nebras_report.py from the engine's own report object."""
    import nebras_report as nr
    section("تقرير PDF احترافي", "غلاف + ترويسة بالبصمة SHA-256 + أرقام الصفحات + تظليل الجمل المرصودة على شكل ملفك الأصلي")
    if not source:
        st.info("أعد تحليل النص أو الملف لإنشاء التقرير.")
        return
    status = nr.converter_status()
    source_is_pdf = str(source.get("filename", "")).lower().endswith(".pdf")
    if not source_is_pdf and not status["word"] and not status["libreoffice"]:
        st.warning("لإنتاج صفحات مطابقة تمامًا لملفك الأصلي يلزم Microsoft Word (مع docx2pdf) أو LibreOffice المجاني "
                   "(libreoffice.org). ثبّت أحدهما ثم أعد تشغيل البرنامج.")
        return
    if source_is_pdf:
        md('<p class="note">يُحافَظ على الصفحات الأصلية لملف PDF في التقرير. يُظلَّل النص القابل للتحديد عندما يطابق النص المستخرج؛ الصفحات المصوّرة تحتاج OCR للفحص وقد لا يظهر عليها التظليل.</p>')
    cache = st.session_state.setdefault("pdf_cache", {})
    ck = (key, id(rep))
    if ck not in cache:
        if st.button("تجهيز تقرير PDF", type="primary", key=f"mk_{key}"):
            with st.spinner("جارٍ تظليل الملف وتحويله وبناء التقرير…"):
                try:
                    pdf = nr.build_report_pdf(rep, filename=source.get("filename"),
                                              data=source.get("data"), text=source.get("text"))
                    while len(cache) >= 3:
                        cache.pop(next(iter(cache)))
                    cache[ck] = pdf
                except Exception as ex:
                    st.error(str(ex))
    if ck in cache:
        stem = Path(filename_only(source.get("filename") or "text")).stem or "text"
        st.download_button("⬇ تحميل التقرير (PDF)", data=cache[ck], type="primary", key=f"dl_{key}",
                           file_name=f"Nebras_AI_Report_{stem}.pdf", mime="application/pdf")
        md('<p class="note">النسبة الرئيسية في التقرير من مؤشر المحرك العام مباشرة؛ التظليل القوي للمواضع المرصودة، والفاتح تكميلي بصري لا يُثبت مصدر جملة بعينها.</p>')


# ----------------------------------------------------------------- result
def report_dashboard(rep):
    section("خريطة الفقرات", "عرض العمود يتناسب مع عدد كلمات الفقرة، وارتفاعه مع درجة الاشتباه")
    if not rep.para_scores:
        st.info("لم يحسب المحرك درجة لفقرات هذا النص.")
        return
    counts = {"hi": 0, "mid": 0, "lo": 0}
    for _, sc, _ in rep.para_scores:
        counts[_band(sc)] += 1
    md(f"""<div class="panel sky">{skyline_svg(rep)}
      <div class="chips">
        <span class="chip"><i style="background:{C['ai']}"></i>اشتباه مرتفع (55 فأكثر) <b class="num">{counts['hi']}</b></span>
        <span class="chip"><i style="background:{C['warn']}"></i>متوسط (30 إلى 54) <b class="num">{counts['mid']}</b></span>
        <span class="chip"><i style="background:{C['human']}"></i>منخفض (أقل من 30) <b class="num">{counts['lo']}</b></span>
      </div></div>""")
    md('<div style="height:18px"></div>')
    table(["الفقرة", "الدرجة", "الكلمات", "مطلع الفقرة"],
          [(i + 1, score, n, rep.texts[i][:155]) for i, score, n in rep.para_scores],
          num_cols={0, 1, 2}, wrap_cols={3}, max_h=340)
    md('<p class="caveat">المؤشر تقدير احتمالي مبني على قرائن أسلوبية، وليس إثباتًا لجهة كتابة النص.</p>')


def batch_dashboard(results):
    good = [(n, r) for n, r in results if not isinstance(r, Exception)]
    errors = [(n, r) for n, r in results if isinstance(r, Exception)]
    section("نتيجة الدفعة")
    md(f"""<dl class="facts" style="border-top:0;padding-top:0;margin-top:0;max-width:520px">
      <div><dt>الملفات الواردة</dt><dd class="num">{len(results)}</dd></div>
      <div><dt>تم تحليلها</dt><dd class="num" style="color:{C['human']}">{len(good)}</dd></div>
      <div><dt>تعذّر تحليلها</dt><dd class="num" style="color:{C['ai']}">{len(errors)}</dd></div></dl>""")
    rows = []
    for name, r in results:
        if isinstance(r, Exception):
            rows.append((name, "—", "—", str(r)))
        else:
            rows.append((name, r.words, f"{r.score:.0f}", r.verdict))
    md('<div style="height:14px"></div>')
    table(["الملف", "الكلمات", "المؤشر", "الحكم"],
          rows, num_cols={1, 2}, wrap_cols={0, 3}, max_h=460)
    if errors:
        with st.expander("تفاصيل الملفات التي تعذّر تحليلها"):
            for name, r in errors:
                st.error(f"{name}: {r}")
    md('<div style="height:14px"></div>')
    st.download_button("تنزيل نتائج الدفعة (CSV)", data=batch_csv_bytes(results),
                       file_name="AI_Inspector_Batch_Results.csv", mime="text/csv", type="primary")
    if good:
        section("نتيجة ملف محدد")
        idx = st.selectbox("اختر ملفًا", options=range(len(good)),
                           format_func=lambda i: good[i][0], key=f"batch_choice_{_nonce()}")
        label = good[idx][0]
        data = (st.session_state.get("batch_sources") or {}).get(label)
        src = {"filename": filename_only(label), "data": data} if data else None
        dial_panel(good[idx][1], shaded_plan(good[idx][1], src))
        math_notice(good[idx][1])
        report_download_panel(good[idx][1], src, f"b{idx}")
        report_dashboard(good[idx][1])


# ----------------------------------------------------------------- input desk
def _nonce() -> int:
    """Bumped on clear. Rotating the widget keys forces brand-new (empty) inputs."""
    return st.session_state.get("desk_nonce", 0)


def reset_results():
    # Professional, reliable reset: rotate the desk keys so every input becomes a fresh
    # empty widget, and drop cached results. Same effect as a page reload, but deterministic.
    st.session_state["desk_nonce"] = _nonce() + 1
    for key in ("current_report", "batch_reports", "editor_origin", "report_source",
                "batch_sources", "original_file_text", "plan_cache", "pdf_cache"):
        st.session_state.pop(key, None)


def input_desk():
    n = _nonce()
    has_results = (st.session_state.get("current_report") is not None
                   or st.session_state.get("batch_reports") is not None)
    selection = st.radio("طريقة الإدخال", [MODE_TEXT, MODE_FILE, MODE_BATCH],
                         horizontal=True, key=f"input_mode_{n}")
    md('<div style="height:10px"></div>')
    if selection == MODE_TEXT:
        text_key = f"pasted_input_{n}"
        st.text_area("النص", height=130 if has_results else 290, key=text_key,
                     placeholder="الصق النص العربي أو الإنجليزي هنا (300 كلمة على الأقل). الفحص يطابق المحرك الأصلي.")
        _words_now = len(clean_for_engine(st.session_state.get(text_key, "")).split())
        st.caption(f"عدد الكلمات: {_words_now} / الحد الأدنى {MIN_WORDS} كلمة.")
        if st.button("حلّل النص", type="primary", key=f"analyze_text_{n}"):
            s = st.session_state.get(text_key, "")
            src = clean_for_engine(s)
            words = len(src.split())
            if not s.strip():
                st.warning("أدخل نصًا أولًا.")
            elif words < MIN_WORDS:
                st.warning(f"النص يحتوي {words} كلمة فقط. لا يُقبل التحليل أقل من {MIN_WORDS} كلمة — أطِل النص ثم أعد المحاولة.")
            else:
                try:
                    st.session_state["current_report"] = analyze_pasted(src)
                    st.session_state["batch_reports"] = None
                    st.session_state["editor_origin"] = None
                    st.session_state["report_source"] = {"text": src}
                    analytics.log_usage("text")
                except Exception as ex:
                    st.error(str(ex))
    elif selection == MODE_FILE:
        # No `type=` filter: Android/iOS pickers grey out files when given a list of extensions (and drop files
        # that arrive without one). Unsupported types are rejected with a clear message below instead.
        uploaded = st.file_uploader("ملف PDF أو Word أو نص", key=f"one_file_{n}")
        md('<p class="note">ملفات PDF النصية تُقرأ مباشرة، والمصوّرة عبر OCR محلي (يحتاج Tesseract مثبتًا). ملفات Word تحتفظ بقرائن تنسيقها.</p>')
        if st.button("حلّل الملف", type="primary", key=f"analyze_file_{n}"):
            if uploaded is None:
                st.warning("اختر ملفًا أولًا.")
            else:
                try:
                    data = uploaded.getvalue()
                    uname = normalize_upload_name(uploaded.name, getattr(uploaded, "type", ""))
                    report = analyze_uploaded_bytes(uname, data)
                    st.session_state["current_report"] = report
                    st.session_state["batch_reports"] = None
                    st.session_state["editor_origin"] = (uname, data, report.is_docx)
                    st.session_state["report_source"] = {"filename": uname, "data": data}
                    st.session_state["editable_text"] = "\n".join(report.texts)
                    st.session_state["original_file_text"] = "\n".join(report.texts)
                    analytics.log_usage("file")
                except Exception as ex:
                    st.error(str(ex))
        origin = st.session_state.get("editor_origin")
        if origin and st.session_state.get("current_report") is not None:
            with st.expander("عدّل النص المستخرج وأعد الفحص"):
                edit_key = f"editable_text_{n}"
                st.text_area("النص المستخرج (قابل للتعديل)", height=200, key=edit_key,
                             value=st.session_state.get("editable_text", ""))
                md('<p class="note">عند تعديل النص يُفحص كنص ملصق؛ وتُفقد قرائن تنسيق Word وربطه بالصفحات الأصلية في PDF.</p>')
                if st.button("أعد الفحص", key=f"rescan_edited_{n}"):
                    text = st.session_state.get(edit_key, "")
                    if not text.strip():
                        st.warning("النص فارغ.")
                    else:
                        try:
                            if text == st.session_state.get("original_file_text", ""):
                                r = analyze_uploaded_bytes(origin[0], origin[1])
                                st.session_state["report_source"] = {"filename": origin[0], "data": origin[1]}
                            else:
                                src = clean_for_engine(text)
                                r = analyze_pasted(src)
                                st.session_state["report_source"] = {"text": src}
                            st.session_state["current_report"] = r
                            st.session_state["batch_reports"] = None
                            analytics.log_usage("rescan")
                        except Exception as ex:
                            st.error(str(ex))
    else:
        multiple = st.file_uploader("ملفات متعددة أو أرشيف ZIP", accept_multiple_files=True,
                                    key=f"multi_files_{n}")
        md('<p class="note">ارفع أرشيف ZIP لمجلد كامل. يُحلَّل كل ملف بنفس طريقة الملف المنفرد.</p>')
        if st.button("حلّل الدفعة", type="primary", key=f"analyze_batch_{n}"):
            if not multiple:
                st.warning("ارفع ملفًا واحدًا على الأقل.")
            else:
                try:
                    files = [(normalize_upload_name(x.name, getattr(x, 'type', '')), x.getvalue()) for x in multiple]
                    results = analyze_batch_inputs(files)
                    if results:
                        st.session_state["batch_sources"] = {lbl: d for lbl, d in expand_uploaded_inputs(files)}
                        st.session_state["batch_reports"] = results
                        st.session_state["current_report"] = None
                        st.session_state["editor_origin"] = None
                        analytics.log_usage("batch")
                    else:
                        st.warning("لم يتضمن الرفع ملفات مدعومة.")
                except Exception as ex:
                    st.error(str(ex))


# ----------------------------------------------------------------- admin (hidden)
def _admin_gate() -> bool:
    """Gate shown in the (collapsed-by-default) sidebar. No URL parameter needed,
    because Streamlit Cloud passes query params unreliably inside its iframe."""
    if st.session_state.get("admin_ok"):
        return True
    with st.sidebar:
        pw = st.text_input("كلمة السر", type="password",
                           key="admin_pw_side", label_visibility="collapsed",
                           placeholder="دخول المشرف")
        if st.button("دخول", key="admin_login_side"):
            if analytics.check_password(pw):
                st.session_state["admin_ok"] = True
                st.rerun()
            else:
                st.error("كلمة السر غير صحيحة.")
    return False


def admin_panel():
    if not st.session_state.get("admin_ok"):
        _admin_gate()
        return
    md("---")
    section("لوحة المشرف — إحصاءات الزوّار")
    if not analytics.enabled():
        st.warning("لم تُضبط أسرار Supabase بعد. أضِف [supabase] url/key و ADMIN_PASSWORD في أسرار التطبيق.")
        return
    stats = analytics.fetch_stats()
    if stats is None:
        st.error("تعذّر قراءة البيانات من Supabase. تحقّق من العنوان/المفتاح ووجود جدول events.")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("عدد الزيارات", stats["visits"])
    c2.metric("مرات الاستخدام", stats["uses"])
    c3.metric("جلسات فريدة", stats["unique_sessions"])
    c4.metric("إجمالي الأحداث", stats["events"])
    md(f'<p class="note">آخر حدث: <bdi>{esc(stats["last_event"] or "—")}</bdi></p>')
    section("حسب البلد")
    table(["البلد", "عدد الزيارات"], list(stats["by_country"]),
          num_cols={1}, wrap_cols={0}, max_h=420)
    section("حسب نوع الاستخدام")
    table(["النوع", "العدد"], sorted(stats["by_kind"].items(), key=lambda kv: -kv[1]),
          num_cols={1}, wrap_cols={0})
    st.button("تحديث", key="admin_refresh")
    if st.button("خروج", key="admin_logout"):
        st.session_state["admin_ok"] = False
        st.rerun()


def main():
    md(f"<style>{_css()}</style>")
    try:
        get_engine()
    except Exception as e:
        st.error(f"توقف التشغيل لحماية سلامة محرك التحليل: {e}")
        st.stop()
    analytics.init_session()
    masthead()
    batch = st.session_state.get("batch_reports")
    rep = st.session_state.get("current_report")
    desk, board = st.columns([1.15, 1], gap="large")
    with desk:
        with st.container(border=True):
            input_desk()
            batch = st.session_state.get("batch_reports")
            rep = st.session_state.get("current_report")
            if batch is not None or rep is not None:
                st.button("امسح النتائج", key="clear_results", on_click=reset_results)
                if rep is not None and batch is None:
                    md(f'<p class="note" style="margin:6px 0 0">المصدر: <bdi>{esc(rep.source)}</bdi></p>')
    with board:
        if batch is not None:
            md(f"""<section class="panel dial-panel" aria-label="لوحة الدفعة">{dial_svg(None).replace('بانتظار النص','دفعة')}
            <div class="verdict-pill idle">تم تحليل {sum(1 for _, r in batch if not isinstance(r, Exception))} من {len(batch)} ملفًا</div></section>""")
        else:
            dial_panel(rep, shaded_plan(rep, st.session_state.get("report_source")) if rep is not None else None)
            if rep is not None:
                math_notice(rep)
                report_download_panel(rep, st.session_state.get("report_source"), "single")
    if batch is not None:
        batch_dashboard(batch)
    elif rep is not None:
        report_dashboard(rep)
    footer()
    admin_panel()


if __name__ == "__main__":
    main()
