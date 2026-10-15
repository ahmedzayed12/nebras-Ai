# v3 validation and its limits

`v3-validation.json` records scores, source links, text hashes, model fingerprint,
and counts. No submitted user document or user-document text is included.

Human validation: 58 Python PEP documents from the repository snapshot
[`475d6cdf8caf61cfe02ec527f84d9489566349e6`](https://github.com/python/peps/tree/475d6cdf8caf61cfe02ec527f84d9489566349e6)
(31 December 2019), plus 12 non-overlapping prose excerpts from Conan Doyle's
*The Adventures of Sherlock Holmes*, from GITenberg snapshot
[`7e5dd2703fbf503c004063e96ab7c61262375e5f`](https://github.com/GITenberg/The-Adventures-of-Sherlock-Holmes_1661/tree/7e5dd2703fbf503c004063e96ab7c61262375e5f)
(20 October 2018). Both snapshots predate contemporary generative text tools.
The original texts retain their respective source notices/licenses in the
downloaded repositories; only scores, identifiers and links are committed here.

Selection: retain unindented prose blocks of at least 30 alphabetic words,
exclude code/directive blocks, require 800 words per PEP, truncate at 24,000
characters, sort by SHA-256 of filename. The first 100 PEP documents were used
for development and are **excluded** from the 58 reserved documents. Austen
excerpts used during development are likewise absent from validation. No model
weight or threshold is fitted to these manifests. Re-running validation after
a code change is regression verification, not a fresh independent experiment.

Synthetic controls: four families in `tests/fixtures/generated_controls.json`,
assembled from new assistant-generated prose and existing repository synthetic
control paragraphs. Six variants per family: original, reviewed Word metadata,
spacing, typing errors, surface synonym rewriting, rewriting plus metadata.
They are correlated, intentionally small controls, **not** a population sample
of models or real humanizer services. The plain generated rewrite is a known
false negative and remains in the recorded denominator.

| Reserved check | Baseline v2.1 | v3 |
|---|---:|---:|
| Human samples at index >=52 | 2/70 | 0/70 |
| Human samples at index >=65 | 0/70 | 0/70 |
| Synthetic variants at index >=52 | 18/24 | 18/24 |
| Synthetic variants at index >=65 | 16/24 | 18/24 |

Neither threshold is a calibrated authorship probability. Zero observed false
positives does not establish a zero population rate: the nominal 95% Wilson
upper bound is about 5.2%, and shared authors/chapters make the independence
assumption optimistic. These checks cover English technical prose and fiction,
not representative legal, mathematical, non-native-English or Arabic writing.
Do not report 100% accuracy or general humanizer detection from these counts.

## Reproduce

Obtain the two repositories at the pinned commits above, then run:

```sh
python tools/build_human_benchmark.py --peps /path/to/peps --fiction /path/to/doyle/1661.txt --output human.json
python tools/benchmark_balanced.py --human-json human.json --output results.json
python -m pytest -q
```

Source preparation and scoring are deterministic. The manifest builder refuses
different repository commits. It reads local repositories and makes no network
calls. The benchmark does not train, tune, or upload the text.
