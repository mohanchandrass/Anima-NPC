ï»¿# Persona-Aware, Knowledge-Bounded NPC Dialogue Through Model-Agnostic LLM Orchestration

IEEE paper source and scientific-revision artifacts. Authors, in order: Mohan Chandra S S (1RVU23CSE282), Shreyas C (1RVU23CSE443), Nithish Gowda H N (1RVU23CSE317), Nithin S (1RVU23CSE316). RV University. USNs replace emails as requested.

## Current revision (2026-11-06)

The manuscript is a 7-page rewrite describing the 6 November 2026 working tree on top of commit 2e04990. See [REPORT_REVISION.md](REPORT_REVISION.md). Sections further down this file describe earlier revisions.

Deliverables: `paper.pdf` (identical to `main.pdf`) and `paper.docx`. Rebuild both from this directory:

```sh
../../.tools/tectonic/tectonic.exe --keep-logs --keep-intermediates main.tex
cp main.pdf paper.pdf
python build_docx.py
../../.venv/Scripts/python.exe validate_and_package.py
```

`build_docx.py` needs python-docx and PyMuPDF, authoring tools that are not in `requirements.txt`. `validate_and_package.py` runs from the project `.venv`.

## Overleaf and local compilation

Upload `paper_source.zip` using Overleaf New Project -> Upload Project. Select `main.tex` and use pdfLaTeX. The ZIP includes the main source, bibliography, compiled native-vector figures, editable TikZ/macro sources, public experiment data, raw observations, analysis scripts and reproduction notes. It excludes credentials, embedding weights, indices, player data and game assets. No SVG conversion or shell escape is needed to build the paper.

Local standard LaTeX:

```sh
latexmk -pdf -interaction=nonstopmode -halt-on-error -jobname=paper main.tex
```

Reviewed local build uses Tectonic 0.17.0:

```sh
tectonic --keep-logs main.tex
```

Copy its `main.pdf` output to `paper.pdf`. Main-document packages: IEEEtran, cite, amsmath, graphicx, booktabs, array, hyperref and T1 font encoding. Paper fonts/margins/line spacing are standard IEEEtran. All figures are vector PDFs generated from native TikZ, not rasterized flowcharts.

## Native figure sources

`figure_sources/diagram_styles.tex` defines the shared restrained palette and styles. The four editable documents are `system_architecture.tex`, `rag_orchestration_pipeline.tex`, `model_agnostic_architecture.tex` and `knowledge_ingestion.tex`. Compile each from its source directory, using a standard LaTeX installation with standalone/TikZ/PGF, or Tectonic. For example, from this directory:

```sh
tectonic --outdir figures figure_sources/system_architecture.tex
tectonic --outdir figures figure_sources/rag_orchestration_pipeline.tex
tectonic --outdir figures figure_sources/model_agnostic_architecture.tex
tectonic --outdir figures figure_sources/knowledge_ingestion.tex
```

The first three appear in the manuscript. Ingestion is supplied as a compiled editable supplementary diagram; its offline path is already represented in Figure 1, so a redundant fourth main-paper figure is omitted.

## Experiments and evidence

Read `experiments/README.md`. Twenty-four fixed queries use real CPU MiniLM/Chroma over twelve original fictional documents. A real-model study completed seven paired cases plus one unmatched RAG fallback before a provider rate limit. Raw candidates, latencies, revision changes and metric summaries are included. Labels are author-assigned, not independent human judgments. Literal answer-target mentions are not semantic correctness; blinded human rating sheets remain blank. No real multi-backend comparison or post-update generated answer was completed.

The paper builds independently from this ZIP. Runtime experiment reproduction also needs the source repository at ff9fd7e, its root .venv/single requirements.txt, and the fixture paths explained in the experiment README. Actual provider calls require deliberate authorization and a local key. Do not rerun paid work simply to compile the paper.

Latest native-compatibility full offline regression: 514 passed; native CTest 2/2 passed. The controlled evaluation revision passed 509 tests, including 17 isolated evaluation tests. Regression counts are engineering validation, not dialogue-quality results. Parent research documents contain REVISION_AUDIT.md, SCIENTIFIC_EVALUATION_REPORT.md and IEEE_FINAL_REVIEW.md; the last is an explicit reviewer-role self-review.

## Game demonstration and submission limits

AnimaConnector 0.2.0 was live-tested on Skyrim 1.7.104.0. Genuine Jenassa context reached the loopback bridge. The authored Hello -> What do you need? turn received native started/completed acknowledgements; an earlier closed-menu trial received started/interrupted acknowledgements. Completion is the native receiver lifecycle, not independent confirmation of player perception. No live LLM/RAG, audible voice or frame-time experiment is claimed. Minimal receipts and DLL provenance are in experiments/raw_results/native_compatibility_followup.json.

A clear original game screenshot is still required by the final-refinement request. Existing Windows captures are black/obstructed and are excluded; no synthetic or web image substitutes. Native Print Screen/F12 capture was requested. Independent relevance/semantic ratings, boundary trials, a second backend and generated-answer update propagation remain research limitations.

## Editing and validation

Edit `main.tex` for title, author block and prose; `references.bib` for references. The exact approved title is also set in PDF metadata. `validate_and_package.py` checks title, author IDs, abstract length, seven-page expanded evidence limit, citation/label/assets and final PDF/ZIP integrity. Local underfull paragraph/float notices are documented in the final validation report; no clipping, missing references or font substitution may be treated as acceptable merely because compilation succeeds.


## Response attribution and examples

Table II reproduces all 14 exact no-RAG/RAG responses for seven matched fictional-corpus questions; it is not a historical software-improvement benchmark. The additional uppercase HELLO guard trial is preserved in experiments/raw_results/anima_uppercase_attribution.json. Its route was authored, with zero model calls, and native started/completed receipts. The user's Mantella.exe HELLO -> No need to shout screenshot belongs to Mantella and is excluded. Current Anima bridge startup explicitly labels its offline provider and disabled RAG/STT/TTS; its visible console prefixes diagnostics with [ANIMA].

## Current report expansion (2026-11-03)

See REPORT_REVISION.md for scope, evidence mappings and a labelled self-review. The expanded manuscript has seven pages, five result tables, all seven matched dialogue comparisons, and separate Conclusion/Future Work subsections. It retains the three main native-vector diagrams and all four editable TikZ sources. The existing eight model-study inputs yielded 14 accepted generated outputs and one fallback; two native greeting strings bring the displayed input-string count to ten. This revision introduces no new live/provider experiments. The genuine gameplay screenshot and additional generated test cases remain pending the separate system work. Historical test totals above are not rerun results for this document revision.

validate_and_package.py emits ../evidence/report_expansion_document_validation.json. The exact ZIP is independently extracted and rebuilt, with verification recorded under ../evidence/report_expansion_zip_verification.json.
