"""Transparent provider wrapper that logs what is sent to the model and what comes back."""

import time

from .diagnostics import event as log_event, verbose_event

RAW_REPLY_LIMIT = 2048
PROMPT_LIMIT = 6000


class LoggingProvider:
    """Logs one MODEL_INPUT and one MODEL_OUTPUT per call; the reply (or failure) passes through unchanged.

    The runner (or bridge) may call `note(**fields)` to add context the provider cannot see in the prompt itself
    (NPC name, persona source, turn type, move, scene items). Notes persist until overwritten.
    """

    def __init__(self, inner, clock=time.monotonic):
        self.inner = inner
        self.clock = clock
        self.context: dict = {}
        self.calls = 0

    def note(self, **fields):
        self.context.update({key: value for key, value in fields.items() if value is not None})

    def __getattr__(self, name):  # aclose, config and any other transport attribute
        return getattr(self.inner, name)

    async def generate(self, prompt, timeout_s):
        self.calls += 1
        call = self.calls
        notes = self.context
        messages = tuple(prompt.messages)
        log_event("MODEL_INPUT", call=call, npc=notes.get("npc", "unknown"), persona_source=notes.get("persona_source", "unknown"),
                  turn_type=notes.get("turn_type", "unknown"), move=notes.get("move", "unknown"), attempt=notes.get("attempt", 1),
                  messages=len(messages), prompt_bytes=sum(len(item.content.encode("utf-8")) for item in messages),
                  evidence=len(getattr(prompt, "evidence", ())), history=getattr(prompt, "history_count", 0),
                  scene_items=notes.get("scene_items", 0), timeout_s=round(timeout_s, 2))
        for index, item in enumerate(messages):  # full text only with --verbose, in console and file alike
            verbose_event("MODEL_PROMPT", _limit=PROMPT_LIMIT, call=call, message=index, role=item.role,
                          chars=len(item.content), text=item.content)
        began = self.clock()
        try:
            reply = await self.inner.generate(prompt, timeout_s)
        except BaseException as error:
            code = getattr(error, "code", None) or type(error).__name__
            status = "cancelled" if type(error).__name__ == "CancelledError" else "failed"
            log_event("MODEL_OUTPUT", call=call, status=status, error=code, latency_ms=round((self.clock() - began) * 1000))
            raise
        log_event("MODEL_OUTPUT", _limit=RAW_REPLY_LIMIT, call=call, status="ok", model=reply.model,
                  finish_reason=reply.finish_reason, input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
                  latency_ms=round((self.clock() - began) * 1000), raw=reply.content)
        return reply


def provider_label(provider) -> tuple[str, str]:
    """(kind, model) for the start-up banner."""
    inner = getattr(provider, "inner", provider)
    config = getattr(inner, "config", None)
    if config is not None:
        return "chat_completions", str(config.model)
    return type(inner).__name__, "offline-demo" if type(inner).__name__ == "OfflineProvider" else "unknown"
