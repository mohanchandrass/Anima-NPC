"""One bounded chat-completions transport; no vendor SDK. Retries are decided by the caller, not hidden here."""

import asyncio
import ipaddress
import json
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import Field, SecretStr, StrictBool, ValidationError, model_validator

from npc.contracts.context import Contract, Identifier
from npc.contracts.dialogue import PromptPackage, ProviderReply, ResponseBody


class ProviderFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)  # never attach raw response/request/key


class ModelProvider(Protocol):
    async def generate(self, prompt: PromptPackage, timeout_s: float) -> ProviderReply: ...


class ProviderConfig(Contract):
    base_url: str = Field(min_length=1, max_length=2048, repr=False)
    model: Identifier
    api_key: SecretStr = Field(repr=False)
    response_format: Literal["json_object", "json_schema", "none"] = "json_object"
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high"] | None = None
    reasoning_enabled: StrictBool | None = None

    @model_validator(mode="after")
    def check_url(self) -> "ProviderConfig":
        if self.reasoning_enabled is not None and self.reasoning_effort is not None:
            raise ValueError("choose reasoning_enabled or reasoning_effort, not both")
        parsed = urlsplit(self.base_url)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base URL must not contain credentials/query/fragment")
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname == "localhost"
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError("HTTPS required except for loopback local providers")
        if not self.api_key.get_secret_value().strip():
            raise ValueError("API key required (use a nonsecret placeholder for local runtimes)")
        return self


class ChatCompletionsProvider:
    def __init__(self, config: ProviderConfig, *, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._client_loop: asyncio.AbstractEventLoop | None = None

    def _shared_client(self) -> httpx.AsyncClient:
        """One long-lived client so later turns reuse the TLS connection. Rebuilt if the event loop changes."""
        loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or self._client_loop is not loop:
            self._client = httpx.AsyncClient(
                transport=self._transport, follow_redirects=False, trust_env=False,
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1, keepalive_expiry=60))
            self._client_loop = loop
        return self._client

    async def aclose(self):
        client, self._client, self._client_loop = self._client, None, None
        if client is not None:
            await client.aclose()

    async def generate(self, prompt: PromptPackage, timeout_s: float) -> ProviderReply:
        if timeout_s <= 0:
            raise ProviderFailure("provider_timeout")
        payload = {"model": self.config.model, "messages": [item.model_dump() for item in prompt.messages],
                   "max_tokens": prompt.max_output_tokens, "stream": False}
        if self.config.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if self.config.response_format == "json_schema":
            schema = ResponseBody.model_json_schema()
            schema["required"] = list(schema["properties"])
            for field in schema["properties"].values():
                field.pop("default", None)
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "npc_response", "strict": True, "schema": schema}}
        if self.config.reasoning_enabled is not None:
            payload["reasoning"] = {"enabled": self.config.reasoning_enabled}
        if self.config.reasoning_effort is not None:
            payload["reasoning_effort"] = self.config.reasoning_effort
        try:
            # Absolute timeout additionally bounds trickle/drip responses.
            async with asyncio.timeout(timeout_s):
                async with self._shared_client().stream(
                    "POST", self.config.base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + self.config.api_key.get_secret_value()}, json=payload,
                    timeout=httpx.Timeout(timeout_s),
                ) as response:
                    if response.status_code == 429:
                        raise ProviderFailure("provider_rate_limited")
                    if response.status_code in {401, 403}:
                        raise ProviderFailure("provider_auth")
                    if response.status_code == 400:
                        raise ProviderFailure("provider_bad_request")
                    if response.status_code == 404:
                        raise ProviderFailure("provider_not_found")
                    if response.status_code >= 500:
                        raise ProviderFailure("provider_unavailable")
                    if response.status_code != 200:
                        raise ProviderFailure("provider_http_error")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > 65536:
                            raise ProviderFailure("provider_response_too_large")
                        chunks.append(chunk)
            body = json.loads(b"".join(chunks))
            choice = body["choices"][0]
            message = choice["message"]
            if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
                raise ProviderFailure("provider_refusal_or_tools")
            if choice.get("finish_reason") == "length" and not message.get("content"):
                raise ProviderFailure("provider_output_limit")
            usage = body.get("usage") or {}
            return ProviderReply(content=message["content"], model=body.get("model", self.config.model),
                                 finish_reason=choice["finish_reason"],
                                 input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"))
        except (TimeoutError, httpx.TimeoutException):
            raise ProviderFailure("provider_timeout") from None
        except httpx.HTTPError:
            raise ProviderFailure("provider_transport") from None
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError, ValidationError):
            raise ProviderFailure("provider_protocol") from None


class OfflineProvider:
    """Deterministic test/demo provider; this is not an LLM."""

    async def generate(self, prompt: PromptPackage, timeout_s: float) -> ProviderReply:
        return ProviderReply(content='{"mode":"speech","text":"Work keeps me occupied, as usual.","cue":null,"evidence_ids":[]}',
                             model="offline-demo", finish_reason="stop", input_tokens=0, output_tokens=0)
