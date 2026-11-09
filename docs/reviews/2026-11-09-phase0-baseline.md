# Phase 0: reproducible release baseline

Plan and implementation review used the development/design-review skills. Both are self-reviews; no independent/teammate approval or live readiness is asserted. The owner requested phase 0, supplied repository-local author identity, and subsequently instructed: create commits, merge into local main when final, **do not push**. That explicit local merge instruction supersedes the ordinary PR path for this task; remote CI/review is not invented or bypassed by a success claim.

## Baseline and reviewed plan

The starting checkout was main 28df175 with the previously reviewed known fixes and production plan on feature/known-issue-fixes. Committed that work as **31aee43**, preserving the reviewed 994-pass/6-skip evidence, then created feature/phase0-release-baseline for this slice.

Before coding, reviewed R0.1/R0.2 against the contracts, module ownership and existing build/setup scripts. Keep a single dependency file and project venv. Reconcile current documentation and exact defaults; add Linux/Windows Python CI and Windows native CTest from pinned inputs. Avoid model/game/private tooling in ordinary tests. Add only a native-only build switch and the readiness correction necessary for repeatable startup. Validate configuration, source-only offline startup and workflow syntax, then record exact local evidence and unrun platform gates.

Plan review findings:

- Normal setup prepares model/media assets and is inappropriate as CI's offline test entry point. Install public package/build dependencies directly through existing helpers and the pinned requirements, without normal setup/model preparation.
- The release build requires Caprica/Papyrus interfaces unavailable on source-only runners. Native-only must build real native targets and execute CTest, while skipping Papyrus/package creation; it must never pretend to produce an installable release.
- Readiness compared the text after == including environment markers to an installed version, causing false outdated detection with onnxruntime-gpu. Parse requirements and evaluate markers before version checks. Include the shared runtime-selection setup helper in the setup fingerprint.
- Remote/platform execution is external evidence: workflow YAML and Linux tests cannot establish a Windows build, GPU operation or native gameplay.

## Changes and implementation self-review

### Existing fixes integrated as a concrete commit

31aee43 includes the known fixes, restored preflighted rollback and production plan. No new game installation, corpus, model weights, saved state or credentials are committed. Source remains on feature branches until the explicit final local-main merge.

### Runtime/setup baseline

Readiness now uses packaging's requirement parser for environment markers and pinned specifiers, retains CPU/CUDA Torch build-tag handling, and still fails cleanly when dependencies are missing/malformed. The runtime-selection helper affects the setup fingerprint. Regressions cover active/excluded requirements, Torch local tags and setup-input invalidation. Initial reproductions failed for both active/excluded markers; an initial test-helper argument collision was corrected before the proper failing reproduction.

The native build helper exposes --native-only, still verifies clean pinned source checkouts, builds the DLL/console and both native test executables, runs CTest with --no-tests=error and writes JUnit. It skips Papyrus compilation and release packaging only after successful CTest. A focused test verifies native targets/CTest execute and packaging is not invoked. Native startup's version label is aligned with existing 0.5.7 metadata/package version; Windows compilation remains pending.

### CI

.github/workflows/baseline.yml defines Ubuntu 24.04/Windows 2022 Python 3.11 jobs plus a Windows native job. Jobs use the project venv, pinned action source revisions, limited read permissions, timeouts and superseded-run cancellation. Python uses the existing Torch-selection and GPU ONNX ordering helpers and the single requirements file; no new dependency list is introduced. Editable installation and pip check precede the complete pytest suite and offline CLI smoke. CTest and pytest reject empty/unexecuted suites through their normal failure behavior. Artifacts contain only JUnit and the actual checkout revision, with seven-day retention; pip package caching excludes model/data/environment directories. LFS media, model downloads, private game tooling, provider secrets and paid runs are not enabled in ordinary CI.

Action revisions were resolved against official action repositories and YAML validated with actionlint 1.7.7. The GitHub release API briefly rate-limited the validator lookup; the pinned official release asset was used instead. This is syntax validation, not an executed hosted job. Links: [setup-python](https://github.com/actions/setup-python), [checkout](https://github.com/actions/checkout), [upload-artifact](https://github.com/actions/upload-artifact).

### Documentation and startup

README/setup/connector guide now agree on small.en, GPU-capable versus CPU-only speech paths, H/F8/F7 controls, scripted/default/auto/explicit-provider behavior, current ESP/SEQ/Papyrus packaging and completion/display versus hearing. Obsolete text-box/caption defaults were reconciled with the current native INI and source. Compatibility is scoped to exact declared runtimes, with old 0.2.0 evidence labelled historical rather than current-candidate live qualification. Research documents/results were left unchanged.

RELEASE_BASELINE.md records baseline evidence, compatibility scope, workflow reproduction, required remote checks and the skip inventory. An offline lab integration test starts HTTP with an empty private-media root and isolated state, verifies four personas/source illustrations and unavailable optional media/services, and closes resources. Its initial fixture assertion assumed media fields were top-level; it was corrected to the existing nested response contract rather than changing runtime output.

## Local validation and limitations

Final local results are recorded below. Checks include full offline Python regression, source-only lab HTTP integration, actual lab CLI startup with temporary SQLite state, offline scenario CLI, public provider/connector/scenario parsing, actionlint, dependencies, compilation, whitespace and documentation links. A portable C++ delivery test is built/run on Linux as extra evidence only; this does not replace the full Windows native job.

No Windows runner, MSVC/CommonLib build, Papyrus packaging, model inference, GPU, microphone, game installation or paid-provider experiment has run in this task. Runtime support, live quality and performance remain later-phase gates. Python/build dependencies installed on hosted runners still need their first clean-platform resolution/run; local package consistency does not prove that fresh remote installation succeeds.

Remote fetch succeeded, but a write dry run reported missing GitHub credentials. The owner then explicitly requested no push and local commits/main integration only. No PR/remote workflow/branch protection is created or claimed. Windows Python and full native CI evidence must be collected after the owner pushes. Phase-0 implementation can be locally integrated; its cross-platform acceptance gate remains pending that evidence.

Final local evidence: `.venv/bin/python -m pytest -q --tb=short --junitxml=output/ci/phase0-local.xml` — **999 passed, 6 skipped** in 30.39 seconds, no failures. The six skips are the documented Linux Windows-API and absent Mantella.esp cases. `pip check`, runtime/scripts/lab compilation, actionlint 1.7.7, whitespace checks and changed documentation links passed. Actual offline scenario CLI returned model_success using offline-demo with one scripted call. Actual offline lab CLI served four public personas with temporary state and shut down cleanly; the separate empty-media HTTP test passed. Connector/provider examples and all three public scenario fixtures validated. The portable C++ delivery executable compiled under g++ and passed. None of these substitutes for the unexecuted Windows Python/MSVC/CTest checks.

Local integration record: phase-0 implementation is committed with the supplied repository-local author identity and merged into local main under the owner's explicit instruction. See Git history for the resulting revision; no push is performed. Server-side CI and review/branch-rule configuration remain pending the owner's later push and platform runs.
