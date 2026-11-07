"""Loopback-only HTTP surface; model-facing work runs outside the game process."""

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .bridge import Bridge, ConnectionError
from .assets import MAX_WAV_BYTES
from .protocol import GameEvent, PipelineResult, SpeechInput


def create_app(bridge: Bridge):
    @asynccontextmanager
    async def lifespan(app):
        worker = getattr(app.state, "tts_worker", None)
        speech = getattr(app.state, "stt_worker", None)
        try:
            if speech:
                await speech.start()
            if worker and worker is not speech:
                await worker.start()
            yield
        finally:
            try:
                await bridge.close()
            finally:
                if worker and worker is not speech:
                    await worker.close()
                if speech:
                    await speech.close()

    app = FastAPI(title="Anima Skyrim connector", lifespan=lifespan)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        # No CORS and no public bind. Check the actual peer, never forwarded headers.
        if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            return JSONResponse({"error": "loopback_only"}, status_code=403)
        if request.method == "POST":
            microphone = request.url.path == "/v1/speech/stt"
            expected_type = "audio/wav" if microphone else "application/json"
            limit = MAX_WAV_BYTES if microphone else 256 * 1024
            if request.headers.get("content-type", "").split(";")[0] != expected_type:
                return JSONResponse({"error": "application_json_required"}, status_code=415)
            data = bytearray()
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > limit:
                    return JSONResponse({"error": "payload_too_large"}, status_code=413)
            request._body = bytes(data)
        return await call_next(request)

    @app.exception_handler(ConnectionError)
    async def conflict(request, error):
        return JSONResponse({"error": str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"error": "invalid_connector_operation"}, status_code=400)

    @app.get("/v1/health")
    async def health():
        session = bridge.session
        return {"status": "ready", "pipeline_mode": bridge.settings.pipeline_mode,
                "session_id": session.event.session_id if session else None,
                "last_error": bridge.error,
                "image_descriptor": (session.visual_status if session else "configured") if bridge.cv_client else "not_connected",
                "visual_attached": bridge.settings.attach_visual_reference,
                "rag_configured": bridge.settings.rag_config is not None}

    @app.post("/v1/game/event")
    async def game_event(event: GameEvent):
        return await bridge.game_event(event)

    @app.get("/v1/game/command")
    async def command(session_id: str):
        return bridge.command(session_id)

    @app.post("/v1/player-input", status_code=202)
    async def speech(speech: SpeechInput):
        return await bridge.player_input(speech)

    @app.post("/v1/pipeline/result")
    async def result(result: PipelineResult):
        return await bridge.result(result)

    return app
