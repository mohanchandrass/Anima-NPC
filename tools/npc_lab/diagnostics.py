"""Opt-in, task-local recording of the exact model inputs in the local app.

No authorization headers, keys or provider response bodies are recorded.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_calls = ContextVar("anima_prompt_calls", default=None)


@contextmanager
def capture_inputs():
    calls = []
    token = _calls.set(calls)
    try:
        yield calls
    finally:
        _calls.reset(token)


class InspectableProvider:
    def __init__(self, provider):
        self.provider = provider

    async def aclose(self):
        if hasattr(self.provider, "aclose"):
            await self.provider.aclose()

    async def generate(self, prompt, timeout_s):
        calls = _calls.get()
        if calls is not None and len(calls) < 2:
            config = getattr(self.provider, "config", None)
            calls.append(dict(
                attempt=len(calls) + 1,
                model=getattr(config, "model", "offline / provider-defined"),
                messages=[message.model_dump(mode="json") for message in prompt.messages],
                max_output_tokens=prompt.max_output_tokens,
                budget_bytes=prompt.budget_bytes,
                evidence=[hit.model_dump(mode="json") for hit in prompt.evidence],
                evidence_omitted=prompt.evidence_omitted,
                evidence_aliases=prompt.evidence_aliases,
                history_used=prompt.history_count,
                history_omitted=prompt.omitted_history_count,
                response_format=getattr(config, "response_format", None),
                reasoning_effort=getattr(config, "reasoning_effort", None),
                reasoning_enabled=getattr(config, "reasoning_enabled", None),
            ))
        return await self.provider.generate(prompt, timeout_s)
