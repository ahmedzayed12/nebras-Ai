# -*- coding: utf-8 -*-
"""Transport adapter. Never calculates or changes an AI Text Inspector score.

Every analysis is delegated to the unmodified AI_Text_Inspector.pyw v2.1.
The SHA-256 guard catches accidental edits to the analysis engine.

The ONE thing done before the engine sees a text is math_guard: equations (LaTeX, Word OMML,
formula-only paragraphs) are removed, because the prose-only engine reads them as human typos
and fake sentence-length variance and under-reports AI text (measured 44 -> 5 on one manuscript).
The removal is recorded on `report.math` (a math_guard.MathMap) and shown to the user.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.machinery
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
from functools import lru_cache
from typing import Iterable
import zipfile

ROOT = Path(__file__).resolve().parent
ENGINE_PATH = ROOT / "AI_Text_Inspector.pyw"
ENGINE_SHA256 = "3692e8f4f4485460e1eb3f448a9f09f58c50685ceeb8ce49bb8921fad24a57f8"
SUPPORTED = {".docx", ".txt", ".md", ".text", ".pdf"}
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_BATCH_FILES = 250
MAX_BATCH_BYTES = 120 * 1024 * 1024


@lru_cache(maxsize=1)
def get_engine():
    actual = hashlib.sha256(ENGINE_PATH.read_bytes()).hexdigest()
    if actual != ENGINE_SHA256:
        raise RuntimeError(
            "تغيّرت بصمة محرك التحليل الأصلي. توقف التحليل لحماية تطابق النتائج. "
            "استعد ملف AI_Text_Inspector.pyw الأصلي (v2.1) من الحزمة."
        )
    name = "ai_text_inspector_v21_unchanged"
    if name in sys.modules:
        return sys.modules[name]
    loader = importlib.machinery.SourceFileLoader(name, str(ENGINE_PATH))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None:
        raise RuntimeError("تعذّر تحميل المحرك الأصلي.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses uses sys.modules while classes are created
    try:
        loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def filename_only(name: str) -> str:
    # Uploaded files / ZIP entries must never dictate an output directory.
    name = str(name).replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if c.isprintable() and c not in "/\\\x00")
    if not name or name in {".", ".."}:
        raise ValueError("اسم الملف غير صالح.")
    return name


def _attach(report, mm):
    report.math = mm
    return _enhance(report)


def _enhance(report):
    """Technical-paper layer (tech_signals). It can only RAISE the engine score, and only when several
    independent stylistic habits of LLM-drafted scientific prose converge. Score, verdict, paragraph scores
    (which drive the PDF shading) are updated together so every report shows the same percentage."""
    try:
        import tech_signals as ts
    except Exception:
        return report
    report.score_engine = float(report.score)
    report.tech = None
    texts = [t for t in report.texts if t and t.strip() and not ts._REFLINE.match(t)]
    layer = ts.analyze(texts)
    report.tech = layer
    fused = ts.fuse(report.score_engine, layer)
    if fused <= report.score_engine + 0.5:
        return report
    eng = get_engine()
    report.score = float(fused)
    report.verdict, report.color = eng.make_verdict(report.score, report.words)
    w = 0.9 * layer.confidence
    new_ps = []
    for i, sc, n in report.para_scores:
        tp = ts.para_score(report.texts[i]) if 0 <= i < len(report.texts) else None
        if tp is not None and tp > sc:
            sc = round(sc + w * (tp - sc), 0)
        new_ps.append((i, sc, n))
    report.para_scores = new_ps
    fired = [x for x in layer.signals if x.z >= 0.35 and x.key != "friction"]
    lines = ["▲ طبقة الأوراق التقنية (مستقلة عن المحرك) رفعت المؤشر من %.0f إلى %.0f لاجتماع بصمات أسلوبية:" % (report.score_engine, report.score)]
    for x in fired:
        lines.append("   +%.1f  %s (%.3g %s)" % (x.weight * x.z, x.label, x.value, x.unit))
    report.reasons = list(report.reasons) + lines
    return report


def analyze_text_guarded(text: str, source: str = "نص ملصق"):
    """Text route (paste / TXT / MD / PDF text): equations out, then the unchanged engine."""
    import math_guard
    clean, mm = math_guard.sanitize_text(text)
    if not mm.active:                       # no maths: byte-for-byte the original route
        return _attach(get_engine().analyze_text(text, source=source), mm)
    return _attach(get_engine().analyze_text(clean, source=source), mm)


def analyze_docx_guarded(path: str, content: bytes):
    """DOCX route: engine's own parser (keeps font/format/metadata evidence), maths removed afterwards."""
    import math_guard
    eng = get_engine()
    paras, meta = eng.parse_docx(path)
    flags = math_guard.omml_flags(content, len(paras))
    clean, mm = math_guard.sanitize_docx_paras(paras, flags)
    if not mm.active:
        return _attach(eng.analyze_docx(path), mm)
    return _attach(eng.core_analyze(clean, meta, os.path.basename(path)), mm)


def analyze_pasted(text: str):
    """The pasted-text route. Identical to the original engine route unless the text contains maths."""
    return analyze_text_guarded(text)


def analyze_uploaded_bytes(filename: str, content: bytes):
    """Use the original engine for text and DOCX; import PDF through local text/OCR extraction."""
    name = filename_only(filename)
    if Path(name).suffix.lower() not in SUPPORTED:
        raise ValueError("الامتدادات المدعومة هي PDF / DOCX / TXT / MD / TEXT. ملف DOC القديم غير مدعوم.")
    if not content:
        raise ValueError("الملف فارغ.")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("حجم الملف يتجاوز حد الرفع في الواجهة (25 MB).")
    if Path(name).suffix.lower() == ".pdf":
        from pdf_input import extract_pdf_text
        # The original engine always sees plain extracted text; no new AI
        # scoring algorithm or PDF-specific thresholds are introduced.
        return analyze_text_guarded(extract_pdf_text(content), source=name)
    with tempfile.TemporaryDirectory(prefix="ai_inspector_") as tmp:
        path = Path(tmp) / name
        path.write_bytes(content)
        if path.suffix.lower() == ".docx":
            return analyze_docx_guarded(str(path), content)
        if path.suffix.lower() in (".txt", ".md", ".text"):
            return analyze_text_guarded(get_engine()._read_txt(str(path)), source=name)
        report = get_engine().analyze_file(str(path))
        if report is None:
            raise ValueError("تعذّر تحليل هذا النوع من الملفات.")
        return report


def analyze_batch(uploaded: Iterable[tuple[str, bytes]]):
    """Like original GUI's batch: keep successful reports + per-file errors."""
    results = []
    total = 0
    for n, (filename, content) in enumerate(uploaded):
        if n >= MAX_BATCH_FILES:
            raise ValueError("الحد الأقصى للدفعة 250 ملفًا.")
        total += len(content)
        if total > MAX_BATCH_BYTES:
            raise ValueError("إجمالي حجم الدفعة يتجاوز 120 MB.")
        try:
            result = analyze_uploaded_bytes(filename, content)
        except Exception as exc:
            result = exc
        results.append((filename, result))
    return results


def expand_uploaded_inputs(files: Iterable[tuple[str, bytes]]):
    """Accept individual documents or ZIP folder uploads without extracting to disk."""
    total = 0
    number = 0
    for original_name, data in files:
        if Path(original_name).suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for entry in archive.infolist():
                        if entry.is_dir() or entry.filename.startswith("__MACOSX/"):
                            continue
                        base = filename_only(entry.filename)
                        if base.startswith("~$") or Path(base).suffix.lower() not in SUPPORTED:
                            continue
                        number += 1
                        total += entry.file_size
                        if number > MAX_BATCH_FILES or total > MAX_BATCH_BYTES or entry.file_size > MAX_FILE_BYTES:
                            raise ValueError("تجاوزت الملفات حدود الحماية: 250 ملفًا، 25 MB لكل ملف، 120 MB إجماليًا.")
                        label = f"{filename_only(original_name)} / {entry.filename}"
                        yield label, archive.read(entry)
            except zipfile.BadZipFile as exc:
                raise ValueError(f"الملف ZIP غير صالح: {original_name}") from exc
        else:
            number += 1
            total += len(data)
            if number > MAX_BATCH_FILES or total > MAX_BATCH_BYTES:
                raise ValueError("تجاوزت الملفات حدود الحماية: 250 ملفًا، 120 MB إجماليًا.")
            if Path(original_name).suffix.lower() in SUPPORTED:
                yield filename_only(original_name), data


def analyze_batch_inputs(files: Iterable[tuple[str, bytes]]):
    """ZIP labels are for display; the original analyzer receives the real basename."""
    results = []
    for label, data in expand_uploaded_inputs(files):
        try:
            result = analyze_uploaded_bytes(filename_only(label), data)
        except Exception as exc:
            result = exc
        results.append((label, result))
    return results


def batch_csv_bytes(results) -> bytes:
    """Original Tkinter GUI CSV columns, ordering, and values (UTF-8 BOM)."""
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["file", "words", "ai_score", "verdict", "errors_per_1000", "format_inconsistency", "human_edit_traces",
                     "math_items_excluded"])
    for name, report in results:
        if isinstance(report, Exception):
            row = (name, "-", "-", "خطأ: %s" % report, "-", "-", "-", "-")
        else:
            row = (name, report.words, "%.0f" % report.score, report.verdict,
                   report.stats["أخطاء مرجّحة لكل 1000 كلمة"],
                   "%.2f" % report.fvals["fmt_incons"], "%.2f" % report.fvals["meta_human"],
                   getattr(getattr(report, "math", None), "stats", None).found
                   if getattr(report, "math", None) else 0)
        writer.writerow(row)
    return out.getvalue().encode("utf-8-sig")


def html_report_bytes(report) -> bytes:
    return get_engine().report_to_html(report).encode("utf-8")


def engine_status():
    eng = get_engine()
    weights = eng.load_weights()
    digest = hashlib.sha256(
        str(sorted(weights.items())).encode("utf-8")
    ).hexdigest()
    return {"version": eng.VERSION, "sha256": ENGINE_SHA256, "weights_fingerprint": digest,
            "custom_weights": os.path.exists(eng.WEIGHTS_FILE)}
