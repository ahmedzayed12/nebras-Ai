"""Extract a deterministic pre-2020 human validation manifest, never download it.

python tools/build_human_benchmark.py --peps /path/to/peps --fiction /path/to/doyle/1661.txt --output human.json
Requires the pinned source snapshots documented in benchmarks/README.md.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

PEP_COMMIT = "475d6cdf8caf61cfe02ec527f84d9489566349e6"
DOYLE_COMMIT = "7e5dd2703fbf503c004063e96ab7c61262375e5f"


def require_commit(root, expected):
    actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if actual != expected:
        raise ValueError("Source snapshot differs from the pinned pre-2020 commit: " + str(root))


def pep_rows(peps):
    require_commit(peps, PEP_COMMIT)
    rows = []
    for path in sorted(peps.glob("pep-*.txt")):
        text = path.read_text(errors="replace")
        body = re.split(r"\n\s*\n", text, maxsplit=1)[-1]
        paragraphs = []
        for block in re.split(r"\n\s*\n", body):
            lines = block.splitlines()
            if any(line.startswith(("    ", "\t", ".. ", ">>>", "http")) for line in lines):
                continue
            t = " ".join(line.strip() for line in lines)
            if len(re.findall("[A-Za-z]+", t)) >= 30 and t.count("`") <= 4:
                paragraphs.append(t)
        if sum(len(t.split()) for t in paragraphs) >= 800:
            rows.append({"id": path.name, "label": "human", "group": path.stem,
                         "source": f"https://github.com/python/peps/blob/{PEP_COMMIT}/{path.name}",
                         "text": "\n\n".join(paragraphs)[:24000]})
    # The first 100 documents were development data; never report them as
    # the reserved validation set. Selection is independent of model scores.
    rows.sort(key=lambda r: hashlib.sha256(r["id"].encode()).hexdigest())
    return rows


def build(peps, fiction):
    require_commit(fiction.parent, DOYLE_COMMIT)
    rows = pep_rows(peps)
    rows = rows[100:]
    book = fiction.read_text(errors="replace")
    start = max(0, book.find("A SCANDAL IN BOHEMIA"))
    paragraphs = [" ".join(p.split()) for p in re.split(r"\n\s*\n", book[start:]) if len(p.split()) > 40]
    for i in range(0, min(len(paragraphs) - 20, 240), 20):
        rows.append({"id": f"doyle-{i:03d}", "label": "human", "group": "doyle-1661",
                     "source": f"https://github.com/GITenberg/The-Adventures-of-Sherlock-Holmes_1661/blob/{DOYLE_COMMIT}/1661.txt",
                     "text": "\n\n".join(paragraphs[i:i + 20])})
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peps", type=Path, required=True)
    parser.add_argument("--fiction", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = build(args.peps, args.fiction)
    args.output.write_text(json.dumps(rows, ensure_ascii=False))
    print(f"{len(rows)} reserved human samples; source texts stay in the local manifest.")
