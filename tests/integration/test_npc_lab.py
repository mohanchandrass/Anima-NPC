import threading

import httpx
import pytest

from npc.generation.provider import OfflineProvider
from tools.npc_lab.server import ASSETS, LabServer
from tools.npc_lab.service import LabService
from npc.vision import VisualContext
from datetime import datetime, timezone


@pytest.fixture(scope="module")
def lab():
    server = LabServer(LabService(OfflineProvider()), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with httpx.Client(base_url=server.origin, trust_env=False, timeout=5) as client:
        yield server, client
    server.shutdown()
    thread.join(timeout=2)
    server.server_close()


def headers(server):
    return {"Origin": server.origin, "X-Lab-Token": server.token}


def test_role_scenes_are_distinct_and_fonts_are_optional_fixed_routes(lab):
    _, client = lab
    scenes = [client.get(f"/scenes/{id}.svg") for id in ("mira", "elin", "daren", "iona")]
    assert all(response.status_code == 200 and response.headers["Content-Type"].startswith("image/svg+xml") for response in scenes)
    assert len({response.content for response in scenes}) == 4
    assert client.get("/scenes/unknown.svg").status_code == 404
    assert client.get("/api/fonts/dialogue.ttf").status_code == 404
    assert client.get("/api/fonts/interface.ttf").status_code == 404
    assert client.get("/api/fonts/arbitrary.ttf").status_code == 404


def test_http_bridge_wait_covers_configured_generation_budget(monkeypatch):
    import tools.npc_lab.server as module
    service = LabService(OfflineProvider(), development_timeout_s=30)
    server = LabServer(service, port=0)
    waits = []
    class Future:
        def result(self, timeout):
            waits.append(timeout)
            return "completed"
    def submit(coroutine, loop):
        coroutine.close()
        return Future()
    monkeypatch.setattr(module.asyncio, "run_coroutine_threadsafe", submit)
    async def operation(): return "unused"
    try:
        assert server.call(operation()) == "completed"
        assert waits == [32]
    finally:
        monkeypatch.undo()
        server.server_close()


def payload(server):
    p = server.service.profiles["mira"]
    return dict(request_id="http-turn", persona_id=p.id, text="How is your work going?",
                relationship=p.relationship.model_dump(), mood=p.mood, state="available", weapon_drawn=False)


def test_static_allowlist_and_bootstrap_do_not_expose_files(lab):
    server, client = lab
    for path in ASSETS:
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert "connect-src 'self' blob:" in response.headers["Content-Security-Policy"]
    html = client.get("/").text
    assert '__LAB_TOKEN__' not in html
    assert server.token in html
    for path in ["/.env", "/.env.example", "/fixtures/personas/npc_lab/mira.json", "/portraits/../../.env", "/api/info?path=.env"]:
        assert client.get(path).status_code == 404


def test_info_and_real_pipeline_turn_with_fake(lab):
    server, client = lab
    info = client.get("/api/info").json()
    assert len(info["profiles"]) == 4
    assert info["mode"] == "offline fake"
    response = client.post("/api/turn", json=payload(server), headers=headers(server))
    assert response.status_code == 200
    data = response.json()
    assert data["result"]["status"] == "model_success"
    assert data["result"]["model"] == "offline-demo"
    assert data["context"]["persona"]["name"] == "Mira"


@pytest.mark.parametrize("extra", [{"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"X-Lab-Token": "wrong"}])
def test_host_origin_and_token_are_required(lab, extra):
    server, client = lab
    supplied = headers(server) | extra
    response = client.post("/api/turn", json=payload(server), headers=supplied)
    assert response.status_code == 403


def test_get_host_is_checked(lab):
    _, client = lab
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403


def test_invalid_input_and_unknown_profiles_are_redacted(lab):
    server, client = lab
    body = payload(server) | {"persona_id": "../../PRIVATE_SECRET", "api_key": "PRIVATE_KEY"}
    response = client.post("/api/turn", json=body, headers=headers(server))
    assert response.status_code == 400
    assert "PRIVATE" not in response.text
    body.pop("api_key")
    assert client.post("/api/turn", json=body, headers=headers(server)).status_code == 400


def test_body_limit_content_type_and_invalid_json(lab):
    server, client = lab
    assert client.post("/api/turn", content="x" * 16385, headers=headers(server) | {"Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/turn", content="hello", headers=headers(server)).status_code == 415
    assert client.post("/api/turn", content="{not-json", headers=headers(server) | {"Content-Type": "application/json"}).status_code == 400


def test_cancel_without_active_request(lab):
    server, client = lab
    response = client.post("/api/cancel", json={"request_id": "nothing-active"}, headers=headers(server))
    assert response.json() == {"cancelled": False}


def test_unknown_post_route(lab):
    server, client = lab
    assert client.post("/api/provider-config", json={}, headers=headers(server)).status_code == 404


def test_visual_proxy_requires_image_and_returns_only_normalized_context(lab):
    server, client = lab
    class FakeCV:
        async def analyze(self, image, content_type):
            assert image == b"image" and content_type == "image/png"
            return VisualContext(id="cv-http", captured_at=datetime.now(timezone.utc), environment="A visible market", uncertainties=("uncertain",))
    original = server.service.cv_client
    server.service.cv_client = FakeCV()
    try:
        assert client.post("/api/vision/analyze", content=b"image", headers=headers(server) | {"Content-Type": "text/plain"}).status_code == 415
        response = client.post("/api/vision/analyze", content=b"image", headers=headers(server) | {"Content-Type": "image/png"})
        assert response.status_code == 200
        assert response.json()["visual"]["environment"] == "A visible market"
        assert "scene_descriptor" not in response.text
    finally:
        server.service.cv_client = original


def test_voice_accepts_only_validated_recorded_turn_ids(lab):
    server, client = lab
    original = server.service.media
    class FakeMedia:
        async def synthesize(self, request_id, persona_id, text):
            assert text == "Work keeps me occupied, as usual."
            return b"fake audio bytes for HTTP framing test"
    server.service.media = FakeMedia()
    try:
        assert client.post("/api/speech", json={"request_id":"unrecorded"}, headers=headers(server)).status_code == 400
        assert client.post("/api/speech", json={"request_id":"http-turn", "text":"arbitrary injected speech"}, headers=headers(server)).status_code == 400
        client.post("/api/turn", json=payload(server), headers=headers(server)).raise_for_status()
        response = client.post("/api/speech", json={"request_id":"http-turn"}, headers=headers(server))
        assert response.status_code == 200
        assert response.headers["Content-Type"] == "audio/wav"
        assert response.content == b"fake audio bytes for HTTP framing test"
        assert "X-Synthesis-Ms" in response.headers
    finally:
        server.service.media = original


def test_missing_model_route_and_invalid_model_id_are_not_file_access(lab):
    _, client = lab
    for path in ["/api/models/mira.glb", "/api/models/../../.env.glb", "/api/models/unknown.glb"]:
        assert client.get(path).status_code == 404


def test_repeated_early_body_rejections_return_http_errors_not_socket_resets(lab):
    server, client = lab
    for _ in range(12):
        response = client.post("/api/turn", json=payload(server), headers=headers(server) | {"X-Lab-Token":"wrong"})
        assert response.status_code == 403


def test_source_only_offline_lab_starts_without_private_media_or_model_services(tmp_path):
    from tools.npc_lab.media import LabMedia, MediaSettings
    from npc.state.social_store import SocialStore
    # Empty private media root: no LFS payload, game, provider key or model cache.
    media = LabMedia(tmp_path, settings=MediaSettings(piper_directory="assets/private/npc_lab/piper", voices={}, models={}))
    server = LabServer(LabService(OfflineProvider(), media=media,
        state_store=SocialStore(tmp_path / "state.sqlite3")), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=server.origin, trust_env=False, timeout=5) as client:
            page = client.get("/")
            assert page.status_code == 200 and "text/html" in page.headers["Content-Type"]
            info = client.get("/api/info").json()
            assert info["mode"] == "offline fake" and info["persistent_state"] is True
            assert info["cv_enabled"] is False and info["skyrim_fonts_available"] is False
            assert len(info["profiles"]) == 4
            assert all(not p["media"]["voice_available"] and p["media"]["model_url"] is None for p in info["profiles"])
            assert client.get("/scenes/mira.svg").status_code == 200
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
