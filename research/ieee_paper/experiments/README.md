# Reproducing the revised experiments

## Current-system studies (2026-11-06)

Offline and free: no API, Ollama or game call. From the repository root:

```powershell
$env:PYTHONPATH = "src;cv/backend;."
.\.venv\Scripts\python.exe -X utf8 research/ieee_paper/experiments/scripts/current_system_study.py
.\.venv\Scripts\python.exe -X utf8 research/ieee_paper/experiments/scripts/live_audit.py
```

`live_audit.py` reads the ignored local files under `output/skyrim` and uses a fixed log and frame cut-off. Screenshot retention can remove old frames, so its frame audit may not reproduce later. Its output excludes player text, reply text and session IDs. Language and vision models are scripted in `current_system_study.py`, so it measures conformance and overhead, not reply quality or visual accuracy.

The sections below describe the earlier study.

These are real MiniLM/Chroma and configured-provider observations over original controlled Cedarvale fiction. They are not independent human ratings or a Skyrim benchmark. Read `research/SCIENTIFIC_EVALUATION_REPORT.md` before interpreting results.

## Environment and data

Use the audited repository at runtime commit `ff9fd7e`, the root `.venv`, and its single pinned `requirements.txt`. No core source was changed. Public files are under `fixtures/evaluation/paper_revision/`; this package also supplies a copy under `experiments/datasets/paper_revision/`. When restoring this folder into a checkout, copy that data to the original fixture path and retain `research/ieee_paper/experiments/scripts/` so repository-relative imports resolve. The source ZIP rebuilds the paper independently; executing runtime experiments additionally requires the repository and its pinned environment.

Actual encoder assets were prepared with the existing LocalEmbedder into ignored `output/paper_revision/models`; weights and vector databases are excluded. First-time preparation downloads the production encoder. No private corpus, credential or saved player state is packaged.

## Commands from the repository root

```powershell
.\.venv\Scripts\python.exe -X utf8 research/ieee_paper/experiments/scripts/evaluate.py --run-id fresh-study
.\.venv\Scripts\python.exe -X utf8 research/ieee_paper/experiments/scripts/evaluate.py --run-id fresh-study --cold
.\.venv\Scripts\python.exe -X utf8 research/ieee_paper/experiments/scripts/analyze.py
.\.venv\Scripts\python.exe -m pytest tests/unit/research -q
```

The archived retrieval run used `--run-id primary`. Fresh index IDs prevent overwriting an existing database; raw result filenames are stable, so preserve archived outputs before rerunning. Analysis reads saved generation records and makes no provider calls.

## Real-provider generation

`generate_pairs.py` invokes the actual core with current local Gemini profile/.env configuration. It performs potentially billable requests and must only be explicitly invoked for a new authorized study. It never exports the key. Max 54 study requests, 400 output tokens, 30-s transport, sequential work, and a fatal auth/quota gate bound execution. It refuses to overwrite existing generation results/index. For a deliberate repeat, preserve prior raw files/index and run in a fresh study checkout, with the same declared model/profile and public data. Changing a backend requires recording its identity/options. Do not use OfflineProvider as a model baseline.

The archived study stopped after 17 requests because of a rate-limit error. Seven pairs plus one unmatched RAG case were observed. Do not fill missing boundary/update rows with fake or guessed responses.

## Labels and human evaluation

Retrieval gold files and literal answer targets were authored before calls and are never derived from ranking outputs. They lack independent adjudication. P@3 uses denominator three and has ceiling 1/3 because each query has one gold source. Target matching is lexical and cannot score factual faithfulness.

Two `analysis/pending_rater_*.csv` sheets contain randomized opaque sample IDs, persona, query and actual response, with all rating fields blank. Keep `rating_condition_key.json` away from raters. Ask separate evaluators to rate persona/relevance from 1 (inconsistent/irrelevant) to 5 (consistent/relevant), split factual assertions, record supported/unsupported claims against the authorized corpus, and assess abstention where applicable. Save each completed sheet independently and report protocol, count, disagreements and agreement before aggregating. No ratings or agreement values currently exist.

## Game demonstration

The user's running Skyrim process and SKSE logs were inspected. SKSE_HTTP loaded; AnimaConnector did not. Runtime 1.7.104.0 differs from the source's explicit 1.6.318.0 compatibility guard. No generated in-game dialogue screenshot exists. `raw_results/game_availability.md` records checks; do not substitute decorative game scenes or local-lab screenshots.
