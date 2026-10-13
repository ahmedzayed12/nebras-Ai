# -*- coding: utf-8 -*-
"""Analyse one or more files from the command line (no browser):   python tools/check_file.py file1.docx [file2.pdf ...]"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backend_adapter as b  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
for path in sys.argv[1:]:
    with open(path, "rb") as fh:
        r = b.analyze_uploaded_bytes(os.path.basename(path), fh.read())
    ps = list(r.para_scores)
    tot = sum(n for _, _, n in ps) or 1
    share = 100.0 * sum(n for _, sc, n in ps if sc >= 55) / tot
    print("=" * 70)
    print(os.path.basename(path))
    print("  engine v2.1 : %5.1f%%" % getattr(r, "score_engine", r.score))
    print("  final index : %5.1f%%   %s" % (r.score, r.verdict))
    print("  suspicious text share : %.0f%%   words: %d" % (share, r.words))
    for line in r.reasons:
        print("  " + line)
if len(sys.argv) < 2:
    print(__doc__)
