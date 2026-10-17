"""Read-only JSON audit: python tools/audit_scoring.py file.docx [more.docx].

Separates extraction/preparation, original feature arithmetic, later fusions,
and the published result. Does not train, tune, or modify documents.
"""
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend_adapter as b


def audit(path):
    path = Path(path)
    data = path.read_bytes()
    r = b.analyze_uploaded_bytes(path.name, data)
    evidence = r.engine_evidence
    bias = b.get_engine().load_weights()["bias"]
    z = bias + sum(c for _, _, c in evidence["features"].values())
    expected = 100.0 / (1.0 + math.exp(-z * r.confidence))
    raw = b.get_engine().analyze_docx(str(path)) if path.suffix.lower() == ".docx" else None
    return {
        "file": path.name, "sha256": hashlib.sha256(data).hexdigest(),
        "engine": b.engine_status(), "bias": bias,
        "raw_document_score": raw.score if raw else None,
        "prepared_engine_score": r.score_engine,
        "recomputed_engine_score": expected,
        "engine_arithmetic_error": r.score_engine - expected,
        "published_score": b.primary_score(r), "published_source": r.score_source,
        "diagnostic_score": r.score,
        "balanced_components": {
            "source": r.balanced.source, "global": r.balanced.global_score,
            "local": r.balanced.local_score, "final": r.balanced.score,
            "global_logit": r.balanced.global_logit,
            "mean_of_readings": (r.balanced.global_score + r.balanced.local_score) / 2.0
            if r.balanced.source == "balanced_v3" else None,
            "head_logit": r.balanced.head_logit, "head_lift": r.balanced.head_lift,
            "excluded_file_contributions": r.balanced.excluded,
            "warnings": r.balanced.warnings,
        },
        "engine_features": evidence["features"],
        "preparation": vars(r.docx_clean) if hasattr(r, "docx_clean") else {},
        "stages": r.score_stages,
        "max_ledger_error": max(abs(s["ledger_error"]) for s in r.score_stages),
    }


if __name__ == "__main__":
    rows = []
    for name in sys.argv[1:]:
        try:
            rows.append(audit(name))
        except Exception as exc:
            rows.append({"file": Path(name).name, "error": str(exc)})
    print(json.dumps(rows, ensure_ascii=False, indent=2))
