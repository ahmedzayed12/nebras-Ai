"""Train the v3.2 expository head on EXTERNAL labelled manifests only.

python tools/train_expository_head.py --genre-json genre.json --peps /path/to/pinned/peps --output head.json

Training humans: the "train" split of the genre manifest (pre-2020 human prose)
plus the 100 development PEPs. Training generated: the "train" split of the
assistant-generated expository essays. The "test" split, the 58 reserved PEPs,
the Doyle excerpts, CUAD and the synthetic controls are never read here.
No submitted user document is accepted: --exclude-sha256 refuses any row whose
text hash is listed, so a reported file can be kept out explicitly.
Needs numpy (offline only; the runtime module is pure Python).
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend_adapter as b
import expository_head as eh
from tools.benchmark_balanced import docx
from tools.build_human_benchmark import pep_rows


def featurize(text):
    r = b.analyze_uploaded_bytes("training.docx", docx(text))
    bl = r.balanced
    if bl.source != "balanced_v3":
        return None
    units = [(u.words, u.x) for u in bl.segmentation.units]
    return eh.inputs({k: v[0] for k, v in bl.features.items()}, units, bl.local_score, bl.global_logit)


def fit(X, y, l2, allowed_sign, iters=20000, lr=0.05):
    """Class-balanced L2 logistic regression; coefficients outside their allowed sign are dropped and refit."""
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    w_cls = np.where(y == 1, 0.5 / y.mean(), 0.5 / (1 - y.mean()))
    active = np.ones(X.shape[1], bool)
    while True:
        w, c = np.zeros(X.shape[1]), 0.0
        for _ in range(iters):
            p = 1 / (1 + np.exp(-(Z @ w + c)))
            g = w_cls * (p - y)
            w -= lr * ((Z.T @ g) / len(y) + l2 * w) * active
            c -= lr * g.mean()
        bad = active & (np.sign(w) * allowed_sign < 0)
        if not bad.any():
            return mu, sd, w * active, c
        active &= ~bad


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--genre-json", type=Path, required=True)
    ap.add_argument("--peps", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--l2", type=float, default=0.1)
    ap.add_argument("--exclude-sha256", nargs="*", default=[])
    args = ap.parse_args()
    excluded = set(args.exclude_sha256)

    rows = [r for r in json.loads(args.genre_json.read_text()) if r["split"] == "train"]
    rows += [dict(r, split="train", label="human") for r in pep_rows(args.peps)[:100]]
    data, used = [], []
    for r in rows:
        digest = hashlib.sha256(r["text"].encode()).hexdigest()
        if digest in excluded:
            raise ValueError("Excluded document found in training manifest: " + r["id"])
        x = featurize(r["text"])
        if x is None:
            continue
        data.append((x, r["label"] == "generated"))
        used.append({"id": r["id"], "label": r["label"], "group": r.get("group", r["id"]), "text_sha256": digest})
    X = np.array([d[0] for d in data])
    y = np.array([float(d[1]) for d in data])
    weights = b.get_engine().load_weights()
    allowed = np.array([np.sign(weights[n]) if n in weights else 1.0 for n in eh.INPUTS])
    mu, sd, w, c = fit(X, y, args.l2, allowed)
    d = ((X - mu) / sd) @ w + c
    gate = float(d[y == 0].max())
    params = {"inputs": list(eh.INPUTS), "mean": [round(float(v), 6) for v in mu],
              "scale": [round(float(v), 6) for v in sd], "coef": [round(float(v), 6) for v in w],
              "intercept": round(float(c), 6), "gate": round(gate, 6), "l2": args.l2}
    result = {"params": params, "training": used,
              "counts": {"human": int((y == 0).sum()), "generated": int(y.sum())},
              "train_generated_above_gate": int((d[y == 1] > gate).sum()),
              "method": "class-balanced L2 logistic regression on the v3.1 evidence vector; coefficients must "
                        "keep the sign of the engine's prior weight (positive for derived inputs); gate = "
                        "maximum training-human decision value."}
    args.output.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "training"}, indent=1))


if __name__ == "__main__":
    main()
