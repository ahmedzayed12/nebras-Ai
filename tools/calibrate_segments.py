# -*- coding: utf-8 -*-
"""Re-measure the human anchors of segment_signals on YOUR human texts (never on generated ones).

    python tools/calibrate_segments.py  folder_with_human_txt_files  [folder_with_generated_txt_files]

Prints, for every wording habit, the human 75th / 97th percentile of single paragraphs (>= 40 words) next to the
values in segment_signals.ANCHORS, and the human 90th percentile of the combined paragraph score (X0).
With a second folder it also prints how many generated paragraphs pass X0 -- for information only.
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import segment_signals as sg  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def paragraphs(folder):
    for f in sorted(glob.glob(os.path.join(folder, "*.txt"))):
        for p in open(f, encoding="utf-8", errors="ignore").read().split("\n\n"):
            p = " ".join(p.split())
            if len(p.split()) >= 40:
                yield p


def q(v, p):
    v = sorted(v)
    return v[int(p * (len(v) - 1))] if v else float("nan")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)
    H = [sg.habits(p) for p in paragraphs(sys.argv[1])]
    print("human paragraphs: %d" % len(H))
    for k, (w, lo, hi) in sg.ANCHORS.items():
        print("  %-9s p75 %6.2f  p97 %6.2f   (in use: %.2f / %.2f)" % (k, q([h[k] for h in H], .75), q([h[k] for h in H], .97), lo, hi))
    xs = [sg.wording_score(h) for h in H]
    print("  combined score  human p90 %.2f  p97 %.2f  (X0 in use: %.2f)" % (q(xs, .90), q(xs, .97), sg.X0))
    if len(sys.argv) > 2:
        G = [sg.wording_score(sg.habits(p)) for p in paragraphs(sys.argv[2])]
        print("generated paragraphs: %d, above X0: %.0f%%" % (len(G), 100.0 * sum(x > sg.X0 for x in G) / max(1, len(G))))
