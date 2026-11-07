# Local RAG inside NPC-main

RAG is a separate internal component under `src/npc/rag/`. The default configuration calls it directly inside the NPC application; no second RAG project, frontend, terminal or HTTP service is needed. It uses the same `.venv` and pinned `requirements.txt` as the rest of NPC-main.

```text
NPC-main/
  src/npc/rag/             Retrieval adapters and evidence checks
    backend/              Document loading, chunking, GPU-preferred embeddings and Chroma
  Knowledge base/kb/      Local game documents
  data/rag/               Generated local index and embedding cache
  configs/rag/            Trusted world/NPC/source bindings
```

The backend reuses the supplied Generalised-RAG-System implementation, without its test frontend/chat/TTS/server. Local knowledge, model cache and index are ignored by Git. Public synthetic test data stays in `tests/` and `fixtures/` as appropriate; do not commit the owner's dialogue dump or generated model/index artifacts.

## Setup and launch

`setup.bat` installs the single requirements file, initializes the local embedding model, explicitly synchronizes the knowledge index, and runs offline regression. The first setup can download the embedding model. It preserves `.env`, player state and local media overrides. If using the already-created project environment directly:

```powershell
.\.venv\Scripts\python.exe scripts/setup_environment.py --no-update
```

The existing dialogue corpus is now copied into `Knowledge base/kb/Dialogue_2015.txt`. Its cached encoder was copied into `data/rag/models/`; the NPC-owned index is built fresh rather than modifying or copying the live external database. The original Desktop/RAG installation remains untouched and is no longer needed by the default configuration.

Start Anima from NPC-main with the same provider configuration as before:

```powershell
.\run_anima.bat --live --config configs/providers/openrouter.yaml --rag-config configs/rag/dialogue.example.json
```

Use `--offline` instead of `--live` for the scripted preview; scripted output cannot demonstrate natural RAG answer quality. Provider keys/model stay in `.env`. Local retrieval itself makes no LLM calls and consumes no OpenRouter/Gemini request quota; live response generation still needs provider access/quota. No API calls are part of local index preparation or default tests.

**Inspect context** shows retrieval status, original source/chunk/record IDs, the short citation-label map, and exact provider-bound passages. Source IDs confirm which context was supplied; they do not prove semantic truth.

## Documents and maintenance

Add/edit game documents under `Knowledge base/kb/`, then stop the app and run:

```powershell
.\.venv\Scripts\python.exe -m npc.rag sync
.\.venv\Scripts\python.exe -m npc.rag status
.\.venv\Scripts\python.exe -m npc.rag query "Who is Eorlund Gray-Mane?" --source Dialogue_2015.txt
```

`prepare` initializes/downloads the configured CPU encoder and syncs the corpus; `sync` is the explicit incremental ingestion step. Queries/turns do not ingest, watch files or download a missing model. Missing artifacts report unavailable and require preparation. Each checkout builds its own generated index; original absolute-path metadata is deliberately checked rather than silently reassigned after a move.

The backend retains text/Markdown, structured JSON/CSV, HTML, text PDF and DOCX ingestion, source/page references, unchanged-file reuse, edit/delete synchronization and last-good revision behavior. The internal file lock has a short wait. Use one application owner and explicit ingestion outside interactive turns. There is no background indexing service.

## Configuration and bounds

The [example](../configs/rag/dialogue.example.json) selects `backend: local`, `kb_dir: Knowledge base/kb` and `data_dir: data/rag`, anchored to NPC-main. It binds the village lab NPCs (Mira/Elin/Daren) to the exact dialogue filename. Iona's separate world is unbound. Add another game's documents and matching canonical game/world/NPC/source bindings; player text cannot select permissions. Source filtering happens before ranking and is checked again afterward. File-level binding is not per-speaker/quest authorization inside a combined dump.

Up to three matches are normalized through the same eligibility checks for local and optional HTTP transports. Existing per-turn deadlines, cancellation, source attribution, whole-excerpt packing, output validation and one-repair limit remain. The local adapter admits one CPU query at a time; caller timeout/cancellation does not release native-work admission until that worker actually finishes. A second query gets unavailable instead of joining a queue. CPU work and encoder memory still need measurement alongside a real game; game performance is not signed off.

For an intentionally external legacy service only, select `backend: http` and `base_url`. HTTP is optional compatibility; it is not used by the example or required for local setup. Without a RAG configuration, the previous no-retrieval behavior is retained.

## Dialogue and answer-quality limits

`source_format: dialogue_export` recognizes the current tab-separated FormID export. It conservatively omits personal/second-person lines, questions and selected uncertain state variants, retaining source/record provenance. Normal reference documents remain unchanged. Dialogue speaker/conditions remain unknown; no identities or quest conditions are invented. This is not a universal fact extractor or truth guarantee and loses some useful lines.

Dialogue knowledge prompts retain previous player questions but omit generated NPC replies as unverified facts; saved history stays intact. Simple follow-up subject resolution changes the retrieval query, not the original input/world facts. Prompt-only short labels map to original packed evidence IDs at validation. A narrow lexical check rejects copied irrelevant authored restrictions and uses the existing repair, without relaxing boundaries or adding canned topic answers.

Raw dialogue retrieval, temporal grounding and model answer quality still have known gaps. Historical descriptions can be mistaken for current events/locations; valid citations alone cannot detect that. This packaging change does not claim to fix those quality issues or exhausted API quota. See the [local integration plan](plans/I05-local-rag-component.md), [attribution review](reviews/2026-11-01-dialogue-attribution.md) and [topic review](reviews/2026-11-01-current-topic.md).


GPU-first testing update: setup installs the GPU ONNX runtime last; MiniLM prefers CUDA with CPU fallback. CPU operation emits a model-specific warning. Ingestion/query admission remains bounded and does not change knowledge authorization. See [GPU/latency self-review](reviews/2026-11-06-gpu-latency.md).
