"""Build the external expository-genre manifest for the v3.2 head; never download.

python tools/build_genre_benchmark.py --nltk-data /path/to/nltk_data --wells /path/to/A-Short-History-of-the-World_35461 \
    --vanloon /path/to/The-Story-of-Mankind_754 --output genre.json

Human: pre-2020 published prose at pinned commits (Wells 1922, van Loon 1921,
Brown corpus 1961, ABC news and State of the Union from NLTK). Generated:
tests/fixtures/expository_generated.json (assistant-written essays). Each text
also yields a short 150-300 word variant in the same group. Splits are by
source group via SHA-256. No submitted user document is read.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile

NLTK_COMMIT = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"
WELLS_COMMIT = "f2ea8e6bca83b40e7446f8478b292359d2f6e5d6"
VANLOON_COMMIT = "77c08c9c4e69c3902130d968c5585df55bf913ab"
GENERATED = Path(__file__).resolve().parents[1] / "tests/fixtures/expository_generated.json"
TEST_SHARE = 0.35
BROWN_CATS = {"learned", "lore", "belles_lettres", "hobbies", "religion", "government"}


def h(s):
    return hashlib.sha256(s.encode()).hexdigest()


def require_commit(root, expected):
    actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if actual != expected:
        raise ValueError("Source snapshot differs from the pinned commit: " + str(root))


def split_of(group):
    return "test" if int(h(group)[:8], 16) / 0xFFFFFFFF < TEST_SHARE else "train"


def nwords(t):
    return len(re.findall(r"[A-Za-z]+", t))


def plain_paragraphs(raw):
    out = []
    for block in re.split(r"\n\s*\n", raw.replace("\r", "")):
        text = " ".join(l.strip() for l in block.split("\n") if l.strip())
        text = re.sub(r"\{[^}]*\}", "", text)
        text = re.sub(r"\[(?:Illustration|Footnote)[^\]]*\]", "", text)
        text = re.sub(r"_([^_]+)_", r"\1", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append(text)
    return out


def gutenberg_paragraphs(path):
    raw = Path(path).read_text(encoding="utf-8", errors="replace")
    start = re.search(r"\*\*\* ?START OF.*\n", raw)
    end = re.search(r"\*\*\* ?END OF", raw)
    return plain_paragraphs(raw[start.end() if start else 0:end.start() if end else None])


def chunks_from(paragraphs, lo=380, hi=650):
    """Runs of consecutive prose paragraphs (30-300 words each) totalling lo..hi words."""
    run, n = [], 0
    for p in paragraphs + [""]:
        words = nwords(p)
        prose = 30 <= words <= 300 and p[-1:] in ".?!\"'”)" and not p.isupper()
        if not prose or n + words > hi:
            if n >= lo:
                yield run
            run, n = [], 0
            if not prose:
                continue
        run.append(p)
        n += words


def row(rid, label, source, group, text):
    return {"id": rid, "label": label, "source": source, "group": group, "text": text}


def gutenberg_rows(path, source, book, limit):
    cs = sorted(chunks_from(gutenberg_paragraphs(path)), key=lambda c: h(c[0]))
    return [row("%s-%02d" % (book, i), "human", source, "%s-%02d" % (book, i), "\n\n".join(c))
            for i, c in enumerate(cs[:limit])]


def brown_detok(tagged):
    s = " ".join(t.rsplit("/", 1)[0] for t in tagged.split())
    s = s.replace("``", "“").replace("''", "”")
    s = re.sub(r"\s+([,.;:?!)\]])", r"\1", s)
    s = re.sub(r"([(\[“])\s+", r"\1", s)
    s = re.sub(r"\s+”", "”", s)
    s = re.sub(r"\s+(n't|'s|'re|'ve|'ll|'d|'m)\b", r"\1", s)
    s = re.sub(r"\s*--\s*", " — ", s)
    return s.strip()


def brown_rows(nltk):
    z = zipfile.ZipFile(nltk / "packages/corpora/brown.zip")
    cats = dict(l.split() for l in z.read("brown/cats.txt").decode().splitlines() if l.strip())
    rows = []
    for fid, cat in sorted(cats.items()):
        if cat not in BROWN_CATS:
            continue
        raw = z.read("brown/" + fid).decode("utf-8", errors="replace")
        paras = [" ".join(brown_detok(l) for l in b.strip().split("\n") if l.strip())
                 for b in re.split(r"\n\s*\n", raw) if b.strip()]
        for c in list(chunks_from(paras, 300, 650))[:1]:
            rows.append(row("brown-" + fid, "human", "Brown corpus (1961), " + cat, "brown-" + fid, "\n\n".join(c)))
    return rows


def abc_rows(nltk, kind, limit):
    z = zipfile.ZipFile(nltk / "packages/corpora/abc.zip")
    lines = z.read("abc/%s.txt" % kind).decode("utf-8", errors="replace").splitlines()
    arts, cur = [], []
    for l in (l.strip() for l in lines):
        if not l:
            continue
        if l[-1] not in ".?!\"'”)" and cur:      # an unpunctuated line is the next headline
            arts.append(cur)
            cur = []
        cur.append(l)
    arts.append(cur)
    rows = []
    for a in arts:
        body = [l for l in a[1:] if l[-1:] in ".?!\"'”)"]
        if sum(nwords(l) for l in body) < 250:
            continue
        keep, m = [], 0
        for l in body:
            keep.append(l)
            m += nwords(l)
            if m > 600:
                break
        gid = "abc-%s-%s" % (kind, h(a[0])[:8])
        rows.append(row(gid, "human", "ABC news (%s), NLTK abc corpus" % kind, gid, "\n\n".join(keep)))
    rows.sort(key=lambda r: h(r["id"]))
    return rows[:limit]


def sotu_rows(nltk):
    z = zipfile.ZipFile(nltk / "packages/corpora/state_union.zip")
    rows = []
    for name in sorted(n for n in z.namelist() if n.endswith(".txt") and n.split("/")[-1][:4].isdigit()):
        stem = name.split("/")[-1][:-4]
        if int(stem[:4]) < 1990:
            continue
        paras = plain_paragraphs(z.read(name).decode("utf-8", errors="replace"))
        for i, c in enumerate(list(chunks_from(paras))[:3]):
            rows.append(row("sotu-%s-%d" % (stem, i), "human", "State of the Union, NLTK state_union corpus",
                            "sotu-" + stem, "\n\n".join(c)))
    return rows


def short_variant(r, lo=150, hi=300):
    """Leading paragraphs (or sentences of the first one) totalling lo..hi words."""
    paras = r["text"].split("\n\n")
    title = [p for p in paras[:2] if nwords(p) < 15 and p[-1:] not in ".?!"]
    body = [p for p in paras if p not in title and nwords(p) >= 15]
    keep, n = [], 0
    for p in body:
        if n + nwords(p) > hi:
            sents = re.split(r"(?<=[.?!])\s+", p)
            part = []
            for s in sents:
                if n + nwords(s) > hi:
                    break
                part.append(s)
                n += nwords(s)
            if part:
                keep.append(" ".join(part))
            break
        keep.append(p)
        n += nwords(p)
    if n < lo:
        return None
    return dict(r, id=r["id"] + "-short", text="\n\n".join(title[:1] + keep))


def build(nltk, wells, vanloon):
    require_commit(nltk, NLTK_COMMIT)
    require_commit(wells, WELLS_COMMIT)
    require_commit(vanloon, VANLOON_COMMIT)
    rows = gutenberg_rows(wells / "35461.txt", "H. G. Wells, A Short History of the World (1922)", "wells", 40)
    rows += gutenberg_rows(vanloon / "754.txt", "H. W. van Loon, The Story of Mankind (1921)", "vanloon", 40)
    rows += brown_rows(nltk) + abc_rows(nltk, "science", 80) + abc_rows(nltk, "rural", 40) + sotu_rows(nltk)
    for case in json.loads(GENERATED.read_text()):
        rows.append(row(case["id"], "generated", "assistant-generated expository essay", case["topic"], case["text"]))
    rows += [s for s in map(short_variant, list(rows)) if s]
    for r in rows:
        r["split"] = split_of(r["group"])
        r["text_sha256"] = h(r["text"])
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--nltk-data", type=Path, required=True)
    ap.add_argument("--wells", type=Path, required=True)
    ap.add_argument("--vanloon", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    rows = build(args.nltk_data, args.wells, args.vanloon)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    from collections import Counter
    print(Counter((r["label"], r["split"], r["id"].endswith("-short")) for r in rows))
