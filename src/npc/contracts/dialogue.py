"""Contracts for the single-turn generation/validation preview."""

from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from npc.contracts.context import Contract, Identifier, Move, Nonnegative, ShortText
from npc.contracts.knowledge import Evidence, KnowledgeContext

Cue = Literal["look_at", "resume_task", "guarded_stance", "dismissive_gesture"]
ViolationCode = Literal[
    "invalid_json", "invalid_schema", "wrong_mode", "empty_speech", "unexpected_text",
    "too_many_words", "too_many_sentences", "too_many_characters", "unsupported_cue",
    "unknown_evidence", "irrelevant_boundary", "assistant_scaffolding", "incomplete_generation", "stale_state",
    "repeated_reply", "move_not_made", "unsupported_name",
]


class ResponseBody(Contract):
    mode: Literal["speech", "silence", "end"]
    text: Annotated[str, Field(strict=True, max_length=2000)]
    cue: Cue | None = None
    evidence_ids: Annotated[tuple[Identifier, ...], Field(max_length=16)] = ()


class InteractionPlan(Contract):
    route: Literal["authored", "generate", "silent", "end"]
    intent: Identifier
    affect: Identifier
    knowledge_need: Literal["none", "required", "uncertain"]
    max_words: Annotated[StrictInt, Field(ge=0, le=90)]
    max_sentences: Annotated[StrictInt, Field(ge=0, le=4)]
    allowed_modes: Annotated[tuple[Literal["speech", "silence", "end"], ...], Field(min_length=1, max_length=3)]
    allowed_cues: Annotated[tuple[Cue, ...], Field(max_length=4)] = ()
    authored_text: Annotated[str, Field(strict=True, max_length=400)] = ""
    fallback_text: Annotated[str, Field(strict=True, min_length=1, max_length=200)] = "I cannot say."
    reason_codes: Annotated[tuple[Identifier, ...], Field(max_length=16)]
    move: Move | None = None
    policy_version: Literal["0.1"] = "0.1"

    @model_validator(mode="after")
    def check_route(self) -> "InteractionPlan":
        if self.route in {"silent", "end"}:
            if self.max_words or self.max_sentences or self.authored_text:
                raise ValueError("non-speaking plans must not allocate speech")
            required = "silence" if self.route == "silent" else "end"
            if self.allowed_modes != (required,):
                raise ValueError("non-speaking route/mode mismatch")
        elif self.allowed_modes != ("speech",) or not self.max_words or not self.max_sentences:
            raise ValueError("speaking plans require speech mode and nonzero limits")
        if self.route == "authored" and not self.authored_text.strip():
            raise ValueError("authored route requires a line")
        return self


class ChatMessage(Contract):
    role: Literal["system", "user"]
    content: Annotated[str, Field(strict=True, min_length=1, max_length=32000)]


class PromptPackage(Contract):
    messages: Annotated[tuple[ChatMessage, ...], Field(min_length=2, max_length=3)]
    budget_bytes: Nonnegative
    max_output_tokens: Annotated[StrictInt, Field(ge=32, le=512)] = 160
    template_version: Literal["0.1"] = "0.1"
    evidence: Annotated[tuple[Evidence, ...], Field(max_length=3)] = ()
    evidence_omitted: Nonnegative = 0
    evidence_aliases: Annotated[dict[Identifier, Identifier], Field(max_length=3)] = Field(default_factory=dict)
    history_count: Nonnegative = 0
    omitted_history_count: Nonnegative = 0
    sections_dropped: tuple[str, ...] = ()


class ProviderReply(Contract):
    content: Annotated[str, Field(strict=True, max_length=8192)]
    model: Identifier
    finish_reason: Identifier
    input_tokens: Nonnegative | None = None
    output_tokens: Nonnegative | None = None


class CurrentState(Contract):
    dialogue_revision: Nonnegative
    turn_epoch: Nonnegative


class ValidationResult(Contract):
    accepted: bool
    violations: tuple[ViolationCode, ...]
    response: ResponseBody | None = None


class TurnPreview(Contract):
    request_id: Identifier
    npc_id: Identifier
    status: Literal["authored_success", "policy_silence", "model_success", "repair_success", "error_fallback", "cancelled", "busy"]
    plan: InteractionPlan
    response: ResponseBody | None
    model_calls: Annotated[StrictInt, Field(ge=0, le=2)]
    violations: tuple[ViolationCode, ...] = ()
    error_code: Identifier | None = None
    model: Identifier | None = None
    elapsed_ms: float = Field(ge=0)
    stage_ms: Annotated[dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]], Field(max_length=12)] = Field(default_factory=dict)
    input_tokens: Nonnegative = 0
    output_tokens: Nonnegative = 0
    usage_complete: bool = True
    response_deadline_ms: Nonnegative
    model_cutoff_ms: Nonnegative
    timing_profile: Literal["realtime", "development"]
    history_used: Nonnegative = 0
    history_omitted: Nonnegative = 0
    knowledge: KnowledgeContext | None = None
    evidence_used: Annotated[tuple[Identifier, ...], Field(max_length=3)] = ()
    evidence_omitted: Nonnegative = 0
    turn_type: Identifier | None = None
    # Milliseconds spent in retrieval, prompt, model (including any retry delay) and validation.
    # Response-ready only: not a durable delivery acknowledgement.
