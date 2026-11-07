import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from npc.contracts.dialogue import ChatMessage, PromptPackage
from npc.generation.provider import ChatCompletionsProvider, ProviderConfig, ProviderFailure

PROMPT = PromptPackage(messages=(ChatMessage(role="system", content="Return JSON"), ChatMessage(role="user", content="Hello")), budget_bytes=300)


def config(**changes):
    return ProviderConfig(**{**dict(base_url="https://example.test/v1", model="test-model", api_key="PRIVATE_TEST_KEY"), **changes})


def body(**changes):
    return {**{"model": "actual-model", "choices": [{"message": {"content": '{"mode":"speech","text":"Hello"}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 20, "completion_tokens": 10}}, **changes}


def invoke(handler, **config_changes):
    provider = ChatCompletionsProvider(config(**config_changes), transport=httpx.MockTransport(handler))
    return asyncio.run(provider.generate(PROMPT, 1))


def test_transport_payload_and_usage_are_bounded_and_explicit():
    def handler(request):
        assert str(request.url) == "https://example.test/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer PRIVATE_TEST_KEY"
        payload = json.loads(request.content)
        assert payload["stream"] is False
        assert payload["max_tokens"] == 160
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(200, json=body())
    reply = invoke(handler)
    assert reply.model == "actual-model"
    assert (reply.input_tokens, reply.output_tokens) == (20, 10)


@pytest.mark.parametrize("url", ["http://remote.test/v1", "https://user:password@example.test/v1", "https://example.test/v1?key=secret", "file:///tmp/x", "https://example.test/#secret"])
def test_unsafe_provider_urls_are_rejected(url):
    with pytest.raises(ValidationError):
        config(base_url=url)


def test_loopback_and_optional_json_format_support():
    assert config(base_url="http://localhost:11434/v1").base_url.startswith("http:")
    def handler(request):
        assert "response_format" not in json.loads(request.content)
        return httpx.Response(200, json=body(usage=None))
    reply = invoke(handler, response_format="none")
    assert reply.input_tokens is None


@pytest.mark.parametrize("status,code", [(400, "provider_bad_request"), (404, "provider_not_found"), (401, "provider_auth"), (429, "provider_rate_limited"), (500, "provider_unavailable"), (503, "provider_unavailable"), (302, "provider_http_error")])
def test_provider_errors_are_typed_redacted_and_not_retried(status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="PRIVATE_RESPONSE", headers={"location": "https://other.test"})
    with pytest.raises(ProviderFailure) as error:
        invoke(handler)
    assert error.value.code == code
    assert "PRIVATE" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("payload", [{}, {"choices": []}, body(usage={"prompt_tokens": -1}), body(choices=[{"message": {"content": None}, "finish_reason": "stop"}])])
def test_malformed_provider_envelopes_fail_closed(payload):
    with pytest.raises(ProviderFailure, match="provider_protocol"):
        invoke(lambda request: httpx.Response(200, json=payload))


def test_large_http_response_is_rejected():
    with pytest.raises(ProviderFailure, match="provider_response_too_large"):
        invoke(lambda request: httpx.Response(200, content=b"x"*65537))


def test_network_errors_are_redacted():
    def handler(request):
        raise httpx.ConnectError("PRIVATE_TEST_KEY", request=request)
    with pytest.raises(ProviderFailure, match="provider_transport"):
        invoke(handler)


@pytest.mark.parametrize("effort", [None, "minimal", "low"])
def test_reasoning_effort_is_explicit_and_optional(effort):
    def handler(request):
        payload = json.loads(request.content)
        if effort is None:
            assert "reasoning_effort" not in payload
        else:
            assert payload["reasoning_effort"] == effort
        return httpx.Response(200, json=body())
    invoke(handler, reasoning_effort=effort)


def test_total_timeout_bounds_handler_even_without_httpx_inactivity_timeout():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=body())
    provider = ChatCompletionsProvider(config(), transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderFailure, match="provider_timeout"):
        asyncio.run(provider.generate(PROMPT, 0.02))


def test_total_timeout_bounds_drip_fed_response():
    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                await asyncio.sleep(0.005)
                yield b" "
    provider = ChatCompletionsProvider(config(), transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Drip())))
    with pytest.raises(ProviderFailure, match="provider_timeout"):
        asyncio.run(provider.generate(PROMPT, 0.03))


@pytest.mark.parametrize("enabled", [None, False, True])
def test_reasoning_toggle_is_explicit_and_does_not_raise_output_budget(enabled):
    def handler(request):
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 160
        if enabled is None:
            assert "reasoning" not in payload
        else:
            assert payload["reasoning"] == {"enabled": enabled}
        assert "reasoning_effort" not in payload
        return httpx.Response(200, json=body())
    invoke(handler, reasoning_enabled=enabled)


@pytest.mark.parametrize("content", [None, ""])
def test_reasoning_only_length_limited_reply_is_output_limit_not_protocol(content):
    payload = body(choices=[{"message": {"content": content, "reasoning": "PRIVATE_REASONING"}, "finish_reason": "length"}])
    with pytest.raises(ProviderFailure, match="provider_output_limit") as error:
        invoke(lambda request: httpx.Response(200, json=payload))
    assert "PRIVATE_REASONING" not in str(error.value)


def test_partial_answer_still_reaches_incomplete_generation_validation():
    payload = body(choices=[{"message": {"content": '{"mode":"speech"'}, "finish_reason": "length"}])
    reply = invoke(lambda request: httpx.Response(200, json=payload))
    assert reply.finish_reason == "length"
    assert reply.content == '{"mode":"speech"'


def test_conflicting_reasoning_controls_are_rejected():
    with pytest.raises(ValidationError, match="choose reasoning_enabled or reasoning_effort"):
        config(reasoning_enabled=False, reasoning_effort="low")


def test_one_shared_client_serves_every_call_and_closes_on_aclose():
    # Before: a new httpx.AsyncClient (and TLS handshake) was built for every generate() call.
    requests = []
    provider = ChatCompletionsProvider(config(), transport=httpx.MockTransport(
        lambda request: requests.append(request) or httpx.Response(200, json=body())))
    async def run():
        await provider.generate(PROMPT, 1)
        first = provider._client
        await provider.generate(PROMPT, 1)
        assert provider._client is first and not first.is_closed
        await provider.aclose()
        assert first.is_closed and provider._client is None
        await provider.aclose()  # idempotent
        await provider.generate(PROMPT, 1)  # usable again after close
        assert provider._client is not first
        await provider.aclose()
    asyncio.run(run())
    assert len(requests) == 3


def test_shared_client_is_rebuilt_for_a_new_event_loop():
    provider = ChatCompletionsProvider(config(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body())))
    assert asyncio.run(provider.generate(PROMPT, 1)).model == "actual-model"
    assert asyncio.run(provider.generate(PROMPT, 1)).model == "actual-model"


def test_timeout_applies_per_request_on_the_shared_client():
    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json=body())
    provider = ChatCompletionsProvider(config(), transport=httpx.MockTransport(handler))
    async def run():
        with pytest.raises(ProviderFailure, match="provider_timeout"):
            await provider.generate(PROMPT, 0.02)
        provider._transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body()))
        await provider.aclose()
        assert (await provider.generate(PROMPT, 1)).model == "actual-model"
        await provider.aclose()
    asyncio.run(run())
def test_provider_reuses_client_and_closes_pool():
    async def run():
        provider = ChatCompletionsProvider(config(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body())))
        await provider.generate(PROMPT, 1)
        client = provider._client
        await provider.generate(PROMPT, .5)
        assert provider._client is client and not client.is_closed
        await provider.aclose()
        assert client.is_closed and provider._client is None
    asyncio.run(run())


def test_optional_strict_schema_requires_all_response_fields_without_defaults():
    def handler(request):
        import jsonschema
        spec = json.loads(request.content)["response_format"]
        assert spec["type"] == "json_schema" and spec["json_schema"]["strict"] is True
        schema = spec["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"mode", "text", "cue", "evidence_ids"}
        assert all("default" not in prop for prop in schema["properties"].values())
        jsonschema.validate({"mode": "speech", "text": "Hello", "cue": None, "evidence_ids": []}, schema)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"mode": "speech", "text": "Hello", "cue": None, "evidence_ids": [], "state_delta": 100}, schema)
        return httpx.Response(200, json=body())
    invoke(handler, response_format="json_schema")
