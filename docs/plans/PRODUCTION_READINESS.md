# Anima production-readiness plan

Date: 2026-11-09. Status: **reviewed plan, not release approval**. Phase-0 execution is tracked in the [release baseline](../RELEASE_BASELINE.md) and [execution review](../reviews/2026-11-09-phase0-baseline.md); Windows/remote CI gates remain separate from local implementation.

## Scope and baseline

Target a reliable **local Windows, single-player Skyrim release** plus the existing browser companion for testing. Keep the modular Python application, native SKSE connector, replaceable API provider and optional local models. Support only declared, actually tested game/runtime and device combinations. Hosting, accounts, multi-user service operation and universal mod compatibility are separate work.

Planning baseline: main 28df175 plus the then-uncommitted fixes on feature/known-issue-fixes. Those reviewed fixes and this plan were committed as 31aee43 during phase 0. The [known-issue review](../reviews/2026-11-09-known-issues.md) records **994 passed, 6 skipped** for that earlier slice; newer phase-0 evidence belongs in the execution review, not in historical research results. Local integration is distinct from the pending Windows/remote CI gates. Preserve other working-tree changes. This plan changes no runtime code and authorizes no publication, game installation, recording or paid benchmark run by itself.

Use [contracts](../CONTRACTS.md), [team workflow](../TEAM_WORKFLOW.md), [placement](../REPOSITORY_STRUCTURE.md), [evaluation](../EVALUATION.md), [resources](../RESOURCE_PROFILE.md), [Skyrim connector](../SKYRIM_CONNECTOR.md) and [social state](../SOCIAL_STATE.md) as references. Where older documents differ from current code, reconcile the claim rather than implement the old text automatically.

## What production-ready means for this release

- Fresh installation, update and rollback work on declared Windows targets without replacing local credentials, media overrides or saved interaction state.
- Normal conversation, interruption, combat, menu close, NPC changes and save/load transitions cannot dispatch obsolete speech or commit an undisplayed reply as completed memory.
- Dependencies can fail without hangs, unbounded workers, overlapping voices or misleading readiness. CPU fallback and subtitles-only operation are explicit degraded modes.
- End-to-end latency and game resource impact are measured on the actual target machine. Fast fallbacks are counted separately from successful generated speech.
- Knowledge eligibility, state isolation and output validation remain enforced. Human-reviewed dialogue quality is acceptable on a held-out set; citations alone are not proof.
- A release has repeatable checks, a compatibility table, useful private diagnostics and one clear runbook.

## Order of work

| Phase | Outcome | Priority / dependency |
|---|---|---|
| 0 | Reproducible, accurately documented baseline | P0; first |
| 1 | Real performance and quality baseline | P0; after phase 0; informs tuning |
| 2 | Reliable interruption, recovery and native state handling | P0; start after phase 0, guided by phase 1 failures |
| 3 | Focused performance and dialogue corrections | P1; after phase 1 measurements |
| 4 | Setup, status and diagnostic polish | P1; after phase 0, finish against phases 2–3 |
| 5 | Release candidate qualification | P0 release gate; after phases 0–4 |

These are small deliverables and gates, not a fixed calendar estimate. Review each change before coding and run the relevant regression afterward. Do not bundle all phases into one implementation branch.

## Phase 0 — Establish one release baseline

### R0.1 Integrate the known fixes and reconcile current instructions

Owners: existing runtime/adapter/scripts/tests/docs directories.

- Review/integrate the current known-issue fixes through the normal authorized workflow.
- Reconcile README/setup/connector examples with current code: small.en, GPU preference/CPU fallback, actual text key H versus older F6 instructions, provider selection, speech backend choices and memory semantics.
- Distinguish automatic fallback, optional components and the exact native/runtime support matrix. Remove claims such as all compatible endpoints working unless evidence supports them.
- Keep immutable historical research evidence identifiable by its original revision rather than rewriting it to imply current measurements.

Acceptance: one recorded source revision; no unresolved merge changes; examples parse; documented controls/defaults match current configuration; source-only/offline startup works. A clean working tree and passing tests are baseline evidence, not production signoff.

### R0.2 Automate checks already required by the project

- Add Linux and Windows Python 3.11 offline regression jobs using the project environment and single requirements file. Include dependency consistency and relevant contract/integration tests.
- Add a Windows native build and CTest check using the existing pinned build inputs. Cache only reproducible dependencies, never credentials or player data.
- Make expensive model downloads and paid/live runs explicit opt-in jobs. Default regression must not require GPU, Skyrim, microphone, API credentials or private knowledge.
- Keep a test skip inventory: Windows-only checks must run on Windows; game-dependent checks need named manual evidence rather than permanent unexplained skips.

Acceptance: the same candidate revision passes Python checks on both hosts and native checks on the supported toolchain. Existing skips have reasons and a platform where applicable checks actually execute. Do not count an empty test job as success.

## Phase 1 — Measure the complete running system

### R1.1 Establish a small repeatable game benchmark

Owners: existing [evaluation workflow](../EVALUATION.md), fixtures/evaluation, thin scripts or benchmarks harnesses; reuse TURN_LATENCY, TURN_TIMING and stage_ms.

- Use at least three NPC roles and authored, ordinary generated, knowledge-bearing, repaired and failed turns. Separate typed input from recorded speech; use original public utterances and isolated test state.
- Record OS/game/SKSE/connector versions, provider/model/options, corpus revision, device placement, hardware, prompt/policy version and audio route with every run.
- Measure a cold start separately and at least 30 matched warm trials per main route; use 100+ trials where a p95 claim matters. Include timeout, repair, fallback and missing-receipt counts in the original denominators.
- Report STT, context/publication wait, retrieval, prompt, initial generation, repair, validation, TTS and delivery. Treat cumulative readiness/start metrics separately from additive stages. Distinguish native-start receipt from actual audible playback; measure physical/acoustic delay separately if making that claim.
- Use one small offline summary of the existing logs if manual comparison is inadequate. Do not introduce a telemetry server or another orchestration layer.

Initial targets from existing evaluation documents: warm generated text response-ready p90 <=3 seconds; speech release to validated audio-start p90 <=6 seconds; authored/silent core p95 <=100 ms. These remain targets, not achieved results. Resolve the gap between these targets and current per-call retry/TTS/development deadlines using measurements; deadlines are maximum waits, not advertised typical latency.

Acceptance: a reproducible baseline report with route-specific p50/p90/p95/max, success/repair/fallback counts and the largest actual bottleneck. No excluded slow failures or scripted responses presented as model benchmarks.

### R1.2 Measure GPU contention and recognition/voice quality

- Compare game alone, idle backend, normal voice conversation, RAG, and optional CV. Record game average/p95/p99 frame time, CPU/RAM, GPU utilization/VRAM, active worker count and recovery events.
- Include low/free-VRAM, GPU-unavailable and CPU fallback cases. Report actual loaded devices; installed CUDA/provider availability is not proof of GPU execution.
- Evaluate small.en on at least 30 short/long, quiet/noisy utterances with key Skyrim names. Report word/key-name errors, silence handling and decoding latency. Do not add a new recognizer before this identifies a problem.
- Listen to generated human and non-human voice examples, repeated voice switching and interrupted/recovered output. Measure speed and intelligibility together.

Acceptance: declared hardware profiles with truthful full-voice, degraded-CPU and subtitles-only behavior. Existing <=5% game p95 frame-time regression is an initial target to assess; define per-profile memory budgets from measurements. The old 512 MB core/CPU estimate is not a cap for all resident GPU models combined, and 4 GB VRAM does not imply all models must fit simultaneously.

## Phase 2 — Close failure and state-handling gaps

### R2.1 Harden cancellation and dependency recovery

Owners: adapters/skyrim, runtime, output; fake process/network/device boundaries in tests.

Exercise a hung/dead STT or TTS worker, GPU OOM, rate limit, transport loss, unavailable CV/index and repeated interruptions. Verify one active inference per admitted worker, bounded recovery, no process/file leaks, no blocked shutdown and no queue growing across retries. Check parent and mounted-app lifespans with shared Piper/STT instances. Surface unavailable/recovering states in health/status rather than treating every timeout as ready.

Measure screenshot capture shutdown: cancellation of a thread await cannot kill a hung native capture. If a real or reproduced capture can block teardown, isolate that operation in the smallest killable worker consistent with existing capture behavior; do not migrate all I/O to services.

Acceptance: deterministic injected failures recover or degrade within declared bounds; a 100-turn repeat/cancel stress run settles back to its baseline worker/file counts. Faults never release inference admission before actual work has stopped.

### R2.2 Verify delivery, restart and memory truthfulness

Owners: adapters/skyrim, state, shared contracts.

Walk cancellation/combat/NPC change/save-load races through final validation, audio preparation, staging, native start and completion. Replay duplicate/out-of-order/late callbacks and restart after ambiguous playback. Keep scope/NPC/request/delivery correlation, revision and expiry checks intact. Completed game receipts and browser display acknowledgements must remain explicitly different from verified human hearing.

Add only the persistent delivery/reconciliation state needed to prevent ambiguous replay or false memory after restart. If a receiver cannot reconcile, preserve unknown and require a fresh session rather than claiming exactly-once playback. Do not build a general distributed outbox ahead of a reproduced missing guarantee.

Acceptance: stale output never starts; duplicates never repeat social effects; interrupted/unknown output never becomes a fabricated completed exchange. Restart and save-branch isolation have integration tests and an actual game smoke record. An unsupported receiver guarantee is documented as a limitation, not silently assumed.

### R2.3 Protect game state and saved local state

- Review native Hold/Unhold ownership: SetDontMove(true) followed by unconditional false and ClearLookAt can overwrite state owned by quests/mods. Preserve prior supported state, or narrow/avoid the mutation where reliable ownership cannot be established.
- Review global KillSubtitles calls and engine subtitle resets for effects on other speakers/mods. Prefer modifications limited to Anima-owned entries where the engine allows it; verify vanilla dialogue and interruption coexistence in game.
- Test SQLite lock/write/disk-full/corruption failures, concurrent admission, backup/restore, schema migration compatibility and save-scope reset. Provide an explicit maintenance/export/archive path before growth limits stop admission; never silently delete authoritative deduplication data.
- Qualify installer rollback on disposable Windows game-shaped folders: incomplete backups, old manifests, partial installation, file locks and local overrides. Preserve credentials/media/player data and refuse unknown backup paths.

Acceptance: tested save/restore ownership boundaries and no unintended quest/movement/subtitle/state changes in the declared compatibility matrix. Disk/DB failures produce clear actionable outcomes. Release update and rollback both retain recoverable local state.

## Phase 3 — Tune only measured bottlenecks and failures

### R3.1 Improve response time without relaxing correctness

- Use phase 1 results to tune worker warm-up, existing CPU thread caps, speech budgets and device placement. Keep model residency and bounded reference caches; do not add unrestricted caching or concurrency.
- Decide whether the current speech backend meets each hardware profile. Compare a replacement only if measured speed/quality misses the target; keep validated subtitles available and do not make repeated cold worker restarts look like useful recovery.
- Check strict JSON-schema support on the exact configured endpoint before enabling it. Compare at least 30 matched ordinary/repair-prone cases with compatibility mode, keeping model/persona/input/limits constant. Record unsupported endpoints, tokens, repairs, latency and human-reviewed output quality.
- Change prompt/output budgets or retry policy only when samples justify it; preserve persona/knowledge constraints and max two model calls. Never reintroduce local truncation of qualified text or raw unvalidated speech streaming.
- Measure RAG with the actual corpus and a 10x fixture scale before optimizing it. Keep ingestion outside interactive turns; verify cold encoder/index behavior, source filters and whole-passage packing.

Acceptance: a before/after report on the same revision/settings/hardware, with improvement in the measured bottleneck and no regression in forbidden disclosure, qualification, voice selection or game frame time. No performance claim rests only on unit tests.

### R3.2 Polish conversation quality using held-out examples

Use at least 50 held-out cases spanning repeat/follow-up, indirect questions, permission/negation, unknown facts, unavailable/busy NPCs, abuse/apology and hidden knowledge. Include multiple personas, relationship states and three distinct NPC knowledge scopes. Reuse authored fixtures; keep them out of tuning prompts. Human reviewers score relevance, role consistency, continuity and preservation of constraints. Models may assist analysis but do not act as independent evidence.

Follow current [evaluation definitions](../EVALUATION.md): report routing, length, retrieval and semantic errors separately. Critical unauthorized source exposure, stale dispatch and unvalidated delivery must be zero in fixtures. Treat the older <=15% general boundary-error target as an experimental diagnostic, never permission for critical leaks. Set and freeze broader quality acceptance thresholds before the run; do not tune them afterward to make results pass. Preserve citations as provenance, not entailment.

Acceptance: saved redacted examples, raw counts and reviewer disagreement; targeted fixes for the largest observed failures, including false refusals. No blanket refusal policy, learned relationship classifier or permanent extra critic call is required.

## Phase 4 — Make the release easy to operate

### R4.1 Simplify setup and configuration

- Fresh-machine smoke test setup, first start, offline start, restart and update. Detect absent prerequisites once and give one repair action.
- Audit overlapping CPU/GPU ONNX distribution installation and CUDA/Torch library loading. Preserve the single requirements file; verify which wheel owns the effective runtime after fresh install and upgrade. Simplify installation only with evidence that the replacement keeps GPU-first execution and a working CPU fallback.
- Preserve explicit provider endpoints/keys, voice/media overrides and state. Preview active public configuration and clearly label paid provider mode versus scripted mode. Never print keys.
- Detect incompatible game/SKSE/library/plugin combinations before declaring readiness. The supported-version table is based on executed checks, with build-only coverage labelled separately.

Acceptance: a teammate can complete installation from the documented prerequisites without undocumented file moves; setup is repeatable, resumes preparation safely and preserves user configuration/data. Dependency checks and offline startup pass on declared hardware profiles.

### R4.2 Polish status, diagnostics and privacy

Show concise ready/loading/recovering/degraded states with the actual model/device and active voice. Explain CPU fallback, subtitles-only output, missing knowledge and disabled/stale CV without repeating modal warnings on every turn. Keep controls and instructions consistent across the console, mod and browser companion; retain keyboard/accessibility behavior and hidden-tab rendering limits.

Default production logs should capture identifiers, durations and typed outcomes. Audit MODEL_OUTPUT raw reply logging, which currently runs outside verbose mode; move private prompt/raw-response recording behind deliberate diagnostic opt-in. Keep visible dialogue logging a clearly labelled option if needed for manual testing. Ensure exported diagnostics do not include keys, local knowledge, saved player data or private file paths unintentionally. Keep existing rotation; no cloud log service is needed.

Preserve loopback binding and existing payload/path restrictions. Test origin/host handling and mutating endpoints against the intended local trust boundary. Add authentication only where the boundary review identifies a concrete missing control; no accounts or public hosting are implied.

Acceptance: a tester can identify the active backend/device/voice and recover from a common failure using the displayed instruction. Metadata-only diagnostics are useful, secrets never appear in default logs, and sensitive exports require an explicit choice.

## Phase 5 — Qualify and package a release candidate

Freeze one candidate revision, environment/model/native dependency pins and declared configuration. Build once and attach hashes/version information to the package. Keep the repository private and preserve the approved runtime-media archive/source-hash policy; do not add raw game archives or player data. Do not silently rebuild different binaries under the same version.

Required evidence:

1. Offline Python/contract/integration checks on Linux and Windows, Windows native build/CTest, dependency checks, and relevant existing UI tests. Skips are listed, with required platform cases executed elsewhere.
2. Fresh install, upgrade from the previous candidate and rollback on disposable Windows environments, preserving local settings/state. Original files and an undo point remain available.
3. Native game smoke checks for each advertised runtime: text, speech, role/voice switch, optional CV, unavailable dependencies, menu close, combat, NPC change, save/load and exit/restart.
4. A two-hour conversation/gameplay soak and a 100-turn repeat/interruption run without crashes, orphan workers, stale output, growing queues or unexplained memory/VRAM growth. Include degraded-device cases.
5. Phase 1/3 latency/resource/quality reports meeting the frozen profile targets, or a narrower feature/hardware claim that explicitly excludes unmet modes. CPU fallback alone does not prove a usable voice profile.
6. One short runbook: install/start, controls, provider/device selection, common failures, private diagnostics, backup/reset/archive and rollback. Clearly separate tested support from best effort.

Go/no-go: no unresolved critical/high correctness or data-loss issue; no unclassified failure in required checks; no misleading readiness or unsupported playback/knowledge claims. Require teammate review of the release candidate through the existing workflow. A plan self-review and a large test count are not substitutes for this gate. Tag/push/publish/install only within the owner's authorization for that release.

## Keep out of this polishing cycle

No microservice split, autonomous multi-agent runtime, new UI framework, extra always-on critic, generalized plugin registry, fine-tuning, always-on summarization, streaming speech protocol or unbounded cache. Reconsider only after a measured problem and a separately reviewed requirement. Keep language models replaceable and the lab outside core deployment.

## Plan self-review

This is a self-review under the development/design-review skills, not an independent audit or implementation approval.

| Finding | Resolution |
|---|---|
| “Production grade” could expand into hosting/multiple users | Scoped to the current local Windows single-player product |
| Polishing visuals could hide missing operational evidence | Reproducibility and live measurement precede tuning/UI polish |
| Global model memory targets conflict with GPU-first residency | Use measured hardware profiles; retain existing targets as provisional |
| Current 4 s attempt/retry and 4 s TTS budgets can exceed the 6 s voice target | Make reconciliation an explicit measured task; do not claim the target today |
| Native movement/subtitle changes can affect quest/mod-owned state | Treat ownership/coexistence as a release-blocking investigation, not an assumed verified defect |
| Full production delivery contracts could lead to premature infrastructure | Add only the state needed for reproduced restart/race guarantees; retain unknown semantics |
| Provider/schema tuning could add calls or weaken boundaries | Keep two-call limits, qualification preservation and held-out quality checks |
| Local logs may expose more than expected | Audit raw replies separately from existing verbose-only prompt controls |
| Current offline count could be mistaken for release readiness | Record 994/6 as historical offline evidence and require distinct live/platform gates |
| Plan could silently authorize paid/native/external work | This is planning only; execute each work package under its actual authorization |

Verdict: **go for phase 0 and measurement preparation; conditional go for a production candidate only after the required gates produce evidence**. No runtime or new readiness claim is made by this document.
