# -*- coding: utf-8 -*-
"""Streamlit interface for the original AI Text Inspector v2.1 (v3 redesign).

PRESENTATION ONLY: every score, verdict, finding, statistic, CSV and HTML report
comes from the unchanged engine through backend_adapter.py. Nothing in this file
computes, rescales, re-thresholds or re-orders an analysis result.
"""
from __future__ import annotations

from collections import defaultdict
import html
from pathlib import Path

import plotly.graph_objects as go
import streamlit as st

from backend_adapter import (
    analyze_batch_inputs, analyze_pasted, analyze_uploaded_bytes, batch_csv_bytes,
    engine_status, get_engine, html_report_bytes,
)

st.set_page_config(
    page_title="فاحص النصوص | AI Text Inspector",
    page_icon="🔎", layout="wide", initial_sidebar_state="collapsed",
)

C = {"ink": "#17231F", "ink2": "#4A5A54", "ai": "#B3245C", "human": "#1E7A62",
     "warn": "#B9791B", "rule": "#E1E7E2"}
CATEGORY_COLORS = {
    "spacing": "#FFE2B8", "punct": "#FFCCCA", "typo": "#E1D6FF",
    "orth": "#F3D7ED", "format": "#CFE7FF", "convention": "#FFF1BA",
    "ai": "#C9F1E4", "rhetoric": "#BCECDD", "evidence": "#FFD7E2",
}
MODE_TEXT, MODE_FILE, MODE_BATCH = "نص مباشر", "ملف واحد", "دفعة ملفات أو ZIP"
PLOT_FONT = "IBM Plex Sans Arabic, Reem Kufi, Segoe UI, Tahoma, sans-serif"


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
def masthead():
    st_ = engine_status()
    md(f"""
    <header class="mast">
      <div>
        <h1>فاحص النصوص</h1>
        <p class="lede">الصق نصًا أو ارفع ملفًا، وسترى أين تظهر القرائن الأسلوبية للكتابة الآلية
        وأين تظهر آثار التحرير البشري، فقرةً فقرة.</p>
      </div>
      <div class="engine"><span class="ltr">AI Text Inspector · engine v{esc(st_["version"])}</span><br>
        <span class="ltr"><b>SHA-256 verified</b> · {esc(st_["sha256"][:12])}</span></div>
    </header>""")


def footer():
    st_ = engine_status()
    weights = "أوزان مخصّصة (weights.json)" if st_["custom_weights"] else "الأوزان الافتراضية الأصلية"
    md(f"""
    <footer class="foot">
      <p>المؤشر تقدير احتمالي مبني على قرائن أسلوبية، وليس إثباتًا لمن كتب النص. لا تستخدمه وحده
      في اتهام أحد أو في قرار عالي الأثر. تحقّق من أداء الكاشف باستقلال قبل الاعتماد عليه.</p>
      <p class="ltr">{esc(weights)}</p>
    </footer>""")


def empty_state(eng):
    a, b = st.columns([1.35, 1], gap="large")
    with a:
        section("هكذا يُلوَّن النص", "مثال توضيحي، وليس نتيجة تحليل")
        mk = lambda cat, t: (f'<mark style="background:{CATEGORY_COLORS[cat]}" '
                             f'title="{esc(eng.CAT_LABELS.get(cat, cat))}">{esc(t)}</mark>')
        specimen = ("يؤدي التحول الرقمي " + mk("rhetoric", "دورًا محوريًا") +
                    " في تحسين جودة البحث" + mk("punct", " ،") + mk("spacing", "  ") +
                    "غير أن " + mk("evidence", "الدراسات تؤكد") + " أن الأثر يتفاوت من حالة إلى أخرى.")
        legend = "".join(
            f'<span><i style="background:{bg}"></i>{esc(eng.CAT_LABELS[c])}</span>'
            for c, bg in CATEGORY_COLORS.items() if c in eng.CAT_LABELS)
        md(f'<div class="specimen"><p class="txt">{specimen}</p><div class="legend">{legend}</div></div>')
    with b:
        section("ماذا ستحصل عليه")
        md("""<ul class="steps">
          <li><b>درجة وحكم</b> على مسطرة من 0 إلى 100، مع نسبة الثقة.</li>
          <li><b>العوامل</b> التي رفعت الحكم نحو الكتابة الآلية أو خفّضته نحو البشرية.</li>
          <li><b>خريطة الفقرات</b> لترى أي الفقرات أكثر اشتباهًا.</li>
          <li><b>النص الملوّن</b> بالمواضع المرصودة، وتقرير HTML قابل للتنزيل.</li>
        </ul>""")


# ----------------------------------------------------------------- result pieces
def verdict_panel(rep):
    ticks = "".join(
        f'<span class="tick{" major" if i % 5 == 0 else ""}" style="right:{i*10}%"></span>'
        for i in range(0, 11))
    score = max(0, min(100, rep.score))
    md(f"""
    <section class="verdict" aria-label="النتيجة الإجمالية">
      <div class="v-score"><span class="big num" style="color:{esc(rep.color)}">{rep.score:.0f}</span>
        <span class="of">من 100</span></div>
      <div>
        <div class="v-label">حكم المحرك</div>
        <p class="v-text" style="color:{esc(rep.color)}">{esc(rep.verdict)}</p>
        <div class="ruler" role="img" aria-label="المؤشر {rep.score:.0f} من 100">
          <div class="bar"></div>{ticks}
          <div class="needle" style="right:{score:.8f}%"></div>
        </div>
        <div class="ruler-ends"><span>أسلوب بشري</span><span>أسلوب مولَّد</span></div>
      </div>
      <dl class="facts">
        <div><dt>الثقة بالحكم</dt><dd class="num">{rep.confidence*100:.0f}%<small>تقدير مبني على طول النص</small></dd></div>
        <div><dt>الكلمات</dt><dd class="num">{rep.words:,}</dd></div>
        <div><dt>الجمل</dt><dd class="num">{rep.sentences:,}</dd></div>
      </dl>
    </section>""")


def finding_category_counts(rep, engine):
    return [(engine.CAT_LABELS.get(k, k), n, CATEGORY_COLORS.get(k, '#E9EEF6'))
            for k, n in rep.count_by_cat().most_common() if k != "meta"]


def factor_bars(rep, engine):
    factors = [(engine.FEATURE_LABELS[k], v, w, d)
               for k, (v, w, d) in rep.features.items() if v > 0.005]
    factors.sort(key=lambda row: abs(row[3]), reverse=True)
    factors = factors[:14]
    if not factors:
        st.info("لم يرصد المحرك عوامل مؤثرة في هذا النص.")
        return
    peak = max(abs(r[3]) for r in factors) or 1.0
    rows = []
    for label, v, w, d in factors:
        width = abs(d) / peak * 50
        cls = "ai" if d > 0 else "hu"
        tip = f"القيمة {v:.4f} · الوزن {w:+.4f} · الأثر {d:+.4f}"
        rows.append(f'<div class="frow" title="{esc(tip)}"><span class="lab">{esc(label)}</span>'
                    f'<div class="track"><span class="fill {cls}" style="width:{width:.4f}%"></span></div>'
                    f'<span class="val num">{d:+.2f}</span></div>')
    md('<div class="fkey"><span class="h">يميل إلى الكتابة البشرية ◂</span>'
       '<span class="a">▸ يميل إلى الكتابة الآلية</span></div>'
       f'<div class="fbars">{"".join(rows)}</div>')


def category_bars(counts):
    peak = max(n for _, n, _ in counts) or 1
    rows = "".join(
        f'<div class="crow"><span>{esc(t)}</span>'
        f'<div><div class="cb" style="width:{n/peak*100:.2f}%;background:{c}"></div></div>'
        f'<span class="cn num">{n}</span></div>' for t, n, c in counts)
    md(f'<div class="cbars">{rows}</div>')


def paragraph_figure(rep):
    positions = [i + 1 for i, sc, n in rep.para_scores]
    scores = [sc for i, sc, n in rep.para_scores]
    words = [n for i, sc, n in rep.para_scores]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=positions, y=scores, name="مؤشر الفقرة",
                         marker_color=[C["ai"] if v >= 55 else C["warn"] if v >= 30 else C["human"] for v in scores],
                         customdata=words,
                         hovertemplate="الفقرة: %{x}<br>المؤشر: %{y:.0f}<br>الكلمات: %{customdata}<extra></extra>"))
    fig.add_hline(y=55, line_dash="dot", line_color="#74837D",
                  annotation_text="55", annotation_position="top right")
    fig.update_layout(height=300, margin=dict(l=18, r=18, t=12, b=30),
                      font=dict(family=PLOT_FONT, color=C["ink"], size=13),
                      xaxis=dict(title="رقم الفقرة (الأولى عند اليمين)", dtick=1 if len(positions) <= 30 else None,
                                 showgrid=False, autorange="reversed"),
                      yaxis=dict(title="درجة الفقرة", range=[0, 105], gridcolor="#E1E7E2", side="right"),
                      showlegend=False, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", bargap=0.26)
    return fig


def highlighted_paragraph(text: str, findings, chosen_cat=None):
    """Span-level visualization ONLY; uses findings as-is and does not alter input."""
    ranges = []
    for idx, f in enumerate(findings):
        if f.end <= f.start or f.cat not in CATEGORY_COLORS:
            continue
        if chosen_cat and f.cat != chosen_cat:
            continue
        a = max(0, min(len(text), f.start))
        b = max(a, min(len(text), f.end))
        if b > a:
            ranges.append((idx, a, b, f.cat))
    starts, ends = defaultdict(list), defaultdict(list)
    boundaries = {0, len(text)}
    for idx, a, b, cat in ranges:
        starts[a].append((idx, cat))
        ends[b].append(idx)
        boundaries.update((a, b))
    active = {}
    points = sorted(boundaries)
    chunks = []
    for a, b in zip(points[:-1], points[1:]):
        for idx in ends[a]:
            active.pop(idx, None)
        for idx, cat in starts[a]:
            active[idx] = cat
        piece = esc(text[a:b])
        if active:
            # Tkinter category tags render in creation priority order (not finding order).
            cat = max(active.values(), key=lambda name: list(CATEGORY_COLORS).index(name))
            piece = f'<mark style="background:{CATEGORY_COLORS[cat]}">{piece}</mark>'
        chunks.append(piece)
    return "".join(chunks) or esc(text)


def marked_text_view(rep, filtered_cat=None, selected_para=None):
    grouped = defaultdict(list)
    for f in rep.findings:
        if f.para >= 0:
            grouped[f.para].append(f)
    indices = range(len(rep.texts)) if selected_para is None else [selected_para]
    return '<div class="viewer">' + "".join(
        f'<div class="pg"><span class="no">{i+1:02d}</span>'
        f'<div class="body" dir="auto">{highlighted_paragraph(rep.texts[i], grouped[i], filtered_cat)}</div></div>'
        for i in indices) + '</div>'


# ----------------------------------------------------------------- report
def report_dashboard(rep):
    eng = get_engine()
    verdict_panel(rep)
    md('<p class="caveat">المؤشر تقدير احتمالي مبني على قرائن أسلوبية، وليس إثباتًا لجهة كتابة النص.</p>')
    tabs = st.tabs(["نظرة عامة", "العوامل والأسباب", "خريطة الفقرات", "المواضع المرصودة", "النص الملوّن", "تفاصيل الملف"])

    with tabs[0]:
        a, b = st.columns([1.5, 1], gap="large")
        with a:
            section("العوامل الأكثر تأثيرًا", "الأثر كما حسبه المحرك")
            factor_bars(rep, eng)
        with b:
            section("الفئات المرصودة")
            counts = finding_category_counts(rep, eng)
            if counts:
                category_bars(counts)
            else:
                st.info("لا توجد مواضع مصنفة في الفئات المعروضة.")
            section("الملخص الإحصائي")
            table(["المقياس", "القيمة"], [(k, v) for k, v in rep.stats.items()], num_cols={1})

    with tabs[1]:
        a, b = st.columns([1.15, 1], gap="large")
        with a:
            section("المساهمات التفصيلية", "كما حسبها المحرك")
            rows = [(eng.FEATURE_LABELS[k], v, w, c)
                    for k, (v, w, c) in sorted(rep.features.items(), key=lambda kv: -abs(kv[1][2])) if v > 0.005]
            table(["العامل", "القيمة (0–1)", "الوزن", "الأثر"], rows, num_cols={1, 2, 3}, max_h=520)
        with b:
            section("الأسباب")
            if rep.reasons:
                for line in rep.reasons:
                    md(f'<div class="note boxed">{esc(line)}</div>')
            else:
                st.info("لم يُرجع المحرك أسبابًا إضافية.")

    with tabs[2]:
        section("مؤشر الاشتباه حسب الفقرة")
        if rep.para_scores:
            st.plotly_chart(paragraph_figure(rep), use_container_width=True, config={"displayModeBar": False})
            table(["الفقرة", "الدرجة", "الكلمات", "مطلع الفقرة"],
                  [(i + 1, score, n, rep.texts[i][:155]) for i, score, n in rep.para_scores],
                  num_cols={0, 1, 2}, wrap_cols={3}, max_h=360)
        else:
            st.info("لم يحسب المحرك درجة لفقرات هذا النص.")

    with tabs[3]:
        section("المواضع المرصودة")
        chosen = st.selectbox("تصفية حسب الفئة", ["الكل"] + list(eng.CAT_LABELS.values()), key="finding_category")
        rows = [(eng.CAT_LABELS[f.cat], f.label, f.para + 1 if f.para >= 0 else "—", f.text, f.note)
                for f in rep.findings if f.kind != "meta" and (chosen == "الكل" or chosen == eng.CAT_LABELS[f.cat])]
        md(f'<p class="note">عدد المواضع المطابقة: <bdi class="num">{len(rows):,}</bdi></p>')
        if rows:
            table(["الفئة", "النوع", "الفقرة", "المقتطف", "ملاحظة"], rows,
                  num_cols={2}, wrap_cols={3, 4}, max_h=560)
        else:
            st.info("لا مواضع مطابقة لهذه الفئة.")

    with tabs[4]:
        section("مواضع القرائن في النص")
        c1, c2 = st.columns([1, 2])
        with c1:
            cat_lbl = st.selectbox("الفئة الملوّنة", ["كل الفئات"] + list(eng.CAT_LABELS.values()), key="mark_category")
        with c2:
            opts = ["كامل المستند"] + [f"الفقرة {i+1}" for i in range(len(rep.texts))]
            para_lbl = st.selectbox("انتقل إلى", opts, key="mark_para")
        cat = next((k for k, v in eng.CAT_LABELS.items() if v == cat_lbl), None)
        pidx = None if para_lbl == "كامل المستند" else opts.index(para_lbl) - 1
        md(marked_text_view(rep, cat, pidx))
        legend = "".join(
            f'<span><i style="background:{bg}"></i>{esc(eng.CAT_LABELS[c])}</span>'
            for c, bg in CATEGORY_COLORS.items() if c in eng.CAT_LABELS)
        md(f'<div class="legend" style="border-top:0">{legend}</div>')

    with tabs[5]:
        left, right = st.columns([1, 1], gap="large")
        with left:
            section("معلومات الفحص")
            table(["الحقل", "القيمة"],
                  [("المصدر", rep.source), ("صيغة Word", "نعم" if rep.is_docx else "لا"),
                   ("إجمالي المواضع", len(rep.findings)), ("المحرك", eng.VERSION)]
                  + [(k, v) for k, v in rep.stats.items()], num_cols={1})
        with right:
            section("البيانات الوصفية لمستند Word")
            if rep.meta:
                table(["الحقل", "القيمة"], [(k, v) for k, v in rep.meta.items()], wrap_cols={1})
            else:
                st.info("لا توجد بيانات Word وصفية لهذا التحليل.")
            section("آثار التحرير المرصودة")
            traces = [f for f in rep.findings if f.kind == "meta"]
            if traces:
                for f in traces:
                    md(f'<div class="note boxed" dir="auto">{esc(f.text)} — {esc(f.note)}</div>')
            else:
                md('<p class="note">لم يرصد المحرك آثار تحرير.</p>')

    section("حفظ النتيجة")
    st.download_button("تنزيل التقرير (HTML)", data=html_report_bytes(rep),
                       file_name="AI_Text_Inspector_Report.html", mime="text/html",
                       type="primary", key="html_export")
    md('<p class="note">يُنشأ التقرير باستدعاء report_to_html من المحرك الأصلي مباشرة.</p>')


def batch_dashboard(results):
    good = [(n, r) for n, r in results if not isinstance(r, Exception)]
    errors = [(n, r) for n, r in results if isinstance(r, Exception)]
    section("نتيجة الدفعة")
    md(f"""<dl class="facts" style="border-top:0;padding-top:0;margin-top:0">
      <div><dt>الملفات الواردة</dt><dd class="num">{len(results)}</dd></div>
      <div><dt>تم تحليلها</dt><dd class="num" style="color:{C['human']}">{len(good)}</dd></div>
      <div><dt>تعذّر تحليلها</dt><dd class="num" style="color:{C['ai']}">{len(errors)}</dd></div></dl>""")
    rows = []
    for name, r in results:
        if isinstance(r, Exception):
            rows.append((name, "—", "—", str(r), "—", "—", "—"))
        else:
            rows.append((name, r.words, f"{r.score:.0f}", r.verdict,
                         r.stats["أخطاء مرجّحة لكل 1000 كلمة"],
                         f"{r.fvals['fmt_incons']:.2f}", f"{r.fvals['meta_human']:.2f}"))
    md('<div style="height:14px"></div>')
    table(["الملف", "الكلمات", "مؤشر AI", "الحكم", "أخطاء لكل 1000", "عدم اتساق التنسيق", "آثار التحرير"],
          rows, num_cols={1, 2, 4, 5, 6}, wrap_cols={0, 3}, max_h=460)
    if errors:
        with st.expander("تفاصيل الملفات التي تعذّر تحليلها"):
            for name, r in errors:
                st.error(f"{name}: {r}")
    md('<div style="height:14px"></div>')
    st.download_button("تنزيل نتائج الدفعة (CSV)", data=batch_csv_bytes(results),
                       file_name="AI_Inspector_Batch_Results.csv", mime="text/csv", type="primary")
    if good:
        section("التفاصيل الكاملة لملف")
        idx = st.selectbox("اختر ملفًا", options=range(len(good)),
                           format_func=lambda i: good[i][0], key="batch_choice")
        report_dashboard(good[idx][1])


# ----------------------------------------------------------------- input desk
def reset_results():
    st.session_state["current_report"] = None
    st.session_state["batch_reports"] = None
    st.session_state["editor_origin"] = None
    st.session_state.pop("original_file_text", None)


def input_desk():
    has_results = (st.session_state.get("current_report") is not None
                   or st.session_state.get("batch_reports") is not None)
    selection = st.radio("طريقة الإدخال", [MODE_TEXT, MODE_FILE, MODE_BATCH],
                         horizontal=True, key="input_mode")
    md('<div style="height:10px"></div>')
    if selection == MODE_TEXT:
        st.text_area("النص", height=120 if has_results else 230, key="pasted_input",
                     placeholder="الصق النص العربي أو الإنجليزي هنا. يُحلَّل كما هو دون تعديل.")
        if st.button("حلّل النص", type="primary"):
            s = st.session_state.get("pasted_input", "")
            if not s.strip():
                st.warning("أدخل نصًا أولًا.")
            else:
                try:
                    st.session_state["current_report"] = analyze_pasted(s)
                    st.session_state["batch_reports"] = None
                    st.session_state["editor_origin"] = None
                except Exception as ex:
                    st.error(str(ex))
    elif selection == MODE_FILE:
        uploaded = st.file_uploader("ملف Word أو نص", type=["docx", "txt", "md", "text"], key="one_file")
        md('<p class="note">يُقرأ ملف DOCX بتنسيقاته وبياناته عبر القارئ الأصلي.</p>')
        if st.button("حلّل الملف", type="primary"):
            if uploaded is None:
                st.warning("اختر ملفًا أولًا.")
            else:
                try:
                    data = uploaded.getvalue()
                    report = analyze_uploaded_bytes(uploaded.name, data)
                    st.session_state["current_report"] = report
                    st.session_state["batch_reports"] = None
                    st.session_state["editor_origin"] = (uploaded.name, data, report.is_docx)
                    st.session_state["editable_text"] = "\n".join(report.texts)
                    st.session_state["original_file_text"] = "\n".join(report.texts)
                except Exception as ex:
                    st.error(str(ex))
        origin = st.session_state.get("editor_origin")
        if origin and st.session_state.get("current_report") is not None:
            with st.expander("عدّل النص المستخرج وأعد الفحص"):
                st.text_area("النص المستخرج (قابل للتعديل)", height=200, key="editable_text")
                md('<p class="note">عند تعديل نص DOCX يُفحص كنص ملصق، تمامًا كما في البرنامج الأصلي، '
                   'وتُفقد قرائن تنسيق Word.</p>')
                if st.button("أعد الفحص", key="rescan_edited"):
                    text = st.session_state.get("editable_text", "")
                    if not text.strip():
                        st.warning("النص فارغ.")
                    else:
                        try:
                            if origin[2] and text == st.session_state.get("original_file_text", ""):
                                r = analyze_uploaded_bytes(origin[0], origin[1])
                            else:
                                r = analyze_pasted(text)
                            st.session_state["current_report"] = r
                            st.session_state["batch_reports"] = None
                        except Exception as ex:
                            st.error(str(ex))
    else:
        multiple = st.file_uploader("ملفات متعددة أو أرشيف ZIP", accept_multiple_files=True,
                                    type=["docx", "txt", "md", "text", "zip"], key="multi_files")
        md('<p class="note">ارفع أرشيف ZIP لمجلد كامل. يُحلَّل كل ملف بنفس طريقة الملف المنفرد.</p>')
        if st.button("حلّل الدفعة", type="primary"):
            if not multiple:
                st.warning("ارفع ملفًا واحدًا على الأقل.")
            else:
                try:
                    results = analyze_batch_inputs((x.name, x.getvalue()) for x in multiple)
                    if results:
                        st.session_state["batch_reports"] = results
                        st.session_state["current_report"] = None
                        st.session_state["editor_origin"] = None
                    else:
                        st.warning("لم يتضمن الرفع ملفات مدعومة.")
                except Exception as ex:
                    st.error(str(ex))


def main():
    md(f"<style>{_css()}</style>")
    try:
        get_engine()
    except Exception as e:
        st.error(f"توقف التشغيل لحماية سلامة محرك التحليل: {e}")
        st.stop()
    masthead()
    input_desk()
    batch = st.session_state.get("batch_reports")
    rep = st.session_state.get("current_report")
    if batch is not None or rep is not None:
        md('<div style="height:22px"></div>')
        src, clear = st.columns([5, 1], vertical_alignment="center")
        with src:
            if rep is not None and batch is None:
                md(f'<p class="note" style="margin:0">المصدر: <bdi>{esc(rep.source)}</bdi> · المحرك v{esc(get_engine().VERSION)}</p>')
        with clear:
            if st.button("امسح النتائج", key="clear_results"):
                reset_results()
                st.rerun()
    if batch is not None:
        batch_dashboard(batch)
    elif rep is not None:
        report_dashboard(rep)
    else:
        empty_state(get_engine())
    footer()


if __name__ == "__main__":
    main()
