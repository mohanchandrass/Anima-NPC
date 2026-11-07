"""Bounded response-ready preview, without persistence or game delivery."""

import asyncio
from contextlib import asynccontextmanager
from time import monotonic
from typing import Callable

from pydantic import Field, StrictInt, model_validator

from npc.behavior import apply_material, apply_move, choose_move, choose_plan
from npc.context.conversation import read_turn
from npc.context import build_situation
from npc.context.assembly import assemble_knowledge
from npc.contracts.knowledge import KnowledgeContext, Retriever
from npc.contracts.context import ContextScenario, Contract
from npc.contracts.dialogue import CurrentState, ProviderReply, ResponseBody, TurnPreview
from npc.generation.prompt import PromptTooLarge, build_prompt
from npc.generation.provider import ModelProvider, ProviderFailure
from npc.validation import validate_response
from npc.vision import VisualContext


class PreviewLimits(Contract):
    response_timeout_ms: StrictInt = Field(default=3000, ge=100, le=60000)
    model_cutoff_ms: StrictInt = Field(default=2450, ge=50, le=59000)
    max_input_bytes: StrictInt = Field(default=2048, ge=512, le=32000)
    retrieval_timeout_ms: StrictInt = Field(default=750, ge=10, le=5000)
    max_output_tokens: StrictInt = Field(default=160, ge=32, le=512)
    # Optional per-call limit (a real-time profile): a call slower than this is abandoned and may be retried once.
    provider_attempt_ms: StrictInt | None = Field(default=None, ge=100, le=59000)
    # Pause before the single retry of a transient provider failure. It counts against model_cutoff_ms.
    retry_delay_ms: StrictInt = Field(default=250, ge=0, le=5000)
    # Opt-in: retry provider_unavailable/provider_timeout once. Off by default (one call plus the single repair).
    retry_transient: bool = False

    @model_validator(mode="after")
    def reserve_response_time(self) -> "PreviewLimits":
        if self.model_cutoff_ms >= self.response_timeout_ms - 10:
            raise ValueError("reserve time after model work for validation/fallback")
        return self


async def _await_work(work, timeout_s, cancellation, retain_cleanup):
    task = asyncio.create_task(work)
    cancel_task = asyncio.create_task(cancellation.wait()) if cancellation is not None else None
    tasks = {task} | ({cancel_task} if cancel_task else set())
    try:
        done, _ = await asyncio.wait(tasks, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED)
        if cancel_task is not None and cancel_task in done:
            raise ProviderFailure("cancelled")
        if task not in done:
            raise ProviderFailure("provider_timeout")
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            retain_cleanup(task)
        else:
            # Retrieve exceptions even when cancellation won the race.
            if not task.cancelled():
                task.exception()
        if cancel_task is not None:
            cancel_task.cancel()
            await asyncio.gather(cancel_task, return_exceptions=True)


# Only transient provider failures are retried, and only with the second of the two allowed calls.
# Auth, rate-limit, bad-request, transport and protocol failures are never retried.
RETRYABLE = frozenset({"provider_unavailable", "provider_timeout"})


class PreviewRunner:
    """Reuse ONE instance to bound local request work. No pending-work queue."""

    def __init__(self, provider: ModelProvider, limits: PreviewLimits | None = None, *, clock: Callable[[], float] = monotonic,
                 retriever: Retriever | None = None, name_allowlist: tuple[str, ...] = ()):
        self.name_allowlist = tuple(name_allowlist)
        self.provider = provider
        self.retriever = retriever
        self.limits = limits or PreviewLimits()
        self.clock = clock
        self._slot = asyncio.Lock()
        self._pending_cleanup: asyncio.Task | None = None
        self._owners: set[tuple[str, str, str, str]] = set()

    async def aclose(self):
        """Release the provider's shared connection, if it keeps one."""
        close = getattr(self.provider, "aclose", None)
        if close is not None:
            await close()

    def _retain_cleanup(self, task: asyncio.Task):
        # At most one outstanding request task: its admission remains held.
        self._pending_cleanup = task

        def settled(completed):
            if not completed.cancelled():
                completed.exception()
            if self._pending_cleanup is completed:
                self._pending_cleanup = None

        task.add_done_callback(settled)

    @asynccontextmanager
    async def _admission(self):
        await self._slot.acquire()
        try:
            yield
        finally:
            cleanup = self._pending_cleanup
            if cleanup is not None and not cleanup.done():
                cleanup.add_done_callback(lambda _: self._slot.release())
            else:
                self._slot.release()

    async def run(self, scenario: ContextScenario, *, cancellation: asyncio.Event | None = None,
                  current_state: Callable[[], CurrentState] | None = None, visual_context: VisualContext | None = None) -> TurnPreview:
        key = (scenario.turn.scope.game_id, scenario.turn.scope.world_id, scenario.turn.scope.save_branch_id, scenario.turn.npc_id)
        if key in self._owners:
            situation = build_situation(scenario.turn, scenario.snapshot, scenario.now, visual_context=visual_context)
            return TurnPreview(request_id=situation.request_id, npc_id=situation.npc_id, status="busy",
                               plan=choose_plan(situation), response=None, model_calls=0, error_code="npc_busy", elapsed_ms=0,
                               response_deadline_ms=self.limits.response_timeout_ms, model_cutoff_ms=self.limits.model_cutoff_ms,
                               timing_profile=self._timing_profile())
        self._owners.add(key)
        try:
            return await self._run(scenario, cancellation=cancellation, current_state=current_state, visual_context=visual_context)
        finally:
            self._owners.discard(key)

    def _timing_profile(self):
        return "realtime" if (self.limits.response_timeout_ms, self.limits.model_cutoff_ms) == (3000, 2450) else "development"

    async def _run(self, scenario: ContextScenario, *, cancellation: asyncio.Event | None = None,
                   current_state: Callable[[], CurrentState] | None = None, visual_context: VisualContext | None = None) -> TurnPreview:
        start = self.clock()
        stage_ms = {key: 0.0 for key in ("context_policy", "retrieval", "prompt", "generation", "repair", "validation")}
        situation = build_situation(scenario.turn, scenario.snapshot, scenario.now, visual_context=visual_context)
        plan = choose_plan(situation)
        conv = situation.conversation
        # Turn type comes from the store when it recorded one; otherwise it is read here (same pure function).
        reading = situation.reading or read_turn(situation.player_input.text, conv)
        if plan.route == "generate":
            plan = apply_move(plan, choose_move(reading, conv, situation.persona, situation.player_input.text), conv,
                              situation.persona, situation.request_id)
        stage_ms["context_policy"] = max(0, (self.clock() - start) * 1000)

        @asynccontextmanager
        async def timed(stage):
            began = self.clock()
            try:
                yield
            finally:
                elapsed = max(0, (self.clock() - began) * 1000)
                stage_ms[stage] += elapsed
                if stage in {"generation", "repair"}:
                    stage_ms["model"] += elapsed
        current_state = current_state or (lambda: CurrentState(dialogue_revision=situation.dialogue_revision, turn_epoch=situation.turn_epoch))
        calls, violations, model = 0, (), None
        input_tokens, output_tokens, usage_complete = 0, 0, True
        history_used, history_omitted = 0, 0
        stage_ms["model"] = 0.0

        def spend(stage, since):
            stage_ms[stage] = stage_ms.get(stage, 0) + max(0, (self.clock() - since) * 1000)

        evidence_used, evidence_omitted = (), 0
        knowledge = KnowledgeContext(scope=situation.scope, npc_id=situation.npc_id,
                                     status="not_requested" if self.retriever else "disabled")

        def stale():
            state = current_state()
            return (state.dialogue_revision, state.turn_epoch) != (situation.dialogue_revision, situation.turn_epoch)

        def stopped():
            return (cancellation is not None and cancellation.is_set()) or stale()

        def finish(status, response=None, error=None):
            # Never turn stale/cancelled work into an audible fallback.
            if stopped():
                status, response, error = "cancelled", None, "cancelled_or_stale"
            return TurnPreview(request_id=situation.request_id, npc_id=situation.npc_id, status=status,
                               plan=plan, response=response, model_calls=calls, violations=violations,
                               error_code=error, model=model, stage_ms=stage_ms, elapsed_ms=max(0, (self.clock()-start)*1000),
                               input_tokens=input_tokens, output_tokens=output_tokens, usage_complete=usage_complete,
                               response_deadline_ms=self.limits.response_timeout_ms, model_cutoff_ms=self.limits.model_cutoff_ms,
                               timing_profile=self._timing_profile(), history_used=history_used, history_omitted=history_omitted,
                               knowledge=knowledge, evidence_used=evidence_used, evidence_omitted=evidence_omitted,
                               turn_type=reading.type)

        def checked(body):
            reply = ProviderReply(content=body.model_dump_json(), model="authored", finish_reason="stop")
            began = self.clock()
            try:
                return validate_response(reply, situation, plan, current_state(), check_conversation=False)
            finally:
                stage_ms["validation"] += max(0, (self.clock() - began) * 1000)

        def fallback(code):
            if (self.clock()-start)*1000 >= self.limits.response_timeout_ms:
                return finish("error_fallback", error="response_deadline")
            body = ResponseBody(mode="speech", text=plan.fallback_text)
            validation = checked(body)
            return finish("error_fallback", validation.response, code if validation.accepted else "invalid_fallback")

        if stopped():
            return finish("cancelled", error="cancelled_or_stale")
        if plan.route != "generate":
            body = ResponseBody(mode="end" if plan.route == "end" else "silence" if plan.route == "silent" else "speech",
                                text=plan.authored_text)
            validation = checked(body)
            return finish("policy_silence" if plan.route in {"silent", "end"} else "authored_success",
                          validation.response, None if validation.accepted else "invalid_authored_response")
        if self._slot.locked():
            return finish("busy", error="provider_busy")
        async with self._admission():
            if self.retriever is not None and plan.knowledge_need != "none":
                remaining = min(self.limits.retrieval_timeout_ms / 1000,
                                self.limits.model_cutoff_ms / 1000 - (self.clock() - start))
                if remaining <= 0:
                    return fallback("model_cutoff")
                began = self.clock()
                try:
                    async with timed("retrieval"):
                        retrieved = await _await_work(self.retriever.retrieve(situation, timeout_s=remaining),
                                                     remaining, cancellation, self._retain_cleanup)
                        knowledge = assemble_knowledge(situation, retrieved)
                    told = set(conv.told)
                    if told and knowledge.status in {"ok", "empty"}:  # never re-offer a passage already given
                        fresh = tuple(hit for hit in knowledge.evidence if hit.id not in told)
                        knowledge = knowledge.model_copy(update=dict(evidence=fresh, status="ok" if fresh else "empty"))
                    plan = apply_material(plan, knowledge, conv)
                except ProviderFailure as error:
                    if error.code == "cancelled":
                        return finish("cancelled", error="cancelled")
                    knowledge = KnowledgeContext(scope=situation.scope, npc_id=situation.npc_id, status="unavailable")
                    return fallback("retrieval_timeout")
                if knowledge.status in {"unavailable", "forbidden"}:
                    return fallback("retrieval_" + knowledge.status)
            for attempt in range(2):
                if stopped():
                    return finish("cancelled", error="cancelled_or_stale")
                remaining = self.limits.model_cutoff_ms/1000 - (self.clock()-start)
                if remaining <= 0:
                    return fallback("model_cutoff")
                began = self.clock()
                try:
                    async with timed("prompt"):
                        prompt = build_prompt(situation, plan, knowledge=knowledge if self.retriever else None, max_input_bytes=self.limits.max_input_bytes,
                                              max_output_tokens=self.limits.max_output_tokens, repair_codes=violations if attempt else ())
                        history_used, history_omitted = prompt.history_count, prompt.omitted_history_count
                        evidence_used = tuple(hit.id for hit in prompt.evidence)
                        evidence_omitted = prompt.evidence_omitted
                except PromptTooLarge:
                    return fallback("prompt_budget")
                remaining = self.limits.model_cutoff_ms/1000 - (self.clock()-start)
                if remaining <= 0:
                    return fallback("model_cutoff")
                calls += 1
                limit = remaining if self.limits.provider_attempt_ms is None else min(remaining, self.limits.provider_attempt_ms / 1000)
                began = self.clock()
                try:
                    async with timed("repair" if attempt else "generation"):
                        reply = await _await_work(self.provider.generate(prompt, limit), limit, cancellation, self._retain_cleanup)
                except ProviderFailure as error:
                    usage_complete = False
                    if error.code == "cancelled":
                        return finish("cancelled", error="cancelled")
                    if self.limits.retry_transient and error.code in RETRYABLE and calls == 1 and attempt == 0:
                        delay = self.limits.retry_delay_ms / 1000
                        if (self.clock() - start + delay) * 1000 >= self.limits.model_cutoff_ms:
                            return fallback(error.code)
                        began = self.clock()
                        try:
                            await asyncio.wait_for(cancellation.wait(), delay) if cancellation is not None else await asyncio.sleep(delay)
                        except TimeoutError:
                            pass
                        spend("model", began)  # The delay is part of the model wait and of the cutoff.
                        cleanup = self._pending_cleanup
                        if cleanup is not None and not cleanup.done():
                            return fallback(error.code)  # An abandoned call is still unwinding; never run two at once.
                        continue
                    return fallback(error.code)
                model = reply.model
                usage_complete = usage_complete and reply.input_tokens is not None and reply.output_tokens is not None
                input_tokens += reply.input_tokens or 0
                output_tokens += reply.output_tokens or 0
                if stopped():
                    return finish("cancelled", error="cancelled_or_stale")
                if (self.clock()-start)*1000 >= self.limits.model_cutoff_ms:
                    return fallback("late_completion")
                async with timed("validation"):
                    validation = validate_response(reply, situation, plan, current_state(), evidence=prompt.evidence,
                                                   evidence_aliases=prompt.evidence_aliases, name_allowlist=self.name_allowlist)
                violations = tuple(dict.fromkeys(violations + validation.violations))
                if validation.accepted:
                    return finish("repair_success" if attempt and violations else "model_success", validation.response)
                if "stale_state" in validation.violations:
                    return finish("cancelled", error="stale_state")
            return fallback("irrelevant_boundary" if "irrelevant_boundary" in violations else "invalid_candidate")
        raise AssertionError("unreachable")
