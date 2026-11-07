import json

import httpx
import pytest
from PIL import Image

from app.vision.ollama_vlm import OllamaVLM, VLMValidationError, VLMUnavailableError


@pytest.mark.asyncio
async def test_vlm_request_bounds_and_normalization():
    sent = []
    def respond(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"response": '{"environment":{"description":"A road"}}'})
    client = OllamaVLM("http://localhost:11434", "fake", 2, transport=httpx.MockTransport(respond))
    result, duration = await client.analyze(Image.new("RGB", (2, 2)), [])
    assert result["environment"]["description"] == "A road" and duration >= 0
    assert sent[0]["options"]["num_predict"] == 1024
    assert sent[0]["stream"] is False and len(sent[0]["images"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("response,expected", [
    (httpx.Response(200, content=b"bad json"), VLMValidationError),
    (httpx.Response(200, json={"response": "[]"}), VLMValidationError),
    (httpx.Response(200, json={"response": None}), VLMValidationError),
    (httpx.Response(200, content=b"x" * (256 * 1024 + 1)), VLMValidationError),
    (httpx.Response(503), VLMUnavailableError),
])
async def test_vlm_rejects_invalid_and_unavailable_responses(response, expected):
    client = OllamaVLM("http://localhost:11434", "fake", 2, transport=httpx.MockTransport(lambda _: response))
    with pytest.raises(expected): await client.analyze(Image.new("RGB", (2, 2)), [])


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,available", [({"models": [{"name": "fake"}]}, True),
    ({"models": []}, False), ([], False), ({"models": None}, False)])
async def test_readiness_handles_missing_and_invalid_model_lists(payload, available):
    client = OllamaVLM("http://localhost:11434", "fake", 2,
                       transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload)))
    ready, detail = await client.readiness()
    assert ready is available and detail


@pytest.mark.asyncio
@pytest.mark.parametrize("vram", [0, 50])
async def test_vlm_warns_on_cpu_or_partial_offload(vram):
    def respond(request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "fake", "size": 100, "size_vram": vram}]})
        return httpx.Response(200, json={"response": '{"environment":{"description":"A room"}}'})
    client = OllamaVLM("http://localhost:11434", "fake", 2, transport=httpx.MockTransport(respond))
    with pytest.warns(RuntimeWarning, match="CV fake model is running on CPU"):
        result, _ = await client.analyze(Image.new("RGB", (2, 2)), [])
    assert result["environment"]["description"] == "A room"
