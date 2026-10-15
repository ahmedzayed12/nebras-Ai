"""Reproduce v3.1's local wording threshold on the fixed development split.

No user documents or reserved validation texts are accepted for fitting.
python tools/calibrate_wording.py --peps /path/to/pinned/peps --output calibration.json
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend_adapter as b
from tools.build_human_benchmark import pep_rows
from tools.benchmark_balanced import docx


def quantile(values, p):
    xs = sorted(values)
    position = (len(xs) - 1) * p
    lo = int(position)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (position - lo)


def calibrate(peps):
    reference = json.loads((Path(__file__).resolve().parents[1] /
                            "benchmarks/v3.1-wording-calibration.json").read_text())
    selected = {r["id"]: r["text_sha256"] for r in reference["per_document"]}
    development = {r["id"]: r for r in pep_rows(peps)[:100]}
    if len(selected) != 52 or not selected.keys() <= development.keys():
        raise ValueError("Calibration must use the fixed 52 development PEPs.")
    per_document, values = [], []
    for row in reference["per_document"]:
        case = development[row["id"]]
        digest = hashlib.sha256(case["text"].encode()).hexdigest()
        if digest != selected[case["id"]]:
            raise ValueError("Calibration text changed: " + case["id"])
        report = b.analyze_uploaded_bytes("calibration.docx", docx(case["text"]))
        scores = [u.x for u in report.balanced.segmentation.units if u.words >= 40]
        values.extend(scores)
        per_document.append({"id": case["id"], "scores": scores, "text_sha256": digest})
    result = {"source": reference["source"], "documents": len(per_document), "units": len(values)}
    result.update({f"p{p}": quantile(values, p / 100) for p in (75, 90, 95, 97, 99)})
    result.update({"max": max(values), "per_document": per_document,
                   "method": reference["method"], "threshold_rounded": round(result["p97"], 2)})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peps", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = calibrate(args.peps)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "per_document"}, indent=2))
