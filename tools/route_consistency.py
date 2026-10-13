# -*- coding: utf-8 -*-
"""Same document, three routes: does Nebras give the same answer?    python tools/route_consistency.py file.docx [...]

DOCX route (full file evidence) / TXT route (paragraphs of the DOCX as plain text) / PDF route (the DOCX converted by
LibreOffice, then read by pdf_input). For every file it prints the three indices and the FEATURES THAT MOVED MOST between
routes, i.e. the ones that depend on how the text was extracted rather than on how it was written. A gap smaller than
GAP_OK is expected (the PDF has no edit history / hand-formatting evidence); a larger one means an extraction-sensitive
feature needs a quality gate. Needs LibreOffice (soffice) and pymupdf for the PDF route; missing parts are skipped."""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backend_adapter as b  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
GAP_OK = 10.0


def _to_pdf(path: str, out: str):
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        return None
    subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", out, path], capture_output=True, timeout=280)
    pdf = os.path.join(out, os.path.splitext(os.path.basename(path))[0] + ".pdf")
    return pdf if os.path.exists(pdf) else None


def _feat(r):
    return {k: c for k, (v, w, c) in (getattr(r, "features", None) or {}).items()}


def compare(path: str) -> None:
    name = os.path.basename(path)
    data = open(path, "rb").read()
    routes = {}
    docx = b.analyze_uploaded_bytes(name, data)
    routes["DOCX"] = docx
    routes["TXT "] = b.analyze_text_guarded("\n".join(t for t in docx.texts if t.strip()))
    with tempfile.TemporaryDirectory() as tmp:
        pdf = _to_pdf(path, tmp)
        if pdf:
            try:
                routes["PDF "] = b.analyze_uploaded_bytes(os.path.basename(pdf), open(pdf, "rb").read())
            except Exception as exc:
                print("  (PDF route skipped: %s)" % exc)
        else:
            print("  (PDF route skipped: LibreOffice not found)")
    print("=" * 78)
    print(name)
    for tag, r in routes.items():
        print("  %s  engine %5.1f   final %5.1f   words %6d   %s" % (tag, getattr(r, "score_engine", r.score), r.score, r.words, r.verdict[:34]))
    scores = [r.score for r in routes.values()]
    gap = max(scores) - min(scores)
    print("  gap between routes: %.1f  %s" % (gap, "OK" if gap <= GAP_OK else "<< EXTRACTION-SENSITIVE: see features below"))
    base = _feat(docx)
    for tag, r in list(routes.items())[1:]:
        f = _feat(r)
        moved = sorted(((k, f.get(k, 0.0) - base.get(k, 0.0)) for k in set(f) | set(base)), key=lambda kv: -abs(kv[1]))[:5]
        print("  features moving DOCX -> %s: %s" % (tag.strip(), ", ".join("%s %+.2f" % kv for kv in moved if abs(kv[1]) >= 0.15) or "none"))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    for p in sys.argv[1:]:
        compare(p)
