"""python -m npc.adapters.skyrim --config configs/skyrim/connector.json"""

import argparse
import json
from pathlib import Path

from .bridge import Bridge
from .protocol import Settings, SpeechInput
from .diagnostics import configure, shutdown, event as log_event
from .server import create_app


def _name_allowlist(settings, root):
    """World vocabulary the validator accepts as names, from the game's role rules (core stays game-neutral)."""
    from .personas import load_rules
    rules = load_rules(root / settings.role_rules_file) if settings.role_rules_file else None
    terms = (rules or {}).get("name_allowlist", ())
    return tuple(t for t in terms if isinstance(t, str))[:512]


def build_runner(settings, root):
    """Compose the existing pipeline; never ingest or download during launch."""
    from npc.generation.config import load_provider
    from npc.runtime.preview import PreviewLimits, PreviewRunner
    from npc.rag import load_retriever
    timeout_ms = int(settings.timeout_s * 1000)
    attempt_ms, retry_delay_ms = int(settings.model_cutoff_s * 1000), 250
    # Two calls at most: the first is cut off at model_cutoff_s, the retry (after its delay) gets the same again.
    cutoff_ms = max(50, min(timeout_ms - 1000, 2 * attempt_ms + retry_delay_ms))
    return PreviewRunner(load_provider(root / settings.provider_profile),
                         PreviewLimits(response_timeout_ms=timeout_ms, model_cutoff_ms=cutoff_ms,
                                       provider_attempt_ms=attempt_ms, retry_delay_ms=retry_delay_ms, retry_transient=True,
                                       max_input_bytes=12000),
                         retriever=load_retriever(root / settings.rag_config if settings.rag_config else None),
                         name_allowlist=_name_allowlist(settings, root))


def send_text(settings, text, *, client=None):
    """Submit only to Anima's active loopback session, never Mantella."""
    import httpx
    import uuid
    def submit(connection):
        health = connection.get("/v1/health")
        health.raise_for_status()
        session = health.json().get("session_id")
        if not session:
            raise ValueError("Open normal NPC dialogue in Skyrim before sending Anima text")
        speech = SpeechInput(session_id=session, input_id=uuid.uuid4().hex, text=text)
        response = connection.post("/v1/player-input", json=speech.model_dump(mode="json"))
        response.raise_for_status()
        log_event("INPUT_SUBMITTED", session=session, request=speech.input_id, text=text,
                  status="accepted_for_processing; not_delivery")
        return response.json()
    if client is not None:
        return submit(client)
    with httpx.Client(base_url=f"http://127.0.0.1:{settings.port}", timeout=5, trust_env=False) as connection:
        return submit(connection)


def port_taken(port):
    """True when something already listens on the bridge port (a second bridge must not start)."""
    import socket
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Independent Skyrim connection layer")
    parser.add_argument("--config", default="configs/skyrim/connector.json")
    parser.add_argument("--cv-url", help="Loopback CV origin; overrides the local JSON setting")
    parser.add_argument("--rag-config", help="Trusted Skyrim world/NPC/source bindings")
    parser.add_argument("--provider-profile", help="Provider YAML; a live profile can incur API charges")
    parser.add_argument("--attach-visual-reference", action="store_true", help="Attach untrusted camera description to dialogue prompts")
    parser.add_argument("--say", help="Send this text to the running ANIMA bridge; does not start a server")
    parser.add_argument("--log-file", help="Optional rotating Anima dialogue log (1 MiB, two backups)")
    parser.add_argument("--console", action="store_true", help="Console window mode: title, colours and [PLUGIN] log tail")
    parser.add_argument("--local-speech", action="store_true", help="Add Windows TTS to default Whisper STT")
    args = parser.parse_args()
    root = Path.cwd()
    path = root / args.config
    if not path.is_file():
        parser.error("create configs/skyrim/connector.json from connector.example.json first")
    raw = json.loads(path.read_bytes())
    for name in ("cv_url", "rag_config", "provider_profile"):
        if (value := getattr(args, name)) is not None:
            raw[name] = value
    if args.attach_visual_reference:
        raw["attach_visual_reference"] = True
    if args.local_speech:
        port = raw.get("port", 8768)
        raw.update(stt_url=f"http://127.0.0.1:{port}/v1/speech/stt", tts_url=f"http://127.0.0.1:{port}/v1/speech/tts")
    local_stt = f"http://127.0.0.1:{raw.get('port', 8768)}/v1/speech/stt"
    if not raw.get("stt_url"):
        raw["stt_url"] = local_stt
    local_tts = f"http://127.0.0.1:{raw.get('port', 8768)}/v1/speech/tts"
    if not raw.get("tts_url") and (root / "data/tts/.chatterbox-ready.json").is_file():
        raw["tts_url"] = local_tts
    settings = Settings.model_validate(raw)
    configure(args.log_file)
    if args.console:
        from .console import setup as setup_console
        from .diagnostics import LOGGER
        setup_console(LOGGER)
    if args.say is not None:
        import httpx
        try:
            send_text(settings, args.say)
        except (ValueError, httpx.HTTPError):
            parser.exit(1, "ANIMA: text was not accepted; check bridge health, open NPC dialogue, and retry.\n")
        finally:
            shutdown()
        return
    if port_taken(settings.port):  # before any model loads: the watcher and the plugin console can both launch a bridge
        shutdown()
        print(f"ANIMA: a bridge is already running on port {settings.port}; this second bridge is exiting.")
        return
    log_event("STARTUP", mode=settings.pipeline_mode, provider_profile=Path(settings.provider_profile).name,
              rag=bool(settings.rag_config), stt=bool(settings.stt_url), tts=bool(settings.tts_url),
              port=settings.port, attribution="Anima bridge only; other mods are separate")
    runner = None
    if settings.pipeline_mode == "preview":
        runner = build_runner(settings, root)
    import uvicorn
    bridge = Bridge(settings, root=root, runner=runner)
    app = create_app(bridge)
    speech_engine, turbo = None, None
    if settings.stt_url == local_stt:
        from .whisper import prepare_model
        from .speech import WhisperSpeech, create_speech_app
        try:
            prepare_model(root)
        except Exception as error:
            parser.exit(1, f"Whisper setup failed ({type(error).__name__}). Check internet/disk space and restart.\n")
        speech_dir = root / settings.output_directory / "speech"
        if args.local_speech:
            from .speech import PiperSpeech
            from .voice_types import VoiceTypeResolver
            from .voice_table import VoiceTable
            piper = Path(settings.piper_directory)
            speech_engine = PiperSpeech(
                speech_dir, root, piper_directory=piper if piper.is_absolute() else root / piper,
                voice_types=VoiceTypeResolver(root / settings.game_directory / "Data"),
                voice_table=VoiceTable(settings.voice_table) if settings.voice_table else None,
                voice_overrides=settings.voice_overrides)
            app.state.tts_worker = speech_engine  # Closed with the server; kills warm Piper processes.
            log_event("TTS_ENGINE", engine=speech_engine.tts_engine, folder=str(speech_engine.piper_directory),
                      models=len(speech_engine.available_models()))
        else:
            speech_engine = WhisperSpeech(speech_dir, root)
    if settings.tts_url == local_tts and not args.local_speech:
        from npc.output.chatterbox import TurboWorker, model_path
        try:
            model_path(root)
        except (OSError, ValueError):
            parser.exit(1, "Chatterbox model missing. Rerun setup_chatterbox.bat.\n")
        turbo = TurboWorker(root)
        app.state.tts_worker = turbo
    if speech_engine is not None:
        app.state.stt_worker = speech_engine
    if speech_engine is not None or turbo is not None:
        from .speech import create_speech_app
        app.mount("/v1/speech", create_speech_app(speech_engine,
            tts_enabled=args.local_speech or turbo is not None, turbo=turbo))
    try:
        uvicorn.run(app, host="127.0.0.1", port=settings.port,
                    access_log=False, limit_concurrency=8, timeout_keep_alive=3)
    finally:
        shutdown()


if __name__ == "__main__":
    main()
