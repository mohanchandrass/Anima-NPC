# Feature merge into main: integration self-review

The owner explicitly requested merging the feature branches into main and pushing. The checkout already contained an unfinished merge of feature/whisper-small-en (3c1a099) into main (65a8302). The other local/known remote feature/skyrim-pipeline-context-handoff branch (26d2fd1) is already an ancestor of main. No branch deletion or history rewriting is needed.

Plan and implementation review are self-reviews under the repository development/design-review workflows. Resolve the existing conflicts by retaining both main's conversation rules, knowledge authorization, console/native/system/Piper playback and per-attempt retry bounds and the feature's GPU-first resident Whisper, CPU warnings, independent fresh CV, strict schema option and stage timings. Keep tests from both sides, adapt only explicitly changed contracts, then compile and run the combined suite before completing the requested merge. User authorization to merge/push takes precedence over the ordinary PR workflow for this operation.

## Resolutions

- Retain main's conversation move/material selection, already-told evidence exclusion and name allowlist; instrument generation/repair separately while preserving the aggregate model stage and transient retry limit.
- Retain main's connection pool lifecycle and the feature's optional strict response schema. Remove duplicate auto-merged stage_ms fields rather than override their finite/nonnegative constraints.
- Retain opening-task management, native/system/Say audio staging and both diagnostic interfaces. Keep per-request TURN_TIMING records distinct from latest-turn TURN_LATENCY values to avoid corrupting either map. Context publication still uses authored rules; optional capture/CV stays in its independent task with session/revision/TTL guards.
- Preserve Piper/SAPI routing alongside resident Whisper. Remove obsolete no-op lifecycle methods which otherwise shadowed the new worker methods. Piper shutdown also closes its inherited STT worker, and the parent lifespan starts/closes a shared Piper/STT engine only once.
- Retain both sets of added tests. Update the older blocking-CV assertion to the owner's explicit nonblocking requirement and assert independent visual-task progress. Isolate a Piper-only HTTP fixture from real STT startup; separate Whisper process tests still verify resident reuse/cancellation/cleanup. Keep absent STT represented as none in TURN_TIMING for text input.

## Validation

Final full offline regression: `.venv/bin/python -m pytest -q --tb=short` — **907 passed, 4 failed, 3 skipped** in 15.22 seconds. This is not an all-passing suite.

All four failures reproduce against an isolated archive of original main 65a8302, using its own source/tests:

- Three `test_protected_folders_are_refused` cases in test_skyrim_console_install.py: Windows-style paths become different absolute paths on this Linux host.
- `test_resolver_survives_missing_and_corrupt_plugins_and_caches` in test_skyrim_piper.py: case-insensitive plugin-name expectation fails on the case-sensitive host.

Baseline focused run: **34 passed, 4 failed**, with the same four failures. They were not skipped, weakened or fixed as unrelated merge changes. The three full-suite skips require Windows known-folder APIs or an absent Mantella.esp reference. No new live game, Windows, GPU, microphone or paid API verification is claimed.

Runtime/scripts/lab/CV compilation, dependency check and git diff whitespace check passed. Self-review verdict: resolved feature integration with no remaining new test failures; pre-existing cross-platform failures remain visible. Full production readiness is not asserted.

Remote fetch failed because HTTPS GitHub credentials are unavailable in this workspace. Therefore current remote freshness and push success cannot be asserted from the cached origin/main reference. The user was asked to authenticate Git without sharing a token in chat. Finish local merge bookkeeping and retry authenticated fetch/push without force when credentials are available.
