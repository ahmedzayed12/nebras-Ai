# -*- coding: utf-8 -*-
"""Stage-by-stage diagnosis of ONE file (no browser, no tuning):   python tools/diagnose_file.py file.docx [more files]

Shows where the final index comes from: engine v2.1 -> file-evidence recalibration -> technical layer -> academic layer
-> paper-trail rule -> paragraph map, and which words/habits fed the academic layer (topic words excluded are listed).
Use it to ask "why this number?" for any text, human or generated. It never changes a score."""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import backend_adapter as b  # noqa: E402
import academic_signals as acad  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def diagnose(path: str) -> None:
    with open(path, "rb") as fh:
        r = b.analyze_uploaded_bytes(os.path.basename(path), fh.read())
    ps = list(r.para_scores)
    tot = sum(n for _, _, n in ps) or 1
    share = 100.0 * sum(n for _, sc, n in ps if sc >= 55) / tot
    print("=" * 78)
    print(os.path.basename(path), "| words:", r.words)
    print("  engine v2.1 (unchanged)      : %5.1f" % getattr(r, "score_engine", r.score))
    print("  final index                  : %5.1f   %s" % (b.primary_score(r), getattr(r, "final_verdict", r.verdict)))
    print("  diagnostic index             : %5.1f" % r.score)
    print("  pooled / local index         : %5.1f / %5.1f" % (
        getattr(r, "score_pooled", r.score), getattr(r, "score_local", r.score)))
    print("  suspected text share (map)   : %.0f%%   paragraphs scored: %d" % (share, len(ps)))
    if getattr(r, "file_shift", None):
        mode = "localized (word-weighted mean)" if hasattr(r, "file_shifts") else "uniform"
        print("  file evidence applied to map : %.2f log-odds, %s" % (r.file_shift, mode))
    for kind, (stored, recovered) in getattr(r, "style_storage_recovered", {}).items():
        print("  complete wording findings    : %s %d -> %d (before evidence filtering)" % (kind, stored, recovered))
    print("  stages:")
    for stage in getattr(r, "score_stages", []):
        local = stage.get("paragraph_mean")
        suffix = " | paragraph mean %.2f" % local if local is not None else ""
        wording = stage.get("wording_only")
        if wording is not None:
            suffix += " | wording-only %.2f" % wording
        print("    %-35s %6.2f (%+.2f points)%s" % (stage["label"], stage["score"], stage["delta"], suffix))
    seg = getattr(r, "segmentation", None)
    if seg is not None and seg.units:
        total = sum(u.words for u in seg.units) or 1
        print("  final wording/slip audit     : wording log-odds %.3f; slip discount %.3f; technical units %d"
              % (sum(u.wording_L * u.words for u in seg.units) / total,
                 sum(u.slip_discount * u.words for u in seg.units) / total,
                 sum(u.technical_L is not None for u in seg.units)))
    for line in r.reasons:
        if line[:1] in "▲◆■●⚠":
            print("    " + line)
    heads = [q.text for q in (getattr(r, "doc_paras", None) or []) if getattr(q, "role", "") == "heading"]
    topic = acad.topic_terms(heads)
    texts = [t for t in r.texts if t and t.strip()]
    m_all, m_top = acad.measure(texts), acad.measure(texts, topic)
    print("  academic habits (per 1000 words): lexicon %.1f -> %.1f after removing %d topic stems | triads %.1f | long words %.1f%% | I/we %.2f"
          % (m_all["lex"], m_top["lex"], len(topic), m_all["tri"], m_all["long_w"], m_all["first"]))
    hits = collections.Counter(acad._stem(h) for h in acad._LEX.findall("\n".join(texts)))
    print("  most frequent lexicon stems   :", ", ".join("%s×%d%s" % (k, v, "*" if k in topic else "") for k, v in hits.most_common(8)),
          " (* = topic word, not counted)")
    fr = getattr(r, "friction", None)
    if fr is not None and fr.applied:
        print("  paper-trail rule: %d slips / %d words (%.2f per 1000); floor %s; weight %.2f"
              % (fr.slips, fr.words, fr.rate, fr.floor, fr.weight))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
    for p in sys.argv[1:]:
        diagnose(p)
