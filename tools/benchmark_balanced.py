"""Reproducible v2/v3 comparison on labelled manifests; no training or tuning.

python tools/benchmark_balanced.py --human-json human.json --output results.json
The human manifest contains id, text, source, group. Scores, hashes and source
links are exported; document text is not copied into the result artifact.
Generated fixtures are synthetic controls, not a representative AI corpus.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import zipfile
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend_adapter as b


def docx(text, reviewed=False):
    body = "".join('<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % escape(p)
                   for p in text.split("\n\n"))
    document = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>%s</w:body></w:document>' % body
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", document)
        if reviewed:
            z.writestr("docProps/app.xml", '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><TotalTime>2400</TotalTime><Application>Microsoft Office Word</Application></Properties>')
            z.writestr("docProps/core.xml", '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Researcher</dc:creator><cp:lastModifiedBy>Editor</cp:lastModifiedBy><cp:revision>40</cp:revision></cp:coreProperties>')
            z.writestr("word/comments.xml", '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>')
    return out.getvalue()


def variants(text):
    yield "original", text, False
    yield "reviewed_metadata", text, True
    yield "spacing", text.replace(" ", "  "), False
    yield "typing_errors", text.replace("the ", "the the ", 2).replace("and ", "and and ", 2), False
    paraphrase = text
    for a, c in {"comprehensive": "detailed", "framework": "approach", "crucial": "important", "holistic": "broad", "Furthermore, ": "", "Moreover, ": ""}.items():
        paraphrase = paraphrase.replace(a, c)
    yield "surface_rewrite", paraphrase, False
    yield "rewrite_and_review", paraphrase, True


def wilson(successes, total):
    if not total:
        return None
    z = 1.96
    p = successes / total
    d = 1 + z * z / total
    c = (p + z * z / (2 * total)) / d
    r = z * (p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5 / d
    return [round(c - r, 4), round(c + r, 4)]


def run(humans):
    generated = json.loads((Path(__file__).resolve().parents[1] / "tests/fixtures/generated_controls.json").read_text())
    rows = []
    for label, cases in (("human", humans), ("synthetic_generated", generated)):
        for case in cases:
            versions = [("original", case["text"], False)] if label == "human" else variants(case["text"])
            for kind, text, reviewed in versions:
                r = b.analyze_uploaded_bytes("benchmark.docx", docx(text, reviewed))
                rows.append({"id": case["id"], "group": case.get("group", case["id"]),
                             "label": label, "variant": kind, "source": case.get("source", "assistant-generated synthetic fixture"),
                             "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                             "v2": r.score_engine, "v3": r.final_score,
                             "global": r.balanced.global_score, "local": r.balanced.local_score})
    summary = {}
    for label in ("human", "synthetic_generated"):
        selected = [r for r in rows if r["label"] == label]
        summary[label] = {"samples": len(selected), "groups": len({r["group"] for r in selected})}
        for model in ("v2", "v3"):
            for threshold in (52, 65):
                count = sum(r[model] >= threshold for r in selected)
                summary[label][f"{model}_at_{threshold}"] = {"count": count, "rate": count / max(1, len(selected)),
                                                           "wilson95": wilson(count, len(selected)) if label == "human" else None}
    return {"summary": summary, "results": rows,
            "limitations": ["Human excerpts may share authors; intervals assume independent samples and may be optimistic.",
                            "Four generated fixture families with six correlated variants each; not population recall.",
                            "English technical/fiction validation; no Arabic or comprehensive academic-domain calibration.",
                            "A plain generated rewrite is deliberately included even when the detector misses it."],
            "engine": b.engine_status()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--human-json", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run(json.loads(Path(args.human_json).read_text()))
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
