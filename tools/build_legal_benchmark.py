"""Read pinned CUAD test contracts locally; write no text into score artifacts.

python tools/build_legal_benchmark.py --cuad /path/to/cuad --output legal.json
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.build_human_benchmark import require_commit

CUAD_COMMIT = "67faa0e6023b04fcaae6cc09497ab00e5d63a2a2"


def build(root):
    require_commit(root, CUAD_COMMIT)
    with zipfile.ZipFile(root / "data.zip") as archive:
        contracts = json.loads(archive.read("test.json"))["data"]
    rows = []
    for case in sorted(contracts, key=lambda x: hashlib.sha256(x["title"].encode()).hexdigest()):
        words = case["paragraphs"][0]["context"].split()
        if len(words) < 800:
            continue
        # CUAD contexts are single flattened strings. Form sentence-aligned
        # reading units >=80 words, retaining a contiguous <=3200-word excerpt.
        sentences = re.split(r"(?<=[.!?])\s+", " ".join(words[:3200]))
        paragraphs, current = [], []
        for sentence in sentences:
            current.append(sentence)
            if len(" ".join(current).split()) >= 80:
                paragraphs.append(" ".join(current))
                current = []
        if current:
            paragraphs.append(" ".join(current))
        rows.append({"id": case["title"], "group": case["title"], "label": "human",
                     "source": f"https://github.com/TheAtticusProject/cuad/blob/{CUAD_COMMIT}/data.zip#test.json",
                     "text": "\n\n".join(paragraphs)})
        if len(rows) == 40:
            break
    return rows


if __name__ == "__main__":
    # Permit invocation as a script as well as python -m tools.…
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cuad", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = build(args.cuad)
    args.output.write_text(json.dumps(rows))
    print(f"{len(rows)} legal contracts; source text stays in the local manifest.")
