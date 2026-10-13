# -*- coding: utf-8 -*-
"""Check / re-calibrate slide_mode on YOUR presentations.

    python tools/calibrate_slides.py  <human_pdf_folder>  <ai_pdf_folder>

Prints, for every deck: whether it was recognised as a deck, each template habit (share of slides), the layer score
and the final index. Adjust slide_mode.SPEC anchors so that no human deck reaches 3 fired habits.
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import backend_adapter as b  # noqa: E402
import slide_mode as sm      # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

for label, folder in zip(("human", "ai"), sys.argv[1:3]):
    print("==", label)
    for f in sorted(glob.glob(os.path.join(folder, "*.pdf"))):
        data = open(f, "rb").read()
        meta, slides = sm.read_pdf(data)
        layer = sm.analyze(meta, slides)
        r = b.analyze_uploaded_bytes(os.path.basename(f), data)
        habits = " ".join("%s=%.2f" % (s.key, s.value) for s in layer.signals)
        print("  %-30s deck=%-5s fired=%d layer=%s final=%5.1f  %s" % (os.path.basename(f)[:30], layer.is_deck, layer.fired,
              "-" if layer.score is None else "%.0f" % layer.score, r.score, habits))
if len(sys.argv) < 3:
    print(__doc__)
