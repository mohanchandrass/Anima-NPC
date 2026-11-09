# Phase 0 release baseline

This page describes source and automation readiness for the local Windows, single-player release. It does not certify native/game compatibility, audible delivery, GPU fit or production performance. Follow the [production-readiness plan](plans/PRODUCTION_READINESS.md) for later gates.

## Source and scope

Known-issue fixes and the plan were reviewed and committed as `31aee43`, based on main `28df175`. Phase-0 automation and documentation are on `feature/phase0-release-baseline`; the final implementation revision is recorded by Git and by CI, not a moving version label. Each CI test artifact includes `revision.txt` from the actual checkout. A pull-request run tests GitHub's merge checkout; use its recorded SHA when reproducing it.

Current changes retain the reviewed fixes: bounded repair without local truncation, explicit-model provider selection, preflighted rollback, Windows-path/case handling and correct expired-CV reporting. See the [known-issue review](reviews/2026-11-09-known-issues.md) and [phase-0 execution review](reviews/2026-11-09-phase0-baseline.md) for executed checks and outstanding gates. Historical research metrics retain their original source revision and are not current release qualification.

## Active defaults and guarantees

| Item | Current behavior |
|---|---|
| Native source version | 0.5.7; one Windows x64 DLL, exact declared runtimes below |
| Text / microphone / options | H / hold F8 / F7, subject to installed INI overrides; activation may start a session and show a hint without opening text |
| Bare connector example | Preview pipeline with scripted offline provider; no paid model call |
| Explicit live launcher | `run_anima_skyrim.bat` selects Gemini and local Piper/SAPI speech; the installed console uses the auto provider profile. Explicit profile arguments win |
| Auto model/provider | Native Gemini model names select Gemini; vendor/model names select OpenRouter even with both keys configured |
| STT | Resident small.en; GPU preferred, CPU INT8 fallback with a warning |
| Embeddings | MiniLM/ONNX; CUDA preferred with visible CPU fallback; scoped retrieval only |
| TTS | Piper/SAPI CPU paths with `--local-speech`; optional Chatterbox after preparation prefers supported CUDA; audio route defaults to system |
| Optional components | CV, retrieval bindings, private character media and speech can be absent; declared fallback behavior remains available |
| Completed memory | Browser display acknowledgements and game-completed receipts are distinct; neither proves human hearing. A game completion may describe subtitle display despite failed audio |
| Provider support | Gemini/OpenRouter transports and offline fixtures exist; every additional endpoint/model requires its own capability/quality check |

Settings contracts and the packaged INI are authoritative for defaults. Local configuration is preserved; phase 0 does not rewrite an existing `.env`, game installation, voice/media override or interaction database.

## Runtime compatibility evidence

| Game executable | Address Library | Source/build scope | Live qualification |
|---|---|---|---|
| 1.6.318.0 | `versionlib-1-6-318-0.bin`, header 2 | Explicit native allowlist and metadata/tests | Current candidate not live-qualified |
| 1.7.104.0 | `versionlib-1-7-104-0.bin`, header 5 | Explicit native allowlist and metadata/tests | Historical 0.2.0 load/text receipts exist; later source and reported gameplay samples do not qualify this candidate automatically |
| Other runtimes / VR | None declared | Rejected or outside scope | Unsupported by this baseline |

A successful build does not change `live_game_verified` in packaging metadata. Current live claims need source/binary hash, runtime/mod profile, configuration and a fresh run record. See [connector build/installation](SKYRIM_CONNECTOR.md#build-and-installation).

## Automated checks

[Release baseline workflow](../.github/workflows/baseline.yml) runs on main/feature pushes, main pull requests and manual dispatch:

- Python 3.11 on Ubuntu 24.04 and Windows 2022, each using project `.venv`, the single pinned requirements file, the existing Torch/ONNX runtime selection, editable installation and `pip check`.
- Full Python contract/unit/offline integration regression and an offline scenario CLI smoke command. The suite includes HTTP lab startup against an empty private-media root with temporary state.
- Windows native DLL/console/test build from pinned native sources and CTest with `--no-tests=error`. `--native-only` bypasses Papyrus compilation and release packaging, so CI needs no game, Papyrus SDK, private media or model weights.
- JUnit results and checkout revision only, retained for seven days. Pip package downloads may be cached against requirements; environments, user knowledge, model assets, saved state, credentials and native release binaries are not cached/uploaded.

Actions use immutable source revisions and read-only repository permissions. Jobs have timeouts and cancel superseded runs. No API secrets are passed to them. Downloading public build/package dependencies is setup work; test execution uses offline model settings. No model preparation, game installation or paid/live evaluation is part of default CI. Such work remains an explicitly authorized operator task outside this workflow.

Required check names to configure in repository branch rules after the workflow is available: `Python 3.11 (ubuntu-24.04)`, `Python 3.11 (windows-2022)` and `Native Windows build and CTest`. Adding this file does not configure server-side branch protection or prove a job ran.

## Reproduce checks locally

For application setup use [SETUP](../SETUP.md); it deliberately prepares local model/media assets and therefore is not the source-only CI entry point. In a prepared Python 3.11 project environment:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q --junitxml=output/ci/python.xml
.\.venv\Scripts\python.exe -m npc.runtime fixtures/scenarios/smith_available.json
.\.venv\Scripts\python.exe -m tools.npc_lab --offline --port 8766 --state-db output/npc_lab/baseline-check.sqlite3
```

Stop the lab with Ctrl+C after checking `http://127.0.0.1:8766`. The separate state path avoids mixing test history with the default state. Without private media it uses source illustrations and exposes voice/model unavailability. Basic scripted behavior is not a model-quality benchmark.

On Linux, substitute `.venv/bin/python`. To create a clean contributor environment without preparing models, follow the workflow's installation steps, including CPU Torch selection and the GPU ONNX wheel installed last. Do not run normal setup and describe it as a no-model-download check.

Native source checks on a Windows x64 compiler host:

```powershell
.\.venv\Scripts\python.exe scripts/build_skyrim_connector.py --fetch --native-only
```

The usual build without `--native-only` still requires the separate Papyrus tooling and produces a release ZIP. Neither command installs into a game. Review the [team workflow](TEAM_WORKFLOW.md) before merging; use the exact candidate SHA for all evidence.

## Skip inventory

| Test area | Linux expectation | Required execution/evidence |
|---|---|---|
| Windows known-folder API (`test_skyrim_console.py`) | Skip | Windows Python job |
| Windows capture ctypes ABI (`test_skyrim_windows_capture.py`, 3 cases) | Skip | Windows Python job; actual rendering/capture still needs a game smoke test |
| Conditional symlink and Git tooling tests | Run where available | Git is present in hosted jobs; symlink checks may skip on restricted Windows hosts, which must be reported |
| Optional Mantella.esp reference comparisons (`test_skyrim_anima_esp.py`, 2 cases) | Skip when reference absent | Documented optional comparison on a machine with that reference; not a runtime dependency or release proof |
| Native delivery/runtime C++ tests | Not the Windows build | Native Windows job runs both executables through CTest |
| Actual game/audio/GPU/microphone behavior | Outside default suite | Separate phase 1/2/5 qualification; never reported as CI passes |

A Windows job must not silently retain the four Windows-only Linux skips. Unexpected skips/failures require investigation; no empty suite is accepted. Optional-reference skips do not waive native ESP generation/contract tests, which still run in the ordinary suite.

## Remaining phase-0 gates

Local implementation and Linux validation are separate from published/integrated CI evidence. Phase 0 is complete only after the same committed candidate passes both Python platforms and the Windows native job, and normal repository review/integration is complete. If this host cannot execute or dispatch Windows checks, report that explicitly rather than claim the gate passed. Later phases still own live quality, latency, hardware contention and recovery qualification.
