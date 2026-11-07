# Independent Skyrim connection layer

This connector is independently authored and does not load, modify or call Mantella. It consists of an SKSE plugin (`AnimaConnector.dll`) and a loopback Python bridge. The built-in pipeline connects captured frames to the local VLM service, scoped RAG, the existing context/policy/provider/validation pipeline and native text/optional audio delivery. Player STT defaults to automatically prepared local Whisper small.en (English, GPU preferred; CPU INT8 fallback). Chatterbox Turbo TTS is enabled automatically after its one-time setup. Native source/build verification and live-game verification are distinct; see the [implementation self-review](reviews/2026-11-02-skyrim-connector.md) and [integration plan](plans/I18-skyrim-vision-pipeline.md).

## What it captures

Opening the vanilla dialogue menu starts a conversation session after a short menu-settling delay. The plugin reads the engine's selected speaker, actor reference/base identity and source plugin, race, voice type, sex, combat/death state, weapon state, NPC-to-player line of sight, hostility, base factions and equipped hand items. It reads location/cell, interior status, game clock and outdoor weather. A bounded one-second full snapshot refresh keeps observations fresh; meaningful actor/dialogue changes invalidate pending output. It never scans the entire world or all quests.

The engine dialogue manager supplies the currently offered topic text, resolved response text entries, topic/info identifiers and associated quest/current stage. This is a read-only view of the current menu, not an exhaustive dialogue-condition evaluator. Limits: 16 topics and 8 response entries/topic; text fields are bounded. `dialogue_complete` means the observed manager list was not truncated, **not** that every possible line/branch has been enumerated. Runtime behavior on the selected Skyrim version still needs in-game verification.

Skyrim does not expose a definitive "everything this NPC knows" field. The payload preserves game observations and dialogue provenance. Biography, personality and knowledge references can be added by an explicit authored profile keyed by `base.plugin.lower():base.local_id.lower()`. Unconfigured roles/knowledge remain unknown. Factions, player-camera visibility and quest records do not automatically grant the NPC knowledge. Vanilla quest dialogue and its effects remain under the game's control; generated speech never executes quest, inventory or combat actions.

## Console window at game start

Plugin 0.5.3 can open a visible console window when Skyrim starts, showing every Anima log: the bridge's `[ANIMA]` lines (green, errors red) and the native plugin log tailed as `[PLUGIN]` lines (cyan). The plugin log is found through the Windows known-folder API, so OneDrive-redirected Documents work. `AnimaConsole.exe` (icon, title "Anima Skyrim Bridge") starts the Python bridge with `--console`, exits (and stops the bridge) when the game exits or the window is closed, and stays open with the exit code and a key prompt if the bridge fails.

Setup, after building with `scripts/build_skyrim_connector.py`:

```powershell
.\.venv\Scripts\python.exe scripts/install_skyrim_console.py --dry-run   # preview
.\.venv\Scripts\python.exe scripts/install_skyrim_console.py             # writes AnimaConsole.ini beside output/skyrim-mod/AnimaConsole.exe
```

It prints the two lines to add under `[Console]` in the installed `AnimaConnector.ini` (`LaunchConsole=1`, `ConsoleExe=<full path>`). The script never touches the game or mod-manager folders. If `AnimaConsole.exe` is installed from the ZIP into a mod folder, copy `AnimaConsole.ini` next to it.

At load the plugin checks `http://127.0.0.1:<Port>/v1/health` on a background thread (about 250 ms timeouts). If a bridge already answers, nothing is launched and that bridge is left alone; otherwise the exe is started once per game session. A missing exe is logged as a warning and the game continues. To turn it off, set `LaunchConsole=0` (the default) and restart the game. Live-game behaviour is not verified by the automated tests.

### Talking to NPCs without a dialogue menu (plugin 0.5.1)

With no session and no menu open, pressing `ChatKey` (F6) or holding F8 while the crosshair is on a living, non-hostile actor within about 600 units starts a menu-less session with that actor (F6 then opens the text box; F8 records). With no valid target F6 keeps the "talk to an NPC first" message. The session ends when the actor is more than about 600 units away, in combat or dead, when the cell changes, when the text box is closed or submitted empty, or after 60 s with nothing pending. Vanilla `MenuTopicManager` speaker checks are not applied to it; sessions started from the vanilla dialogue menu behave as before. Not verified live.

### Start on activation, key diagnostics (plugin 0.5.4)

Many NPCs have no dialogue menu: E only plays a static bark. With `AutoStartOnActivate=1` (default; `0` disables; a missing key means 1, so older `AnimaConnector.ini` files keep working) the plugin listens for `TESActivateEvent`. When the player activates a living, non-hostile, non-combat actor within about 600 units that has no session and no `DialogueMenu` opens within 700 ms, it starts the same menu-less direct session as F6 and opens the text box (same end conditions: distance, combat, death, cell change, 60 s idle). If a dialogue menu opens, nothing changes: the vanilla menu session takes over. Doors, containers, items, the player and non-player activators are ignored. A pause or custom menu defers the start for up to 3 s. The NPC's bark is never stopped, and `SuppressVanillaSpeech` applies only once a session is active.

Diagnostics in `AnimaConnector.log`: one `ANIMA key ChatKey|F8 down:` line per press (session empty, dialogue menu, `GameIsPaused`, CustomMenu, listening, target name/form ID/distance/dead/hostile/combat/base and the outcome, or "none" when the crosshair has no actor) and `ANIMA activate:` lines for each actor activation and its decision. The crosshair actor comes from `CrosshairPickData::target`, then `targetActor`. Decision logic lives in `activation_policy.h` with native tests. Not verified live.

### Lip movement without engine audio

When the bridge plays voice itself (`audio_file` null) and sends a `mouth` array, the plugin drives the mouth envelope for the delivery's planned duration and restores the mouth at stop. Not verified live.

The console window shows each turn: `PLAYER_INPUT`, `PIPELINE_RESULT` (route and model result), `OUTPUT_STAGED` (reply text), `TTS_ENGINE`/`AUDIO_OUTPUT`/`TURN_TIMING` (voice engine and timing), `NATIVE_ACK` (plugin receipts), each in its own colour, plus `[PLUGIN]` lines.

## Build and installation

The 0.2.0 plugin explicitly supports **SkyrimSE.exe 1.6.318.0 and 1.7.104.0** from one x64 DLL. Other runtimes remain rejected. Use the matching SKSE loader and Address Library for the selected executable:

| Runtime | Required Address Library | Header format | Verification |
|---|---|---:|---|
| 1.6.318.0 | `Data/SKSE/Plugins/versionlib-1-6-318-0.bin` | 2 | Native allowlist/metadata/library checks; separate live run still required |
| 1.7.104.0 | `Data/SKSE/Plugins/versionlib-1-7-104-0.bin` | 5 | Live load, Jenassa context and authored text delivery started/completed; see current review |

A pinned modern CommonLibSSE-NG supplies version-aware native layouts and both Address Library readers. Metadata uses an exact version list rather than generic version independence: old SKSE rejects the newer `StructsPost629` flag, and this plugin does use native structures. A matching-file/header preflight runs before SKSE initialization; CommonLib validates and resolves the complete library afterward. Merely bypassing a version guard is not compatibility. See [dual-runtime plan](plans/I19-dual-skyrim-runtime.md) and [current review](reviews/2026-11-03-dual-skyrim-runtime.md). Mantella and a Mantella ESP are not dependencies of this connector (checked 2026-11-05: its own DLL, port 8768, audio folder and HTTP client; no Mantella code or `SKSE_HTTP`). Two other mods are required for parts of the UI: the in-game **text box needs UIExtensions** and the **settings menu needs SkyUI**. Both are separate mods that stay needed when Mantella is off. **F8 speech and `--say` need neither.** At start-up the plugin logs whether `MantellaDialogue.dll`, `MantellaSubtitles.dll` and `SKSE_HTTP.dll` are loaded (diagnostics only). Whether another subtitle-hooking mod affects playback is not ruled out until a run with Mantella disabled.

Build with the project Python, CMake, and an x64 MSVC toolchain with C++23 support (Visual Studio 2022 17.14 or newer). The IDE alone does not install its C++/Windows SDK components:

```powershell
# First build only: download the pinned source dependencies if missing.
.\.venv\Scripts\python.exe scripts/build_skyrim_connector.py --fetch
# Subsequent builds:
.\.venv\Scripts\python.exe scripts/build_skyrim_connector.py
```

Build tools are separate from Python runtime dependencies. The build accepts CMake on PATH or the project-local `output/build-tools/cmake/data/bin/cmake.exe`. The latter can be prepared with `python -m pip install --target output/build-tools cmake==3.31.6`. Visual Studio 2022 is the default generator. For an already configured compiler environment, `--generator Ninja --build-dir output/skyrim-build-dual` uses a separate Release tree. Keep distinct build trees for different generators/toolchains. Pinned sources are recorded in `configs/skyrim/native-dependencies.json`; modified dependency checkouts are rejected rather than overwritten.

The build packages `output/skyrim-mod/AnimaConnector.zip`, including DLL/INI, source license notices, both runtime targets, per-runtime verification status and DLL hash. Build/test success never automatically marks either runtime live-verified. Native source and dependency licenses are included; the previous dependency pins are retained in `configs/skyrim/native-dependencies.legacy-1.6.318.json` as provenance for the original source baseline, not as a drop-in dependency set for 0.2.0. Install it as a separate mod-manager mod, or copy the ZIP's `SKSE` directory into Skyrim's `Data` directory. It has no ESP to enable. The build script does not change the game installation. Keep a separate test save. Uninstall by removing only `AnimaConnector.dll`, `AnimaConnector.ini` and this connector's generated `Data/Sound/Voice/AnimaConnector` directory.

## Start the bridge

Run `run_skyrim_connector.bat` from the repository. It creates the ignored `configs/skyrim/connector.json` from the public example on first launch, then runs `python -m npc.adapters.skyrim`. Edit `game_directory` if Skyrim is elsewhere. Start the bridge before opening a game conversation; load Skyrim through SKSE.

The server binds **127.0.0.1:8768**. If changing its port, also change `Data/SKSE/Plugins/AnimaConnector.ini`. Health: `GET /v1/health`. The SKSE log directory contains `AnimaConnector.log`. The bridge's health status reports transport readiness, not that every downstream model or game API has passed a live check.

## Pipeline connections

The public example defaults to `pipeline_mode: "preview"`, composing the working core with an offline provider. Existing local configuration is preserved. The alternative `pipeline_mode: "inbox"` writes typed JSON events to `output/skyrim/inbox/` and waits for a result callback. Do not infer ordering from UUID filenames; use `kind`, `request_id`, `revision` and capture timestamps. Context is persisted before that session's player turn is emitted. Files remain local, subject to the output budget; archive them explicitly when the budget fills.

For a running external pipeline set `pipeline_mode: "http"` and `pipeline_url` to its loopback event endpoint. The bridge sends:

| Event | When | Contents |
|---|---|---|
| `context_ready` | Conversation begins | Actor/game/menu snapshot, profile/knowledge references, screenshot metadata |
| `context_updated` | Material context changes | Updated snapshot and revision |
| `player_turn` | Text/STT input arrives | Correlation/deadline, current game/profile, canonical `ContextScenario`, screenshot reference |
| `delivery` | Game reports output status | Delivery/request IDs, started/completed/interrupted/failed; no invented heard text |
| `context_closed` | Menu closes/save changes | Closed session ID |

`context_ready` is the prewarm/data-ingestion trigger. It does not fabricate a player utterance or ask the model to speak.

The full collected event also enters the pipeline through `scenario.adapter_context`, including in built-in preview mode. This optional shared ingress field has `source: "anima-skse"` and a bounded JSON `payload` containing `game_event`, `npc_profile`, `screenshot` and `image_description`. `game_event` preserves native event IDs, capture time, revision and the complete snapshot (actors, dialogue/quest entries and world fields). Player text/STT confidence and scoped identity remain in `scenario.turn`. Screenshot data is metadata/a local path, not image bytes; descriptors remain null when unavailable or expired. Existing top-level HTTP/inbox fields remain available for compatibility.

The connector delivers observations without interpreting them: canonical fact selection, prompt selection and knowledge authorization are unchanged. Presence in `adapter_context` does not establish NPC perception or grant knowledge. The descriptor reaches ingress regardless of prompt attachment; `attach_visual_reference` still controls the existing explicit prompt path.

The pipeline may return HTTP 202 and later call `POST /v1/pipeline/result`, or return the same result JSON immediately from its `player_turn` request. Other event responses should be HTTP 202/204. A result must refer to the exact pending session/request/NPC:

```json
{
  "session_id": "copy-from-player-turn",
  "request_id": "copy-from-player-turn",
  "npc_id": "copy-from-player-turn",
  "mode": "speech",
  "text": "The final validated line spoken by the NPC.",
  "audio_path": "response.wav"
}
```

`audio_path` is relative to `audio_root` (default `output/pipeline/audio`), or an absolute path contained there. The pipeline must finish writing the file before submitting it. Only PCM16 mono/stereo WAV, 8-48 kHz, at most 30 seconds/6 MB, is accepted. Text must be the final validated transcript used for this audio; the bridge cannot verify semantic text/audio correspondence. It never asks the LLM to choose a filesystem path.

Alternatively return text without `audio_path` and configure `tts_url`. The bridge POSTs `{request_id,text,voice}` to that endpoint and expects `audio/wav` bytes. This calls the existing/future TTS service; it does not introduce another speech model. Without either audio source, output is subtitle-only. Explicit `silence` requires empty text and no audio. `end` can include a farewell; the native side closes the menu after delivery.

The command channel stages verified audio under `Data/Sound/Voice/AnimaConnector/<delivery-id>.wav`, attaches it to the NPC using Skyrim's audio engine and adds an NPC-associated subtitle. Normal delivery waits while the selected speaker has an active vanilla dialogue line; repeated polls leave the command pending. It starts only after that line ends and the session, speaker, revision and expiry checks still pass. Waiting does not extend expiry. Normal delivery does not call `StopCurrentDialogue`; explicit F8/text barge-in retains its intentional interruption behavior. This native fix requires rebuilding/reinstalling the DLL; see the [self-review](reviews/2026-11-05-vanilla-dialogue-delivery.md). Optional amplitude-based mouth movement is controlled by `AnimateMouth` in the INI; it is approximate, not `.lip` or phoneme-aligned animation. Engine playback/subtitle behavior still requires a live smoke test. Terminal delivery removes the staged file. A process crash may leave an orphan file; remove orphan files only while the game/bridge are stopped.

### "Talk with me." dialogue line (Anima.esp, plugin 0.5.1)

`Anima.esp` (light/ESL-flagged, master `Skyrim.esm` only) adds one quest with Start Game Enabled and one top-level player line, "Talk with me.", with no conditions, so it appears for every NPC that opens the dialogue menu, including NPCs with no lines of their own. It is modelled on Mantella's conversation-control line: a Goodbye info that reuses Skyrim's shared "Of course." response and runs the `AnimaStartDialogue` Papyrus fragment, which calls `AnimaNative.OpenConversation()`. The plugin then starts a menu-less session for that speaker and opens the Anima text box once the dialogue menu has closed (it gives up after 8 seconds). No SkyUI or Mantella is needed. `scripts/build_anima_esp.py` generates the file deterministically; `scripts/dump_esp.py` prints its records.

Enable it by installing `AnimaConnector.zip` as a mod in MO2 (it contains `Anima.esp` and `Scripts/AnimaNative.pex`, `Scripts/AnimaStartDialogue.pex`) and ticking `Anima.esp` in the plugins list. Remove it by unticking `Anima.esp` or disabling the mod. Without it, use the chat key (default `@`) while looking at an NPC, or the vanilla dialogue menu plus the menu key, exactly as before.

A malformed plugin can crash the game on load or when opening dialogue. If that happens, disable `Anima.esp` in MO2 first; the DLL does not need it. The line has been checked structurally against `Mantella.esp`, not in a live game.

### Vanilla speech suppression (plugin 0.5.1)

While an Anima delivery is active, the plugin stops the delivering NPC's own vanilla speech (greetings, barks, a topic the player picks in the menu). Each tick it checks `MenuTopicManager::currentTopicInfo` (set only while a menu topic is spoken) and the subtitle list for a subtitle from that NPC that is not Anima's own; if found it calls `Actor::StopCurrentDialogue()`. The player's speech and Anima's sound handle are never touched, and F8, F6 and new input still interrupt the reply through the normal barge-in. One log line (`ANIMA suppressed vanilla speech: ...`) is written per delivery. Set `SuppressVanillaSpeech=0` in `AnimaConnector.ini` to disable (default `1`). Not yet verified in a live game.

The worker polls the bridge every 25 ms while a turn or delivery is pending and every 100 ms otherwise; after bridge errors it backs off from 250 ms up to 2 s.

## Default characters, scene and openings

- NPCs without an authored `profiles_file` entry get a persona derived from `configs/skyrim/role_rules.json` (`role_rules_file`): the first rule matching the NPC name, faction names or IDs, equipment or race sets occupation, role, services and traits; anything else is a plain townsperson. Traits vary deterministically from the NPC reference ID. Authored profiles always win; a missing rules file keeps the old "unknown" persona. Snapshots carry no class field, so class is not matched.
- The rules also hold broad world knowledge (province, holds, the war, dragons returning, the local Jarl when the hold is known) that goes into `Persona.common_knowledge`, and the snapshot's location or cell name becomes `Persona.location`. No quest facts.
- Each turn carries a bounded `scene`: place, time of day, weather, the NPC's gear and, when it sees the player, the player's visible gear and whether the weapon is drawn. It is prompt data only, dropped before identity when the byte budget is short. Quest stages and the dialogue list never enter it.
- `opening_lines` (on in the config, off in the code default) delivers one authored greeting `opening_delay_s` (default 1.2 s) after a conversation opens, through the normal delivery path. It is not a player turn and is not stored in memory. Speaking, typing or F8 first cancels it, and a staged opening is replaced by the player's turn. It is skipped for dead, in-combat or hostile NPCs.
- Openings (plugin 0.5.3) fire only for sessions Anima started itself: the menu-less direct session or the "Talk with me." line. The plugin's `start` event carries `direct` (optional, default false); vanilla DialogueMenu sessions get no opening, and an opening delivery never calls `StopCurrentDialogue`, so it cannot cut off the NPC's own greeting or quest lines.
- `memory_scope` (`character` default, or `session`): `character` keys social memory by the player's name instead of the launch epoch, so an NPC remembers you across launches and different player characters stay separate; `session` keeps the old per-launch memory. Existing rows need no migration. See [social state](SOCIAL_STATE.md).

## Audio output (`audio_output`)

`system` (default): the engine sound handle in the plugin loaded our WAV but never reported it playing, so replies were silent subtitles. The bridge now writes the WAV to `<output_directory>/speech/playback/<delivery_id>.wav`, sends the plugin a subtitle-only command (`audio_file` null, `duration_s` = WAV length + 0.4 s, `mouth` kept) and plays the file through Windows (`winsound`) when the native `started` receipt arrives. It stops and deletes the file on `interrupted`/`failed`, barge-in, a new delivery, session end and shutdown; after `completed` the file is deleted once the sound finishes. Playback errors are logged (`AUDIO_OUTPUT_FAILED`) and never fail the turn. Limits: the voice is not positional (same loudness wherever the NPC stands) and lip movement depends on the plugin acting on `mouth`.

`engine`: the previous behaviour; the WAV is staged under the game's `Data/Sound/Voice/AnimaConnector` and played by the plugin.

`say` (plugin 0.5.2, proof check since 0.5.3, optional, unproven until tested in a live game): the route Mantella's `NpcSpeak` uses. To switch, set `"audio_output": "say"` in `configs/skyrim/connector.json` and install the 0.5.3 package (it carries a new `Anima.esp`, which is no longer light/ESL). The bridge writes the WAV and, when `facefx_directory` holds `FaceFXWrapper.exe` and `FonixData.cdf`, a `.lip` file to `Data/Sound/Voice/Anima.esp/<VoiceTypeEditorID>/AnimaDialo_AnimaSpeechLine_<00000805|00000807>_1.wav`, alternating the two topics like Mantella (the engine caches by path). The command carries `topic_index` (1 or 2) and the subtitle text; the plugin has the actor say that `Anima.esp` topic through `ObjectReference.Say` in the Papyrus VM, adds the subtitle itself and ends at the planned duration. Since 0.5.3 the plugin acks `started` (and audio `full`) only once the engine shows the actor speaking within 2 s; otherwise it answers `failed` with reason `audio_failed`. If the plugin cannot find `Anima.esp` or the topic, it answers `failed` with reason `say_unavailable`. Either way the bridge then replays the same line through system audio and stops using `say` until restart. An NPC with no known voice type, or a failed write, also uses system audio. Without FaceFX there is no `.lip` file. Things only a live game can show: that the engine picks the file by that path, that the lip file format is accepted, and what the engine's own `...` subtitle does beside ours.

## Player speech input

Press **F6** (`ChatKey=64` in `AnimaConnector.ini`, a DirectInput scan code) to open the text box directly while NPC dialogue is open (needs UIExtensions). It acts on key down only, is ignored while another custom menu is open or F8 is held, and outside NPC dialogue shows "ANIMA: talk to an NPC first.". **F7** still opens the options list and **F8** still records.

Hold **F8** while in a conversation, speak, then release. The native plugin sends `listen_start`/`listen_stop`; the bridge records the default Windows microphone as 16 kHz PCM16 mono, with a 15-second cap. Recording starts only from this explicit control. A null/missing `stt_url` selects the built-in Whisper endpoint at connector launch; explicitly configured external URLs are preserved. The STT endpoint receives raw `audio/wav` and returns JSON `{ "text": "...", "confidence": 0.95 }`. Source audio is deleted after reading. Closing the session shuts down recording.

Run `run_skyrim_connector.bat` normally: no STT flags, manual model downloads, file moves, CUDA setup or system FFmpeg installation are required. The launcher runs project setup if Whisper dependencies are missing. Setup prepares the pinned `Systran/faster-whisper-small.en` revision; connector startup also prepares it if absent and verifies that it loads before accepting recordings. First preparation requires internet/disk space; subsequent starts use ignored `data/stt/hub/` offline. Downloads can resume after interruption; an unloadable cached model is repaired once. A failed preparation stops startup with a retry instruction rather than claiming readiness.

Existing installations with only `base.en` cached automatically prepare the pinned `small.en` model at the next connector startup. The old cache is preserved; explicit external STT endpoints are unchanged. This is a model-capacity upgrade, not a latency optimization: accuracy and processing time must be evaluated on the target microphone and hardware.

The decoder uses English, CUDA INT8/FP16 where available and CPU INT8 fallback, two CPU threads, VAD and a single decoding beam. One resident worker loads the model before admission and reuses it across utterances. Each utterance retains a 20-second limit; cancellation/timeout kills and reaps the worker before temporary WAV cleanup and initiates one bounded background recovery. Requests during recovery are rejected rather than queued. CPU fallback prints a model-specific warning. Measure latency alongside Skyrim before claiming real-time performance. `confidence` is a mean token-probability proxy, not a calibrated accuracy estimate. Silence produces no fabricated player utterance. Only local inference is used; no speech API key is needed.

`GET /v1/speech/health` reports built-in STT readiness and the TTS engine. `--local-speech` adds local TTS to Whisper STT: Skyrim voice-type models through `piper.exe` when available, otherwise Windows SAPI's default voice. For NPC-conditioned speech on a GPU, use the Chatterbox setup below; XTTS is not installed.

### Piper voices (`--local-speech`)

Set `piper_directory` in `configs/skyrim/connector.json` to a folder holding `piper.exe` and `models/skyrim/low/<voice>.onnx` plus `<voice>.onnx.json`. The default is `assets/private/npc_lab/piper`, which holds six voices (`maleguard`, `femalenord`, `femaleeventoned`, `femalecommander`, `femalecondescending`, `femaleyoungeager`). A Mantella install has 68 voices in `.../MantellaSoftware/piper`; point `piper_directory` there only if you want them. The bridge reads from the folder, copies nothing, and never runs Mantella. Model files stay out of Git.

Voice choice, in order:

0. **Authored override, then the character table** (both optional, from `connector.json`). `voice_overrides` maps `"plugin:localid"` of the base form (for example `"Skyrim.esm:00013BAA"`) or an exact NPC name to a model name. `voice_table` is the path to a Mantella-format `skyrim_characters.csv` (read in place with `utf-8-sig`; for example `D:/MO2/mods/Mantella/SKSE/Plugins/MantellaSoftware/data/Skyrim/skyrim_characters.csv`). A row matches by reference ID, then base ID (vanilla plugins only), then exact NPC name (only when all rows with that name agree). Rows with an empty `voice_model` are ignored. `voice_model` is lowercased and stripped of spaces to give the Piper model file. A model that has no file in the Piper folder is skipped. Log: `selected_by="override"` or `"voice_table:ref_id"`, `"voice_table:base_id"`, `"voice_table:name"`.
1. **The NPC's voice type.** The plugin reports only the voice-type form (`plugin` and `local_id`); its in-game name is empty and the engine does not expose editor IDs. The bridge therefore opens `<game_directory>/Data/<plugin>`, reads the top-level `VTYP` group, takes the record's `EDID` (for example `MaleGuard`) and lowercases it. If `<that name>.onnx` exists in the folder, it is used. This is the same file naming Mantella uses.
2. **A model of the same sex.** The actor's base sex (0 male, 1 female) selects a model whose name starts with `male` or `female`, preferring `...nord`, then `...eventoned`, then the first by name.
3. **Windows speech.** Used when there is no usable model, the sex is unknown, Piper fails or Piper takes longer than 8 seconds (the process is killed). A timeout goes straight to Windows speech instead of trying another model.

The log shows the result: `TTS_READY engine="Piper" voice="maleguard" selected_by="voice_type"`, or `engine="Windows SAPI" voice="system default"`; `TTS_FALLBACK` gives the reason for each Piper failure, and `TTS_ENGINE` at start-up lists the folder and the number of models found. A voice type defined by a mod resolves only if its plugin is in `<game_directory>/Data`. Up to two models stay loaded between turns (a warm call takes about 0.1 to 0.3 s instead of reloading the model); they are killed on timeout, failure and shutdown. Only one synthesis runs at a time.

### Reply speed and `TURN_TIMING`

Every turn logs one line when it ends: when the game reports the speech started (`outcome="started"`), or when it fails or is cancelled (`outcome="failed"` or `"cancelled"`). Times are milliseconds. A stage the turn did not reach reads `"none"`.

```
TURN_TIMING session="..." request="..." outcome="started" source="stt" total_ms="3120.4" stt_ms="640.2"
  context_wait_ms="0.0" retrieval_ms="48.1" prompt_ms="1.2" model_ms="1412.7" validation_ms="0.9"
  pipeline_ms="1470.5" tts_ms="610.3" staging_ms="12.8" native_start_ms="188.0"
```

`total_ms` runs from the bridge accepting the input to the outcome. `stt_ms` is the speech-to-text call (typed text: `"none"`). `context_wait_ms` is the wait before the turn starts (previous work and, with a CV service, the screenshot). `retrieval_ms`, `prompt_ms`, `model_ms` and `validation_ms` come from the generation pipeline; `model_ms` includes the delay before a retry. `pipeline_ms` is the whole generation call. `tts_ms` is speech synthesis, `staging_ms` writing and analysing the audio, and `native_start_ms` the time from staging until the plugin reports that playback started (the plugin polls every 100 ms). The transient-failure retry (`retry_transient`) is on for the Skyrim connector only. Typical slow stages: `model_ms` above about 4000 means the first model call was cut off and retried; a large `tts_ms` means a cold Piper model or the fallback to Windows speech.

With no CV service (`cv_url` unset), a turn never waits for a screenshot; the capture taken when a conversation opens still runs in the background. The model client is one long-lived connection, closed on shutdown. `model_cutoff_s` (default 4) abandons a slow model call; one transient failure (a 5xx or timeout) is retried once after 250 ms, so the worst case is about 8 s instead of the old 29 s, then the fallback line is used. Authentication and rate-limit errors are never retried.

To prepare manually for diagnostics (normal users need no extra step):

```powershell
.\.venv\Scripts\python.exe -m npc.adapters.skyrim.whisper --prepare
```

An existing STT client can bypass recording and POST its result to `/v1/player-input`:

```json
{"session_id":"from-context-ready","input_id":"unique-utterance-id","text":"Hello.","source":"stt","confidence":0.95}
```

For a text-only test use `source: "text"`. A duplicate input ID does not generate another reply. There is one active turn/delivery; input during playback is rejected until the game reports interruption/completion. The connector does not start a second microphone or implement a new recognizer.

## Screenshots and image-descriptor handoff

On conversation start and material context updates, the bridge captures the foreground game's client area by process ID and saves PNG under `output/skyrim/screenshots/`. Payload metadata includes the file path, frame capture time, source event and `viewpoint: "player_camera"`. Set `cv_url` to `http://127.0.0.1:8000` to send the frame to the local CV service. `image_description` then carries a compact validated descriptor; it stays null when disabled/unavailable/stale. Capture or analysis failure permits ordinary dialogue. Retention defaults to 100 frames; old frames may be removed.

CV runs independently in the background with a `cv_timeout_s` limit (default 8 seconds), without delaying the player turn. Results are bound to session/revision and expire `visual_ttl_s` after capture (default 15 seconds). A subsequent turn schedules refresh of expired/missing visual context and proceeds with only an already-ready fresh descriptor. Slow cold inference can finish too late and be discarded; warm the VLM separately. The descriptor is available for inspection by default. Only explicit `attach_visual_reference: true` includes it in the built-in provider prompt as untrusted player-camera reference. It never becomes NPC perception, state, a RAG source or a valid citation. HUD/identity/quest claims must not be treated as known facts.

This first capture backend uses Windows GDI. Use borderless/windowed Skyrim; exclusive fullscreen, overlays or protected rendering can produce missing/black frames. That behavior needs an actual game check. Captured HUD/quest text is player-visible data and must not be promoted into NPC knowledge by a future descriptor.

## Existing pipeline smoke binding

Set `pipeline_mode: "preview"` to compose the existing `PreviewRunner` and configured provider profile. The default profile is scripted/offline; changing it to a live profile can make paid calls when the player submits input. Set `rag_config` to a trusted RAG configuration with `game_id: "skyrim"`, your `world_id` and actual actor reference IDs such as `ref:00013478`. Prepare the index with the existing [RAG commands](RAG_INTEGRATION.md) before play. Unbound identities fail closed; authored profile knowledge labels alone do not grant retrieval access. No ingestion/model download occurs during a turn. The built-in path uses a 12,000-byte prompt cap to accommodate game observations, evidence and optional visual reference.

Run `run_cv.bat` in one terminal and `run_skyrim_connector.bat` in another. See [CV setup](../cv/SETUP.md) for the Ollama prerequisite; `GET http://127.0.0.1:8000/ready` checks the local model without downloading it. Leave `stt_url` null for automatic Whisper STT. Leave `tts_url` null for automatically prepared Chatterbox, or subtitle-only output before Chatterbox setup. Explicit external URLs are preserved. Submit text to `/v1/player-input` using the active session ID from `/v1/health`; validated output is returned through the plugin's command polling. The [integration tests](../tests/unit/adapters/test_skyrim_connector.py) exercise this path with deterministic inference and game boundaries.

This composes the current canonical game-context subset. Production social memory and reliable heard-memory commits remain unimplemented for this best-effort native receiver. Console acknowledgements do not establish game delivery.

## Guarantees and limits

The handoff inbox stops admission at 4,096 retained event files; archive processed records explicitly. Its total storage budget is independent of the screenshot count limit.

Game object access is on the engine task thread; HTTP runs on a worker. Queues, requests, payloads, turns, microphone time, screenshots and output size are bounded. Commands carry session/NPC identity, state revision, delivery ID and start expiry. Native dedup prevents repeated polling from replaying speech. Combat/menu close/load changes stop output. Late callbacks are rejected; an expired command requests stop rather than assuming it was heard. Acknowledgements are best effort and do not establish word-level heard portions.

Offline tests use fake game/capture/model/device boundaries. They verify mapping, actor identity, visibility, context-before-turn, stale state, callbacks, duplicate/expired commands, file containment and cleanup. No real microphone, Skyrim launch or paid model call runs in these tests. Live readiness requires opening one conversation, checking exported topics/responses and frame, submitting one text/STT utterance, hearing/watching one response, and testing menu close/combat/load interruption.


## Identifying Anima output alongside other mods

Start `run_skyrim_connector.bat --log-file output/skyrim/anima-console.log` in a visible terminal. Every diagnostic has a timestamp and `[ANIMA]` label. Startup lists provider profile and RAG/STT/TTS availability. The default offline profile is scripted, not a live language model. A Mantella.exe window, its speech recognition and its synthesized responses belong to Mantella; Anima neither consumes nor relabels them.

With normal NPC dialogue open, use another terminal to submit directly to Anima:

```powershell
.\.venv\Scripts\python.exe -m npc.adapters.skyrim --say "HELLO"
```

`PLAYER_INPUT` records accepted text, `PIPELINE_RESULT` distinguishes authored/model/fallback routes and model call count, and `OUTPUT_STAGED` records the accepted text awaiting the game. Only `NATIVE_ACK` represents the native receiver's started/completed/interrupted/failed status. Completion is not proof of hearing.

### Delivery receipts (plugin 0.5.1)

A `delivery` receipt may carry two optional fields; receipts without them (older plugins) are still accepted and logged as `unreported`. `NATIVE_ACK` logs both.

- `audio`: `full` (audio observed and ran to its planned end), `ended_early` (the engine reported the sound not playing for 300 ms or more before the planned end; brief false readings are ignored), `failed` (audio never started within 2 s, or could not be started), `none` (text-only delivery). Absent on interruptions, where the audio outcome is not meaningful.
- `reason`: why the delivery stopped: `end` (ran to its planned end), `barge_in` (a new player turn started), `menu_closed`, `combat`, `actor_lost`, `state_changed` (a material change: speaker, combat, dead, hostile, weapon drawn, location or cell), `superseded` (a newer delivery replaced it), `bridge_stop` (the bridge asked it to stop), `audio_failed` (audio file rejected or overlong).

When audio ends early or cannot start, the subtitle stays up for the planned duration and the receipt is `completed` with `audio` set to `ended_early` or `failed`. The player saw the reply, so memory is committed; the bridge cannot tell whether it was heard. `interrupted` and `failed` receipts never commit memory. Non-material snapshot changes (`sees_player`, dialogue list, weather, equipment) are still sent but neither stop playback nor cancel a turn; only a changed `state_revision` does. In the plugin log, `ANIMA stop: reason=... soundID=... elapsed_ms=... planned_ms=...` and `ANIMA IsPlaying -> true/false` lines show exactly what the engine reported. Whether the engine truly plays the 16 kHz SAPI audio is unverified until a live run. Session/request/delivery IDs correlate the stages. Rejected/duplicate inputs are not relabelled as new successful turns. Uppercase greetings currently use the same case-insensitive authored greeting policy; no shout-sensitive behavior is claimed.

The optional local log includes dialogue text; keep it private unless selected for publication. Text fields are escaped to one line and capped at 1024 characters. File output rotates at 1 MiB with two backups. Full prompts, provider keys and raw responses are not logged. No per-frame snapshots or command polls are printed. Close/reopen NPC dialogue after restarting the bridge to establish a fresh session.

## Chatterbox Turbo NPC voices

Run **`setup_chatterbox.bat` once**, then start `run_skyrim_connector.bat` normally. The script uses the same project `.venv` and `requirements.txt`, installs pinned dependencies, downloads the pinned English Turbo checkpoint/tokenizer/voice encoder/generator, generates a setup WAV, finds Skyrim and extracts short voice references. No manual model downloads, file moving, external FFmpeg installation or separate speech server is required. A fresh machine still needs the repository's Python 3.11 prerequisites. Downloads total approximately 3 GB for model files plus Python packages; allow several GB of free space and internet for first setup.

Game discovery checks the existing connector configuration, Bethesda's Windows registry entry and Steam libraries. If automatic discovery fails, supply the game folder once:

```powershell
.\setup_chatterbox.bat --game-directory "D:\Games\Skyrim Special Edition"
```

Setup queries GPU 0 with nvidia-smi and selects CUDA 12.4 wheels for conservatively supported NVIDIA hardware (driver major 552+, compute capability 6.x–9.x); otherwise it installs CPU wheels. Blackwell/newer architectures are not admitted by this pinned Torch release. No separate CUDA toolkit setup is required. The worker uses CUDA when the installed build reports it available; otherwise it uses CPU. No GPU speed, VRAM fit or real-time CPU performance is guaranteed. The current pinned Torch release does not establish compatibility with every GPU generation. Test alongside the game before claiming readiness.

The importer reads vanilla/DLC VTYP editor IDs and the matching voice folders in installed English voice BSAs. Bundled PyAV decodes FUZ/XWM audio; generated 5–10-second PCM16 references and source hashes stay under ignored `data/tts/voices/`. It attempts a bounded number of clips per voice type and preserves existing entries/overrides. These automatically selected clips need listening review for acting, music/noise and pronunciation; no claim of curated performance quality is made. It does not import saved player data, alter game files or commit extracted assets. Mod-added voice definitions are not automatically imported in this first slice.

`manifest.json` maps `voices` by `plugin:local-form-id`. Optional `npcs` entries use the NPC base `plugin:local-form-id` and the same `{file,sha256}` reference shape. NPC overrides take precedence over voice types. References must remain inside the voice directory and contain 5–15 seconds of PCM16. Missing/mismatched references fail synthesis; the bridge reports a TTS failure and retains subtitles rather than choosing an unrelated voice. The setup marker is written only after model preparation, usable references and offline regression succeed. A partial download or missing game does not enable TTS.

One resident worker owns the loaded model and serves one request at a time, with four cached reference conditionings. Every request explicitly selects/restores its voice conditioning, including cache hits. Startup loads the model and warms lazy audio kernels before reporting ready, with a 120-second readiness limit; synthesis has a 20-second limit within the bridge's remaining deadline. A disconnected/cancelled request or timeout kills the worker and starts one bounded background warm-up. New requests receive subtitle fallback until recovery is ready; there is no request queue or automatic retry loop. Shutdown cancels recovery and reaps its process. Generated audio stays buffered and is validated as PCM16 before native delivery. No partial audio streaming or automatic laugh/emotion tags are added. Turbo does not implement the original model's CFG/exaggeration knobs.

Runtime uses only cached model files. `GET /v1/speech/health` reports worker readiness. `output/chatterbox/setup-check.wav` is a diagnostic sample using the model's built-in voice; it is not an NPC voice audition. Run an in-game human and Khajiit test and check matching text/voice, interruption and repeated voice switching. Linux/offline fixture tests do not prove native Windows playback or game-archive compatibility.

Dependencies are pinned to a compatible set because upstream Chatterbox also requires older NumPy, tokenizer, Hugging Face Hub, FastAPI/Starlette, Pillow and setuptools versions (the watermark dependency still uses pkg_resources). The unused upstream Gradio UI is not started. Upstream's project status is unmaintained; code/package and model revisions are pinned rather than following moving branches. See [Chatterbox source](https://github.com/resemble-ai/chatterbox) and [model files](https://huggingface.co/ResembleAI/chatterbox-turbo).

Measured CPU smoke check on the development host: a resident worker became ready in about 50 seconds; a public reference-conditioned 2-second line took 19.67 seconds to generate with a diagnostic 90-second limit. A prior production-limit attempt timed out at 20 seconds. These are functional checks, not an in-game quality/latency signoff. CPU Turbo is not a recommended low-latency conversational target; verify GPU synthesis and Skyrim resource contention on the target machine.

## Start automatically with the game

`.\.venv\Scripts\python.exe -m npc.adapters.skyrim.autostart --install` adds a launcher to the current user's Windows Startup folder and runs a background watcher. The watcher starts the bridge when `SkyrimSE.exe` appears and stops that bridge when the game exits. It leaves a bridge you started by hand alone, and stops retrying after three failed starts in one game session. `--uninstall` removes the launcher.

Optional `configs/skyrim/autostart.json` overrides `provider_profile`, `local_speech`, `log_file`, `game_process`, `health_url`, `poll_seconds` and `start_delay_seconds` (default 15: the watcher waits this long after the game appears and checks health again just before spawning, so it does not race the plugin's LaunchConsole). A second bridge that finds the port taken exits cleanly, before loading any speech model. The default is the Gemini profile with local Windows speech. The watcher changes no game files and makes no model calls itself, but a live profile makes API requests when the player speaks to an NPC.

## GPU-first testing setup

Re-run `setup.bat` after updating. Setup prepares CUDA-capable Torch and GPU ONNX binaries; Whisper, MiniLM embeddings and Chatterbox prefer supported GPU execution and fall back to CPU when unavailable or execution fails. Ollama manages VLM offload automatically. Optional YOLO also prefers CUDA. Each CPU-running model emits a warning naming it; partial Ollama CPU offload also warns. Unknown Ollama placement is explicitly reported. API-hosted LLM hardware is controlled by its provider.

Whisper remains loaded across inputs; LLM HTTP connections are reused across turns and closed at shutdown. Game context publication no longer waits for optional screenshots/CV. Visual data is accepted only for the active session/revision before capture TTL expires; late results never update an in-flight prompt. See the [implementation self-review](reviews/2026-11-06-gpu-latency.md). GPU fit and latency alongside Skyrim require measurement on the target machine.

## Latency diagnostics and short synthesis budget

`tts_timeout_s` in `configs/skyrim/connector.json` defaults to **4 seconds** (0.1–20 allowed). It is also limited by the remaining turn deadline and the existing delivery reserve. A slow/failed synthesis is cancelled and the validated subtitle is staged without audio. Increase this setting only when a measured voice workload needs it; longer waits do not improve throughput.

Run `run_skyrim_connector.bat --log-file output/skyrim/latency.log` to retain the existing bounded logs. `STT_LATENCY` includes failures/cancellation; `PIPELINE_RESULT` includes route, result status, token/call count, repair flag and core `stage_ms`; `TURN_LATENCY` reports timings at staging and native started receipt. Origins distinguish speech release from text input. Context/policy, retrieval, prompt, initial generation, repair, validation, TTS and delivery wait are durations in milliseconds. `cv_wait` stays zero because CV is independent; trusted-context publication wait is separate.

`validated_response_ready`, `response_staged` and `playback_start_receipt`/`subtitle_start_receipt` are cumulative milliseconds from the input origin. Native receipt timing includes command polling/transport and is not word-level hearing or acoustic latency. Missing start receipts are never counted as successful playback. Cold model preparation remains separate. Inspect complete `stage_ms` in the preview/lab trace for core-only checks. See the [latency self-review](reviews/2026-11-06-latency-completion.md) for test evidence and measurement limits.
