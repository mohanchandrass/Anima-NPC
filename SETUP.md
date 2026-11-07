# Set up Anima

## Windows: recommended path for teammates

Install **Python 3.11** (with the Python launcher), Git and **Git LFS**. Sign in with an account that has access to this private repository. Clone the repository, then double-click **[run_anima.bat](run_anima.bat)**. It checks the installation and runs setup automatically when needed. No administrator privileges, GPU, Node, Skyrim or API key are needed for basic setup.

```powershell
git clone https://github.com/mohanchandrass/NPC.git
cd NPC
.\run_anima.bat --offline
```

`setup.bat` first attempts a fast-forward-only pull of the current tracking branch, then finds Python 3.11, creates/reuses the project's `.venv`, installs the single pinned `requirements.txt`, installs the project in editable mode, checks dependencies, creates `.env` from `.env.example` **only if absent**, and runs the full offline Python regression. It stops on a failed step and keeps the result visible. Existing credentials, saved state and game files are preserved. It does not install Python globally or make LLM calls. It hydrates the owner-approved private Git LFS runtime pack when necessary and verifies/extracts it into the app's local asset directory. For automation use `setup.bat --no-pause`; it returns a nonzero code on failure. Rerunning is supported. Dirty working trees, detached/no-upstream branches and unavailable network skip the source update with an explanation; setup continues using local source. It never resets, stashes or switches branches. `setup.bat --no-update` explicitly uses local source. Missing packages/media still require network access.

The first launch above is an **offline preview with scripted responses**. The private repository now includes the four posed character models, actual game screenshot backgrounds, Futura fonts, and selected Windows Piper voices in one approximately 280 MiB Git LFS archive. `setup.bat` installs it automatically; teammates do not need Skyrim, Blender, Java or Mantella to use the supplied runtime. API generation still needs a separately configured key. Source-only checkouts without the pack retain the illustrated/no-voice fallback.

## Enable real model responses

Open the newly created local `.env` and set your own key and model. For the default Gemini profile:

```dotenv
GEMINI_API_KEY=your-own-key
NPC_LLM_MODEL=gemini-3.5-flash-lite
```

This model was tested locally on 2026-10-31; availability/quotas may change. Model selection is configurable, not hardcoded. Never commit `.env`, paste keys into shared chat, or put them in public YAML/browser code. `setup.bat` will not replace an existing `.env`.

Run `run_anima.bat` to use valid live configuration (may incur API charges). Without valid provider settings it clearly starts a scripted offline preview; `--live` instead requires valid settings and reports configuration errors. Keep the terminal open; Ctrl+C stops it. Restart after editing provider settings, then refresh the browser. Default URL: `http://127.0.0.1:8765`. Another instance can use `--port 8767`.

For OpenRouter, set `OPENROUTER_API_KEY` and its model ID, then run:

```powershell
.\run_anima.bat --config configs/providers/openrouter.yaml
```

See [provider configuration](configs/providers/README.md). OpenRouter is covered offline but remains untested live. Manual testing allows 30 seconds by default (`--development-timeout 1..60`); `--realtime` preserves the core three-second limit. Timeout, quota, authentication and server failures remain typed fallbacks; a fallback is not successful model generation.

## Included runtime media and optional source rebuilds

The owner-approved private runtime archive and per-file hash manifest are under [assets/runtime](assets/runtime/README.md), tracked through Git LFS. Raw game archives, conversion sources/tools and user state remain ignored. Normal teammates run setup and use the supplied assets. To rebuild/replace them from an installed game, follow [local media setup](configs/npc_lab/README.md):

| Optional feature | Local prerequisite |
|---|---|
| Actual character models and native blink/speech/mood morphs | Installed Skyrim SE; portable Blender 4.5.9 + PyNifly 30.2.0; run the importer with `--facial --relaxed` |
| Mantella Piper voices | Installed MantellaSoftware/Piper; run the existing voice-copy command |
| Original Skyrim Futura dialogue/interface font | Installed English Skyrim SE; Java + portable FFDec 26.4.0; run the font importer |

Private files stay under ignored `assets/private/npc_lab/`; portable conversion tools under `.tools/`. Game files are only read, not changed. Without private fonts, Anima uses openly licensed fallback fonts and says so in the inspector. The installed pack uses actual Skyrim screenshots for Warmaiden's, the Bannered Mare, Whiterun and Dragonsreach. Public original illustrations remain fallback-only. Screenshot source/attribution metadata is included in the pack. Facial movement uses native game morph shapes with app-driven blink/mood/speech timing; it is not full skeletal animation or phoneme-aligned lip sync.

## Common setup issues

| Symptom | Action |
|---|---|
| Python 3.11 not found | Install Python 3.11 and its launcher; reopen the terminal and retry. |
| Existing `.venv` is incomplete or uses another Python | Move it aside manually and rerun. Setup never deletes an existing environment automatically. |
| Package installation fails | Restore internet access or fix the reported pip/index error, then rerun setup. |
| Missing model/key on live launch | Fill `.env`, or use `run_anima.bat --offline` for the scripted preview. |
| Port already in use | Stop the existing Anima terminal or use another `--port`. |
| Illustrations/no voice/fallback font | Run `setup.bat`; it downloads missing LFS media automatically. Source-only development still works without the pack. |
| Runtime archive is an LFS pointer or fails checksum | Install Git LFS, verify repository access, then rerun `setup.bat`. Do not use a downloaded source ZIP. |
| API fallback | Inspect the error code and exact input in **Inspect context**; check network, model availability and quota. |

## Manual commands and contributor checks

If you prefer explicit PowerShell commands:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m npc.rag prepare
.\.venv\Scripts\python.exe -m pytest -q
```

Copy `.env.example` only if `.env` does not already exist. Activation is optional; explicit paths avoid PowerShell execution-policy issues. Python 3.11/Windows is the verified setup; other combinations need testing. There is one requirements file. `pyproject.toml` contains package metadata/test discovery, not a competing dependency lock.

Use the [development workflow](.agents/skills/develop-npc-component/SKILL.md), [repository structure](docs/REPOSITORY_STRUCTURE.md), [context preview](docs/CONTEXT_BUILDER.md) and [generation guide](docs/GENERATION_VALIDATION.md). Optional UI tests require Node 22+ and Chrome; they are separate from basic setup and documented in [Anima](docs/ANIMA.md).

Current implementation is a core context/policy/generation/structural-validation slice with initial social persistence and a separate local companion. RAG, production game delivery and hosting are future work. `.venv`, `.tools`, `.toolds`, `.env`, generated state/logs and private assets are ignored. The user-controlled KB folder is not automatically ignored; check privacy before tracking its contents.

Runtime installation is idempotent. Previously package-managed files update automatically; separately modified local media is preserved. Neither `.env` nor saved player data is part of the archive. Keep the repository private; upstream asset rights/licences are retained. See [bundle provenance](assets/runtime/README.md) and [immersion review](docs/reviews/2026-10-31-immersion-refinement.md).

To ensure an existing teammate install uses exactly the shared runtime media, run **`setup.bat --sync-media`** (setup pulls the current tracking branch itself). This intentionally replaces local runtime model/font/voice/scene overrides with verified bundle versions; `.env` and saved player state remain untouched. For automated checks combine `--no-pause --sync-media`. Normal setup preserves local overrides. Matching media does not force identical cloud-model responses or personal relationship state.

Normal launches perform local readiness checks only: pinned package versions, project import, configuration presence, setup fingerprint and installed media presence/size. Missing/outdated installations trigger setup once, including regression. Healthy launches do not run pip, tests or a Git pull. To request source updates explicitly, run setup. Full media hashes are verified during setup rather than on every launch. Same-size local edits are not detected by the fast launch check; use `setup.bat --sync-media` to verify/restore the shared media. Python 3.11, Git, Git LFS and private-repository authentication remain machine prerequisites; the scripts do not install global software.

## Included local RAG component

Setup now also installs the pinned Chroma/ONNX CPU dependencies into the same `.venv`, prepares the local encoder (first setup may download it), and synchronizes `Knowledge base/kb/` into ignored `data/rag/`. No separate RAG environment/server is needed. Add your own documents, then use `python -m npc.rag sync` with the project interpreter outside interactive use. See [RAG integration](docs/RAG_INTEGRATION.md) for the single-process launch and trusted source bindings. Local preparation does not call the dialogue-model API or consume its request quota. Raw corpus, index and model cache remain ignored; existing external installations and player state are preserved.

## Skyrim speech input

Setup automatically downloads and validates the pinned English Whisper small.en model (GPU preferred, CPU fallback) into ignored `data/stt/`. The Skyrim connector launcher also prepares it if missing, preserving explicit external STT endpoints. After setup, run `run_skyrim_connector.bat`, open NPC dialogue and hold F8 to speak; release to transcribe. No manual model placement, GPU/CUDA or system FFmpeg installation is needed. First preparation needs internet; cached operation is local. TTS remains separate. See [connector speech](docs/SKYRIM_CONNECTOR.md#player-speech-input) for limits and readiness checks.

## Chatterbox Turbo voice output

Double-click **[setup_chatterbox.bat](setup_chatterbox.bat)** once. It installs the compatible speech dependencies in the existing `.venv`, downloads the pinned Turbo model and prepares reference voices from installed Skyrim files. Subsequent connector launches enable it automatically. Game discovery is automatic where possible; otherwise run `setup_chatterbox.bat --game-directory "D:\Games\Skyrim Special Edition"`. First setup needs internet and several GB of free disk space. No manual model placement or separate TTS server is required. Setup automatically selects CUDA 12.4 wheels for conservatively supported NVIDIA GPU 0/driver combinations and falls back to CPU wheels otherwise. CPU synthesis can exceed conversational deadlines; verify GPU performance alongside Skyrim. See [NPC voice setup and limitations](docs/SKYRIM_CONNECTOR.md#chatterbox-turbo-npc-voices).


GPU-first testing: setup installs GPU-capable ONNX binaries after the requirements install, alongside the existing CUDA-aware Torch selection. Local models prefer supported GPUs and print model-specific warnings when falling back to CPU. Run setup again after this update; no manual model-file movement is required. Ollama retains control of CV model offloading; partial CPU use is reported. See [GPU/runtime review](docs/reviews/2026-11-06-gpu-latency.md).
