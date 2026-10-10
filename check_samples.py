# -*- coding: utf-8 -*-
"""اختبار انحدار سريع:  python check_samples.py
يفحص نصوص test_samples (مولَّدة) ويطبع الدرجة لكل منها. أضف نصوصك البشرية في test_samples/human لرؤية الإنذارات الكاذبة."""
import os, sys, glob, importlib.machinery, importlib.util
here = os.path.dirname(os.path.abspath(__file__))
loader = importlib.machinery.SourceFileLoader("insp", os.path.join(here, "AI_Text_Inspector.pyw"))
spec = importlib.util.spec_from_loader("insp", loader); m = importlib.util.module_from_spec(spec)
sys.modules["insp"] = m; loader.exec_module(m)
for folder in ("ai", "ai_hard", "human"):
    files = sorted(glob.glob(os.path.join(here, "test_samples", folder, "*.txt")))
    if not files: continue
    print("== %s" % folder)
    for f in files:
        r = m.analyze_text(open(f, encoding="utf-8").read())
        print("  %-22s %5.0f   %s" % (os.path.basename(f), r.score, r.verdict))
