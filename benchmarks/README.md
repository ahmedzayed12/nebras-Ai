# v3 validation and its limits

## v3.1 calculation repair

The earlier v3.0 artifact below remains unchanged. The v3.1 artifacts record
the current model: `v3.1-validation.json`, `v3.1-legal-validation.json` and
`v3.1-wording-calibration.json`.

Three general changes address lost evidence: a single subsection heading no
longer exempts its words throughout a document; duplicate numbered headings
do not count as independent topic support; and a positive unit's contextual
posterior cannot fall below its standalone posterior using the same 0.25 prior.
Negative or neutral own evidence is not raised by that safeguard. File-wide
boosts are excluded from the standalone calculation.

After correlated wording features had been pooled, the historical threshold
2.9 no longer represented the documented human p97. The new wording threshold
is 2.11: the rounded linearly interpolated p97 (2.1072657926534197) across
1,068 units of >=40 words, from the **52 pre-existing development calibration
PEPs**. These are a fixed subset of the first 100 development documents,
identified by filename and text hash in the calibration artifact. Units are
read through the neutral DOCX pipeline. No submitted document, target score,
reserved PEP, Doyle excerpt or legal contract is used to fit this threshold.
The archived diagnostic path retains its historical threshold.

Validation after this change: 0/70 reserved English technical/fiction samples
at either 52 or 65; 18/24 synthetic variants at either threshold. The six plain
rewriting controls are still missed. This repeats an already inspected set,
so it is regression evidence, not a new independent accuracy estimate.

Additional genre check: 40 distinct CUAD test-set contracts from snapshot
[`67faa0e6023b04fcaae6cc09497ab00e5d63a2a2`](https://github.com/TheAtticusProject/cuad/tree/67faa0e6023b04fcaae6cc09497ab00e5d63a2a2)
(21 October 2021, before ChatGPT). Select contracts with >=800 words, ordered
by SHA-256 of title, retain the first 40, take each first <=3,200 words and form
sentence-aligned units of >=80 words because CUAD contexts are flattened.
Result: 0/40 at 52 or 65, versus baseline v2.1 2/40 at 52 and 0/40 at 65.
These are legal **contracts**, not a validation of legal theses, every author,
or AI-edited contracts. Zero observed errors is not proof of zero population
error; the nominal upper Wilson bound for 0/40 is about 8.8%.

Reproduce locally with the pinned repositories:

```sh
python tools/calibrate_wording.py --peps /path/to/peps --output calibration.json
python tools/build_legal_benchmark.py --cuad /path/to/cuad --output legal.json
python tools/benchmark_balanced.py --human-json legal.json --output legal-results.json
```

The calibration tool checks the pre-existing subset, source commit and text
hashes. The contract builder also checks its source commit. Neither downloads
or uploads source text. No user filename or document is in these artifacts.

## Historical v3.0 check

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
