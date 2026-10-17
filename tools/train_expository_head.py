"""Train and evaluate the v3.2 expository head on EXTERNAL labelled manifests only.

python tools/build_genre_benchmark.py ... --output genre.json
python tools/train_expository_head.py --genre-json genre.json --peps /path/to/pinned/peps \
    --output benchmarks/v3.2-expository-head.json --write-module

Training humans: the "train" split of the genre manifest (pre-2020 human prose,
full and short variants) plus the 100 development PEPs. Training generated: the
"train" split of tests/fixtures/expository_generated.json. The "test" split is
scored only after the parameters and gate are fixed. The 58 reserved PEPs, the
Doyle excerpts, CUAD and the synthetic controls are never read here. No
submitted user document is accepted: --exclude-sha256 refuses listed texts.
Needs numpy (offline only; the runtime module is pure Python).
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import backend_adapter as b
import expository_head as eh
from tools.benchmark_balanced import docx
from tools.build_human_benchmark import pep_rows

LIMITATIONS = [
    "Generated essays come from one assistant model family; not population recall for other models.",
    "Human side is published pre-2020 prose (1921-2006); no student essays or non-native English.",
    "The gate is the maximum training-human decision value; one outlier sets it.",
    "Document-level only: the paragraph map and highlights are unchanged.",
    "Texts routed to the v2.1 fallback (short or list-like prose) are skipped: the head never runs there.",
]


def featurize(text):
    r = b.analyze_uploaded_bytes("training.docx", docx(text))
    bl = r.balanced
    if bl.source != "balanced_v3":
        return None
    x = eh.inputs(bl.features, [(u.words, u.x) for u in bl.segmentation.units], bl.local_score,
                  bl.global_logit, r.confidence)
    return x, r.confidence, (bl.global_score + bl.local_score) / 2.0


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


def summary(rows, key):
    out = {}
    for label in ("human", "generated"):
        for length in ("full", "short"):
            xs = [r[key] for r in rows if r["label"] == label and r["id"].endswith("-short") == (length == "short")]
            out["%s_%s" % (label, length)] = {"samples": len(xs), "at_52": sum(x >= 52 for x in xs),
                                              "at_65": sum(x >= 65 for x in xs)}
    return out


def write_module(params):
    path = ROOT / "expository_head.py"
    src = path.read_text()
    start, end = src.index("# BEGIN GENERATED PARAMS\n"), src.index("# END GENERATED PARAMS\n")
    block = ["# BEGIN GENERATED PARAMS", "PARAMS = {"]
    for k in ("mean", "scale", "coef"):
        block.append('    "%s": %s,' % (k, json.dumps(params[k])))
    block += ['    "intercept": %r, "gate": %r,' % (params["intercept"], params["gate"]), "}", ""]
    path.write_text(src[:start] + "\n".join(block) + src[end:])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--genre-json", type=Path, required=True)
    ap.add_argument("--peps", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--l2", type=float, default=0.1)
    ap.add_argument("--exclude-sha256", nargs="*", default=[])
    ap.add_argument("--write-module", action="store_true", help="regenerate PARAMS in expository_head.py")
    args = ap.parse_args()
    excluded = set(args.exclude_sha256)

    manifest = json.loads(args.genre_json.read_text())
    manifest += [dict(r, split="train", label="human") for r in pep_rows(args.peps)[:100]]
    data = {"train": [], "test": []}
    for r in manifest:
        digest = hashlib.sha256(r["text"].encode()).hexdigest()
        if digest in excluded:
            raise ValueError("Excluded document found in manifest: " + r["id"])
        f = featurize(r["text"])
        if f is not None:
            data[r["split"]].append((r, digest) + f)

    train = data["train"]
    X = np.array([t[2] for t in train])
    y = np.array([float(t[0]["label"] == "generated") for t in train])
    weights = b.get_engine().load_weights()
    allowed = np.array([np.sign(weights[n]) if n in weights else 1.0 for n in eh.INPUTS])
    mu, sd, w, c = fit(X, y, args.l2, allowed)
    params = {"inputs": list(eh.INPUTS), "mean": [round(float(v), 6) for v in mu],
              "scale": [round(float(v), 6) for v in sd], "coef": [round(float(v), 6) for v in w],
              "intercept": round(float(c), 6), "l2": args.l2}
    # The gate is computed with the ROUNDED parameters the runtime uses.
    d_train = [eh.decision(t[2], t[3], params) for t in train]
    params["gate"] = round(max(d for d, t in zip(d_train, train) if t[0]["label"] == "human") + 1e-6, 6)

    def scored(t):
        d = eh.decision(t[2], t[3], params)
        return {"id": t[0]["id"], "label": t[0]["label"], "group": t[0].get("group", t[0]["id"]),
                "source": t[0].get("source", ""), "text_sha256": t[1], "head_logit": round(d, 4),
                "v3.1": round(t[4], 1), "v3.2": round(eh.lift(t[4], d, params), 1)}

    test_rows = [scored(t) for t in data["test"]]
    result = {
        "params": params,
        "method": "class-balanced L2 logistic regression on the v3.1 evidence vector (effective feature values, "
                  "confidence-shrunk); coefficients keep the sign of the engine's prior weight (positive for "
                  "derived inputs); gate = maximum training-human decision value; lift closes at most %.0f%% of "
                  "the remaining gap." % (100 * eh.LIFT_SHARE),
        "training_counts": {"human": int((y == 0).sum()), "generated": int(y.sum())},
        "training_generated_above_gate": int(sum(d > params["gate"] for d, t in zip(d_train, train)
                                                 if t[0]["label"] == "generated")),
        "training": [{"id": t[0]["id"], "label": t[0]["label"], "text_sha256": t[1]} for t in train],
        "heldout_test": {"v3.1": summary(test_rows, "v3.1"), "v3.2": summary(test_rows, "v3.2"), "rows": test_rows},
        "skipped_fallback_rows": sum(1 for r in manifest if r["split"] == "test") - len(test_rows),
        "limitations": LIMITATIONS,
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n")
    if args.write_module:
        write_module(params)
    print(json.dumps({k: result[k] for k in ("training_counts", "training_generated_above_gate")}, indent=1))
    print(json.dumps({k: v for k, v in result["heldout_test"].items() if k != "rows"}, indent=1))
    print("gate", params["gate"])


if __name__ == "__main__":
    main()
