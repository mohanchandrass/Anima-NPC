from datetime import datetime, timezone

import pytest

from npc.vision.client import CVFailure, VisualContext, normalize_scene_response, validate_cv_url


def payload():
    return {"request_id": "request-1", "scene_descriptor": {"scene_id": "scene-1", "captured_at": "2026-11-01T00:00:00Z",
        "environment": {"description": "A dim tavern"}, "characters": [{"description": "A person at a counter"}],
        "objects": [{"description": "A wooden table"}], "uncertainties": ["Identity is unknown"],
        "provenance": {"detector_model": "yolo26n", "vlm_model": "qwen2.5vl-3b"}}}


def test_normalizes_only_compact_visual_reference_fields():
    visual = normalize_scene_response(payload())
    assert visual.id == "cv-scene-1"
    assert visual.prompt_payload()["environment"] == "A dim tavern"
    assert visual.prompt_payload()["objects"] == ("A wooden table",)


@pytest.mark.parametrize("url", ["https://127.0.0.1:8000", "http://example.com", "http://user@127.0.0.1:8000", "http://127.0.0.1:8000/path",
    "http://localhost:0", "http://localhost:65536", "http://localhost:abc"])
def test_rejects_non_loopback_or_non_origin_cv_urls(url):
    with pytest.raises(ValueError):
        validate_cv_url(url)


def test_rejects_malformed_upstream_payload():
    with pytest.raises(CVFailure, match="cv_invalid_response"):
        normalize_scene_response({"scene_descriptor": {"environment": {}}})


@pytest.mark.parametrize("uncertainties", [None, "bad shape", {"bad": "shape"}])
def test_bad_uncertainty_shape_is_not_iterated(uncertainties):
    data = payload(); data["scene_descriptor"]["uncertainties"] = uncertainties
    assert normalize_scene_response(data).uncertainties == ()


@pytest.mark.parametrize("status,content,code", [(503, b"", "cv_unavailable"),
    (200, b"x" * (128 * 1024 + 1), "cv_invalid_response"),
    (200, b"not json", "cv_invalid_response"), (422, b"", "cv_invalid_response"),
    (422, b'{"detail":{"code":"INVALID_IMAGE","message":"x"}}', "cv_invalid_response"),
    (422, b'{"detail":{"code":"BLANK_FRAME","message":"x"}}', "cv_blank_frame")])
def test_http_errors_and_bounded_response(status, content, code):
    import asyncio
    import httpx
    from npc.vision import CVSceneClient
    client = CVSceneClient("http://localhost:8000", transport=httpx.MockTransport(lambda _: httpx.Response(status, content=content)))
    with pytest.raises(CVFailure, match=code): asyncio.run(client.analyze(b"image", "image/png"))
