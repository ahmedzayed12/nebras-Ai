# -*- coding: utf-8 -*-
"""Re-calibrate academic_signals on YOUR labelled texts.

    python tools/calibrate_academic.py  <human_folder>  <ai_folder>

Each folder holds .txt files (one document each, ideally >= 1500 words, English).
Prints, for every feature, the human median / 95th / 99th percentile / max and the AI minimum / median,
then the detector's score on both sets (old engine vs. new layers) so you can see the false-positive rate
before changing an anchor in academic_signals.SPEC.  Rule used for the shipped anchors:
z=0 at the human 99th percentile, z=1 near the AI minimum.
"""
import glob
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import academic_signals as acad  # noqa: E402
import backend_adapter as b      # noqa: E402


def q(v, p):
    v = sorted(v)
    return v[int(round(p * (len(v) - 1)))]


def main(hdir, adir):
    sets = {}
    for name, d in (("human", hdir), ("ai", adir)):
        files = sorted(glob.glob(os.path.join(d, "*.txt")))
        if not files:
            sys.exit("no .txt files in %s" % d)
        sets[name] = [open(f, encoding="utf-8", errors="ignore").read() for f in files]
    feats = {k: [acad.measure(t.split("\n")) for t in v] for k, v in sets.items()}
    for key in ("lex", "tri", "long_w", "first"):
        h = [x[key] for x in feats["human"]]
        a = [x[key] for x in feats["ai"]]
        print("%-7s human med=%6.2f p95=%6.2f p99=%6.2f max=%6.2f | AI min=%6.2f med=%6.2f"
              % (key, st.median(h), q(h, .95), q(h, .99), max(h), min(a), st.median(a)))
    for name, texts in sets.items():
        old, new = [], []
        for t in texts:
            r = b.analyze_pasted(t)
            old.append(r.score_engine)
            new.append(r.score)
        print("%-5s n=%d  engine v2.1 >=52: %d   with layers >=52: %d   (median %.1f -> %.1f)"
              % (name, len(texts), sum(x >= 52 for x in old), sum(x >= 52 for x in new), st.median(old), st.median(new)))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
