# GPU-first models and pipeline latency self-review

Scope explicitly requested by the owner: GPU-first local models with visible testing-phase CPU fallback warnings; resident Whisper; independent, freshness-checked CV; reusable provider HTTP client. Plan and implementation reviews are self-reviews using the development and design-review skills. No delegated review or independent signoff is claimed.

## Reviewed plan

Keep changes within existing model/adapter owners. Prefer CUDA where supported, fall back on allocation/runtime failure, report model-specific CPU operation, and preserve external endpoints. Prepare GPU-capable ONNX binaries during setup; load bundled CUDA dependencies before inference. Ollama owns its device scheduling; inspect actual placement instead of asserting GPU use from availability. Do not change the remote LLM's device or introduce local language-model inference.

Keep a single resident Whisper worker using bounded JSON lines, one active utterance/no queue, startup readiness and explicit shutdown. Timeout/cancellation must kill/reap it before deleting audio and launch at most one bounded background recovery. Reuse the existing subprocess lifecycle implementation already used by TurboWorker; Whisper overrides its startup/protocol and does not use its synthesis operation. Start it from the parent connector lifespan because mounted FastAPI lifespans do not run automatically.

Publish trusted context independently of capture/CV. Keep one visual task per session; accept results only for the active session and original revision, with age strictly below visual_ttl_s from capture start. A turn uses only an already-ready eligible descriptor. No raw screenshot or VLM identity becomes a trusted fact, and late results cannot rewrite a prompt. Maintain context-before-player-turn publication ordering.

Retain a bounded provider AsyncClient across requests/repairs, use per-request and absolute timeouts, and close it at connector, lab and CLI shutdown. Keep response-size limits and no automatic retries.

Acceptance tests: GPU selection/CPU warning and fallback; Whisper repeated requests reuse one process, cancellation/timeout cleanup and readiness; slow CV cannot stall text, stale session/revision/TTL cases; two model requests share a client which closes; setup GPU binary install ordering. Run affected unit/integration tests, full offline regression, dependency checks, compilation and whitespace checks. No paid call, microphone recording, game installation or native build change is required.

Plan review resolutions: retain bounded workers and existing inference limits; avoid claiming every model fits alongside Skyrim; CPU fallback is a visible degradation. GPU memory pressure does not relax freshness, delivery or knowledge boundaries. The owner's GPU-first instruction supersedes the original CPU-only embedding/STT resource preference.

## Implementation review findings resolved

- Preparation alone does not keep Whisper loaded. Startup now creates the long-lived subprocess; repeated utterances reuse its model. Cancellation/timeout reaps it before temporary WAV removal. Disconnected HTTP STT clients cancel inference too.
- Parent app owns STT startup/shutdown; mounted subapp lifespan remains useful when speech is run standalone. Explicit external STT remains unchanged.
- GPU execution can fail after successful loading: Whisper decode and TTS warm-up/synthesis have one CPU fallback. Subsequent work stays on CPU rather than repeatedly attempting a known-failing GPU. TTS restores reference conditioning through a new engine after fallback.
- Embedding initialization also executes inference: tokenizer initialization uses the same GPU-fallback path. Source cache location is preserved, and actual ONNX session providers are checked for a CPU warning.
- Chroma declares the CPU onnxruntime distribution. Its wheel and onnxruntime-gpu share import files. Both dependency metadata entries remain pinned for dependency compatibility; setup explicitly reinstalls the GPU wheel last without dependencies so pip order cannot silently select CPU binaries. Re-run setup after changing either distribution. CUDA library loading uses the installed runtime's preload support.
- Optional YOLO auto-selection previously omitted CUDA. It now prefers CUDA, retains MPS support, warns on CPU and falls back once on GPU inference failure. Disabled detection loads no model.
- Ollama uses its automatic GPU offloading policy. After inference, /api/ps checks size_vram against size; CPU or partial offload warns with the model name. Unavailable placement metadata is reported as unknown rather than GPU. No system-wide Ollama configuration is rewritten.
- Closing a session cancels its background CV task, preventing GPU work from accumulating across conversations.
- Capture completion can itself be stale: reject a frame after a session/revision switch before storing metadata. Accept a descriptor only strictly before TTL expiry; the accessor also rejects detached sessions.
- LLM requests reuse the provider-owned client, preserving a distinct timeout for each call. CLI, connector and inspector wrapper/lab shutdown close pools on their owning event loop.

## Verification and limits

Final check results are appended below. Earlier focused runs: 93 provider/Whisper/connector tests and 110 setup/TTS/RAG/CV tests passed; after cancellation fixes, 87 speech/connector/TTS tests passed. A newly added duplicate test basename caused collection failure and was renamed; assertions were retained. An initial pre-update suite used obsolete one-shot worker fixtures and was interrupted; tests now exercise the persistent protocol rather than launching a real model.

The pinned GPU ONNX wheel was installed successfully in the project venv for regression. This host does not establish Windows CUDA, available GPU memory or in-game performance. No new real model/microphone/game/provider experiment has run. GPU runtime, fallback under real VRAM pressure, word accuracy and warm end-to-end latency must be measured on the target machine; no speedup number or production readiness claim is made.

References: [ONNX CUDA provider and library loading](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html), [CTranslate2 hardware support](https://opennmt.net/CTranslate2/hardware_support.html), [Ollama running-model placement](https://docs.ollama.com/api/ps), [HTTPX client pooling](https://www.python-httpx.org/advanced/clients/).

Final validation: `.venv/bin/python -m pytest -q` — **620 passed in 19.83 seconds**. Five warnings include the existing Starlette deprecation and intentionally exercised CPU/unknown-placement reporting. `.venv/bin/python -m pip check` reported no broken requirements; compilation, `git diff --check` and changed documentation's local-link checks passed. Installed ONNX 1.23.2 exposes CUDAExecutionProvider alongside CPUExecutionProvider; provider availability is not a real GPU inference or contention test. Both general setup and Chatterbox setup call the same GPU ONNX binary-install helper.
