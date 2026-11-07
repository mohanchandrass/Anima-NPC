"""One-player connection lifecycle, independent of model and game implementation."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import time
import uuid
from collections import deque

import httpx

from .assets import Artifacts, MAX_WAV_BYTES, mouth_envelope, read_audio, stage_audio, stage_playback, wav_duration
from .audio_output import SystemAudio
from .say_output import next_topic, stage_say
from .voice_types import VoiceTypeResolver
from .protocol import GameEvent, NpcProfile, memory_branch, PipelineResult, Settings, SpeechInput, canonical_scenario, npc_id, profile_for
from .personas import load_rules
from .windows import Microphone, capture_client
from .diagnostics import event as log_event
from npc.vision import CVSceneClient, CVFailure, VisualContext
from npc.state.social_store import SocialStore, StoreFailure
from npc.behavior.moves import opening_plan
from npc.contracts.context import Relationship, Scope, PlayerInput


class ConnectionError(ValueError):
    pass


@dataclass
class Session:
    event: GameEvent
    revision: int = 0
    last_sequence: int = 0
    request_id: str | None = None
    deadline: float = 0
    delivery: dict | None = None
    delivery_status: str | None = None
    inputs: dict = field(default_factory=dict)
    input_payloads: dict = field(default_factory=dict)
    social_key: str | None = None
    social_state: dict | None = None
    cancelled_deliveries: dict = field(default_factory=dict)
    events: set = field(default_factory=set)
    screenshot: dict | None = None
    context_task: asyncio.Task | None = None
    opening_task: asyncio.Task | None = None
    visual_task: asyncio.Task | None = None
    turn_task: asyncio.Task | None = None
    cancelled_tasks: set = field(default_factory=set)
    visual: VisualContext | None = None
    visual_revision: int = -1
    visual_time: float = 0
    visual_status: str = "disabled"
    latency_request: str | None = None
    latency_start: float = 0
    staged_at: float = 0
    latency_timings: dict = field(default_factory=dict)
    latency_origin: str = "text_input"
    tts_outcome: str = "not_requested"
    timings: dict = field(default_factory=dict)  # request_id -> stage record for TURN_TIMING, until emitted


PLAYBACK_PAD_S = 0.4
TIMING_STAGES = ("stt_ms", "context_wait_ms", "retrieval_ms", "prompt_ms", "model_ms", "validation_ms",
                 "pipeline_ms", "tts_ms", "staging_ms", "native_start_ms")


class Bridge:
    def __init__(self, settings: Settings, *, root: Path, capture=capture_client,
                 client: httpx.AsyncClient | None = None, runner=None, microphone=None, clock=time.time, cv_client=None, audio=None, timer=time.monotonic):
        self.settings, self.root = settings, root.resolve()
        self.output = self.resolve(settings.output_directory)
        self.game = self.resolve(settings.game_directory)
        self.audio_root = self.resolve(settings.audio_root)
        self.artifacts = Artifacts(self.output, max_bytes=settings.max_output_bytes,
                                   max_screenshots=settings.max_screenshots, capture=capture)
        self.profiles = {}
        if settings.profiles_file:
            path = self.resolve(settings.profiles_file)
            if path.stat().st_size > 128 * 1024:
                raise ValueError("profiles exceed 128 KB")
            raw = json.loads(path.read_text(encoding="utf-8"))
            if len(raw) > 256:
                raise ValueError("too many NPC profiles")
            self.profiles = {key.lower(): NpcProfile.model_validate(value) for key, value in raw.items()}
        self.rules = None
        if settings.role_rules_file:
            self.rules = load_rules(self.resolve(settings.role_rules_file))
        if settings.pipeline_mode == "http" and not settings.pipeline_url:
            raise ValueError("pipeline_url is required in HTTP mode")
        self.client = client or httpx.AsyncClient(timeout=settings.timeout_s, trust_env=False,
                                                 limits=httpx.Limits(max_connections=3))
        self.state_store = SocialStore(self.resolve(settings.state_db), memory_source="game_completed") if settings.state_db else None
        self.runner = runner
        self.cv_client = cv_client or (CVSceneClient(settings.cv_url, settings.cv_timeout_s) if settings.cv_url else None)
        self.microphone = microphone or Microphone(self.output / "microphone")
        self.session: Session | None = None
        self.ended = deque(maxlen=16)
        self.clock = clock
        self.timer = timer
        self.error: str | None = None
        self._background: set[asyncio.Task] = set()
        self._microphone_task: asyncio.Task | None = None
        self._mic_gate = asyncio.Lock()
        self.audio = audio or SystemAudio()
        self._playback: dict[str, dict] = {}  # delivery_id -> {path, duration, ends_at}; system audio only
        self._playing: str | None = None
        self._say_topic, self._say_off = 0, False  # last Anima.esp topic used; True after the plugin reported no Anima.esp
        self.voice_types = VoiceTypeResolver(self.game / "Data")
        self._cleanups: set[asyncio.Task] = set()
        self._sweep_playback()

    def resolve(self, value):
        path = Path(value)
        return (path if path.is_absolute() else self.root / path).resolve()

    def spawn(self, coroutine):
        if len(self._background) >= 8:
            coroutine.close()
            raise ConnectionError("connector worker admission exhausted")
        task = asyncio.create_task(coroutine)
        self._background.add(task)

        def settled(done):
            self._background.discard(done)
            if not done.cancelled() and done.exception() is not None:
                self.error = type(done.exception()).__name__

        task.add_done_callback(settled)
        return task

    async def close(self):
        for task in tuple(self._background | self._cleanups):
            task.cancel()
        await asyncio.gather(*self._background, *self._cleanups, return_exceptions=True)
        self._drop_playback()
        await asyncio.to_thread(self.microphone.discard)
        try:
            runner_close = getattr(self.runner, "aclose", None)
            if runner_close is not None:
                await runner_close()  # The model provider keeps one connection open between turns.
        finally:
            await self.client.aclose()

    def _stage(self, session, request_id, name, since):
        record = session.timings.get(request_id)
        if record is not None:
            record[name] = round((self.clock() - since) * 1000, 1)

    def _timing_done(self, session, request_id, outcome):
        """Emit one TURN_TIMING line per turn, with every stage reached; unreached stages read `none`."""
        record = session.timings.pop(request_id, None)
        if record is None:
            return
        stages = {name: record.get(name, "none") for name in TIMING_STAGES}
        log_event("TURN_TIMING", session=session.event.session_id, request=request_id, outcome=outcome,
                  source=record.get("source", "text"), turn_type=record.get("turn_type", "none"), move=record.get("move", "none"),
                  total_ms=round((self.clock() - record["t0"]) * 1000, 1), **stages)

    def current(self, session_id):
        if self.session is None or self.session.event.session_id != session_id:
            raise ConnectionError("unknown or ended session")
        return self.session

    def invalidate(self, session):
        for request_id in tuple(session.timings):
            self._timing_done(session, request_id, "cancelled")
        if session.turn_task and not session.turn_task.done():
            session.cancelled_tasks.add(session.turn_task)
            session.turn_task.cancel()
        if self.state_store and session.social_key:
            request = session.request_id or (session.delivery["request_id"] if session.delivery and session.delivery_status not in {"completed", "interrupted", "failed"} else None)
            if request:
                self.state_store.cancel(session.social_key, request)
        if session.request_id:
            session.inputs[session.request_id] = "cancelled"
        session.request_id = None
        if session.delivery and session.delivery_status not in {"completed", "interrupted", "failed"}:
            session.cancelled_deliveries[session.delivery["delivery_id"]] = dict(session.delivery)
            self._drop_playback(session.delivery["delivery_id"])
            session.delivery = {**session.delivery, "kind": "stop"}

    async def game_event(self, event: GameEvent):
        if event.session_id in self.ended:
            if event.kind == "end":
                return {"status": "duplicate"}
            raise ConnectionError("session already ended; open a new conversation")
        if event.kind == "start":
            if not event.snapshot:
                raise ConnectionError("start requires snapshot")
            if self.session and self.session.event.session_id == event.session_id:
                if event.event_id in self.session.events:
                    return {"status": "duplicate"}
                raise ConnectionError("session already started")
            if self.session:
                await self.end(self.session)
            session = Session(event=event, revision=event.state_revision, last_sequence=event.sequence)
            session.events.add(event.event_id)
            self.session = session
            log_event("SESSION_OPEN", session=event.session_id, npc=npc_id(event.snapshot),
                      name=event.snapshot.npc.base.name if event.snapshot.npc.base else "unknown")
            session.context_task = self.spawn(self.publish_context(session, "context_ready"))
            if self.settings.opening_lines and event.direct:  # vanilla menu sessions already have the NPC's own greeting
                session.opening_task = self.spawn(self.open_conversation(session))
            return {"status": "ready", "npc_id": npc_id(event.snapshot)}
        session = self.current(event.session_id)
        if event.game_epoch != session.event.game_epoch:
            raise ConnectionError("game epoch mismatch")
        if event.event_id in session.events:
            return {"status": "duplicate"}
        if event.sequence <= session.last_sequence:
            raise ConnectionError("out of order game event")
        if len(session.events) >= 4096:
            raise ConnectionError("session event limit reached; start a new conversation")
        if event.kind == "snapshot":
            if not event.snapshot or npc_id(event.snapshot) != npc_id(session.event.snapshot):
                raise ConnectionError("snapshot actor mismatch")
            if event.state_revision < session.revision:
                raise ConnectionError("out of order state revision")
            changed = event.state_revision != session.revision
            if changed:
                self.invalidate(session)
                session.visual = None
                session.revision = event.state_revision
            session.event = event
            # Coalesce context notifications while a bounded capture/HTTP operation runs.
            if changed and (not session.context_task or session.context_task.done()):
                session.context_task = self.spawn(self.publish_context(session, "context_updated"))
        elif event.kind == "end":
            await self.end(session)
        elif event.kind == "delivery":
            current = session.delivery and event.delivery_id == session.delivery["delivery_id"]
            retired = session.cancelled_deliveries.get(event.delivery_id)
            command = session.delivery if current else retired
            if not command or not event.status:
                raise ConnectionError("unknown delivery")
            if current and session.delivery_status in {"completed", "interrupted", "failed"}:
                session.events.add(event.event_id)
                session.last_sequence = event.sequence
                return {"status": session.delivery_status}
            if current and event.status == "failed" and event.reason in {"say_unavailable", "audio_failed"} and command.get("topic_index"):
                self._say_fallback(session, command, event.reason)
                session.events.add(event.event_id)
                session.last_sequence = event.sequence
                return {"status": "fallback_system"}
            if self.state_store and session.social_key and not retired:
                if event.status == "completed":
                    self.state_store.displayed(session.social_key, command["request_id"])
                elif event.status in {"interrupted", "failed"}:
                    self.state_store.cancel(session.social_key, command["request_id"])
            if current:
                first_start = event.status == "started" and session.delivery_status != "started"
                session.delivery_status = event.status
                if first_start:
                    self._start_playback(event.delivery_id)
                record = session.timings.get(command["request_id"])
                if record is not None and "staged_at" in record:
                    self._stage(session, command["request_id"], "native_start_ms", record["staged_at"])
                    self._timing_done(session, command["request_id"], event.status)
            if (not retired and event.status == "started" and session.latency_request == command["request_id"]
                    and "delivery_wait" not in session.latency_timings):
                session.latency_timings["delivery_wait"] = max(0, (self.timer() - session.staged_at) * 1000)
                key = "playback_start_receipt" if command.get("audio_file") or event.delivery_id in self._playback else "subtitle_start_receipt"
                session.latency_timings[key] = max(0, (self.timer() - session.latency_start) * 1000)
                self.log_latency(session, command["request_id"], "started")
            log_event("NATIVE_ACK", session=event.session_id, request=command["request_id"],
                      delivery=event.delivery_id, status=event.status, reason=event.reason or "unreported",
                      audio=event.audio or "unreported", heard="unverified",
                      memory_committed=event.status == "completed" and not retired and bool(self.state_store))
            if event.status in {"completed", "interrupted", "failed"}:
                self._remove_staged_audio(session, command)
                if event.status == "completed":
                    self._finish_playback(event.delivery_id)
                else:
                    self._drop_playback(event.delivery_id)
                session.cancelled_deliveries.pop(event.delivery_id, None)
            self.spawn(self.publish({"kind": "delivery", "session_id": event.session_id,
                                "delivery_id": event.delivery_id, "request_id": command["request_id"],
                                "status": event.status, "heard_text": None}))
        elif event.kind == "player_text":
            if not event.text or not event.text.strip():
                raise ConnectionError("text entry requires nonempty text")
            await self.player_input(SpeechInput(session_id=event.session_id, input_id=event.event_id, text=event.text), interrupt=True)
        elif event.kind == "listen_start":
            self._cancel_opening(session)
            if not self.settings.stt_url:
                self.error = "stt_not_configured"
            elif self._microphone_task and not self._microphone_task.done():
                raise ConnectionError("microphone busy")
            else:
                self._microphone_task = self.spawn(self.listen(session))
        elif event.kind == "listen_stop":
            self.spawn(self.finish_listening(session))
        session.events.add(event.event_id)
        session.last_sequence = event.sequence
        return {"status": "accepted"}

    def _cancel_opening(self, session):
        task = session.opening_task
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
        session.opening_task = None

    async def open_conversation(self, session):
        """Authored greeting at session start. Not a player turn: nothing is recorded in memory for it.

        Waits briefly so the NPC's own vanilla greeting is not talked over; any player input cancels it.
        """
        await asyncio.sleep(self.settings.opening_delay_s)
        if (self.session is not session or session.inputs or session.request_id or session.delivery
                or session.opening_task is not asyncio.current_task()):
            return
        data = session.event.snapshot
        if data.npc.dead or data.npc.in_combat or data.npc.hostile_to_player:
            return
        profile = profile_for(data, self.profiles, self.rules)
        request_id = "opening-" + uuid.uuid4().hex
        plan = opening_plan(profile.persona, request_id)
        session.request_id = request_id
        session.deadline = self.clock() + self.settings.timeout_s
        session.inputs[request_id] = "pending"
        log_event("OPENING", session=session.event.session_id, request=request_id, npc=npc_id(data), text=plan.authored_text)
        try:
            await self.result(PipelineResult(session_id=session.event.session_id, request_id=request_id,
                                             npc_id=npc_id(data), mode="speech", text=plan.authored_text, move="opening"))
        except asyncio.CancelledError:
            if session.request_id == request_id:
                session.request_id = None
            session.inputs[request_id] = "cancelled"
            raise
        except (ConnectionError, ValueError, OSError):
            if session.request_id == request_id:
                session.request_id = None
            session.inputs[request_id] = "failed"

    def _sweep_playback(self):
        try:
            for stale in (self.output / "speech" / "playback").glob("*.wav"):
                stale.unlink(missing_ok=True)
        except OSError:
            pass

    def _start_playback(self, delivery_id):
        item = self._playback.get(delivery_id)
        if item is None or item.get("standby"):  # standby: engine `say` is the voice; this copy is only the fallback
            return
        try:
            self.audio.play(item["path"])
            self._playing = delivery_id
            item["ends_at"] = time.monotonic() + item["seconds"]
            log_event("AUDIO_OUTPUT", mode="system", delivery=delivery_id, duration_ms=round(item["seconds"] * 1000))
        except Exception as error:  # The subtitle keeps running; audio never fails the turn.
            log_event("AUDIO_OUTPUT_FAILED", delivery=delivery_id, error=type(error).__name__)

    async def _stage_say(self, session, raw, text):
        """Write the Anima.esp voice (+ lip) files; None means use system audio instead."""
        voice = session.event.snapshot.npc.voice
        folder = await asyncio.to_thread(self.voice_types.resolve, voice.plugin, voice.local_id) if voice else None
        if not folder:
            log_event("SAY_UNAVAILABLE", reason="voice_type_unknown", fallback="system")
            return None
        topic = next_topic(self._say_topic)
        try:
            path, lip, seconds = await asyncio.to_thread(stage_say, raw, self.game, folder, topic, text, self.settings.facefx_directory)
        except (OSError, ValueError) as error:
            log_event("SAY_UNAVAILABLE", reason=type(error).__name__, fallback="system")
            return None
        self._say_topic = topic
        log_event("SAY_STAGED", voice_type=folder, topic_index=topic, lip=lip, file=path.name)
        return {"topic_index": topic, "seconds": seconds}

    def _say_fallback(self, session, command, reason="say_unavailable"):
        """The plugin has no Anima.esp (or its topic): replay the same line through system audio, and stop using say."""
        self._say_off = True
        new_id = uuid.uuid4().hex
        old_id = command["delivery_id"]
        session.delivery = {key: value for key, value in command.items() if key != "topic_index"} | {"delivery_id": new_id}
        session.delivery_status = "pending"
        item = self._playback.pop(old_id, None)
        if item is not None:
            item.pop("standby", None)
            self._playback[new_id] = item
        log_event("SAY_FALLBACK", old_delivery=old_id, delivery=new_id, reason=reason, fallback="system")

    def _drop_playback(self, delivery_id=None):
        """Stop and delete one staged playback file (or all when no ID is given)."""
        for key in (delivery_id,) if delivery_id else tuple(self._playback):
            item = self._playback.pop(key, None)
            if item is None:
                continue
            if self._playing == key:
                self._playing = None
                try:
                    self.audio.stop()
                except Exception as error:
                    log_event("AUDIO_OUTPUT_FAILED", delivery=key, error=type(error).__name__)
            try:
                item["path"].unlink(missing_ok=True)
            except OSError:
                pass

    def _finish_playback(self, delivery_id):
        """After `completed`: let the sound reach its natural end, then delete the file."""
        item = self._playback.get(delivery_id)
        if item is None:
            return
        wait = max(0.0, item.get("ends_at", 0) - time.monotonic()) + 0.2
        if wait <= 0.2:
            self._drop_playback(delivery_id)
            return

        async def later():
            await asyncio.sleep(wait)
            self._drop_playback(delivery_id)

        task = asyncio.create_task(later())
        self._cleanups.add(task)
        task.add_done_callback(self._cleanups.discard)

    def _remove_staged_audio(self, session, delivery=None):
        delivery = delivery or session.delivery
        if delivery and delivery.get("audio_file"):
            relative = delivery["audio_file"]
            path = self.game / "Data" / relative
            # Only internally generated delivery filenames are ever removed.
            expected = self.game / "Data/Sound/Voice/AnimaConnector" / (delivery["delivery_id"] + ".wav")
            if path == expected:
                path.unlink(missing_ok=True)

    async def end(self, session):
        self.invalidate(session)
        if session.visual_task and not session.visual_task.done():
            session.visual_task.cancel()
        if session.context_task and not session.context_task.done():
            session.context_task.cancel()
        if self._microphone_task and not self._microphone_task.done():
            self._microphone_task.cancel()
        await asyncio.to_thread(self.microphone.discard)
        # Native side stops sound before emitting end; no success ack is fabricated.
        self._remove_staged_audio(session)
        self._drop_playback()
        for old in session.cancelled_deliveries.values():
            self._remove_staged_audio(session, old)
        session.cancelled_deliveries.clear()
        if self.session is session:
            self.session = None
        log_event("SESSION_CLOSED", session=session.event.session_id)
        self.ended.append(session.event.session_id)
        self.spawn(self.publish({"kind": "context_closed", "session_id": session.event.session_id}))

    async def publish(self, payload):
        identifier = uuid.uuid4().hex
        envelope = {"schema_version": "0.1", "event_id": identifier,
                    "captured_at": datetime.now(timezone.utc).isoformat(), **payload}
        self.artifacts.event(identifier, envelope)
        if self.settings.pipeline_mode == "http":
            async with self.client.stream("POST", self.settings.pipeline_url, json=envelope) as response:
                response.raise_for_status()
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 256 * 1024:
                        raise ConnectionError("pipeline response too large")
                if response.status_code != 202 and raw and payload["kind"] == "player_turn":
                    result = PipelineResult.model_validate_json(raw)
                    await self.result(result)
        return envelope

    async def publish_context(self, session, kind):
        if self.settings.capture_screenshots and (session.visual_task is None or session.visual_task.done()):
            session.visual_task = self.spawn(self.refresh_visual(session))
        if self.session is not session:
            return
        # Publish trusted context independently of optional background capture/analysis.
        event = session.event
        profile = profile_for(event.snapshot, self.profiles, self.rules)
        await self.publish({"kind": kind, "session_id": event.session_id,
                            "game_epoch": event.game_epoch, "npc_id": npc_id(event.snapshot),
                            "revision": session.revision, "game": event.snapshot.model_dump(mode="json"),
                            "npc_profile": profile.model_dump(mode="json"),
                            "knowledge_status": "authored" if profile.knowledge_sources else "unknown",
                            "screenshot": session.screenshot, "image_description": self.visual_payload(session)})

    async def refresh_visual(self, session):
        event = session.event
        revision = session.revision
        captured_time = self.clock()
        if self.settings.capture_screenshots:
            try:
                path = await asyncio.to_thread(self.artifacts.screenshot, uuid.uuid4().hex, event.game_pid)
                if self.session is not session or session.revision != revision:
                    session.visual_status = "stale"
                    return
                session.screenshot = {"path": path, "viewpoint": "player_camera", "status": "saved",
                                      "captured_at": datetime.now(timezone.utc).isoformat(),
                                      "npc_visibility": "unknown", "event_id": event.event_id}
                if self.cv_client:
                    session.visual_status = "analyzing"
                    try:
                        with Path(path).open("rb") as stream:
                            image = stream.read(10 * 1024 * 1024 + 1)
                        if len(image) > 10 * 1024 * 1024:
                            raise ValueError("CV image exceeds upload budget")
                        async with asyncio.timeout(self.settings.cv_timeout_s):
                            visual = await self.cv_client.analyze(image, "image/png")
                        if (self.session is session and session.revision == revision
                                and 0 <= self.clock() - captured_time < self.settings.visual_ttl_s):
                            session.visual = visual.model_copy(update={
                                "captured_at": datetime.fromisoformat(session.screenshot["captured_at"])})
                            session.visual_revision = revision
                            session.visual_time = captured_time
                            session.visual_status = "ready"
                        else:
                            session.visual_status = "stale"
                    except (CVFailure, TimeoutError, ValueError, OSError):
                        session.visual = None
                        session.visual_status = "unavailable"
            except (OSError, ValueError):
                session.screenshot = {"status": "unavailable", "viewpoint": "player_camera"}
                session.visual = None
                session.visual_status = "unavailable"

    def visual_reference(self, session):
        if (self.session is session and session.visual is not None and session.visual_revision == session.revision
                and 0 <= self.clock() - session.visual_time < self.settings.visual_ttl_s):
            return session.visual
        return None

    def visual_payload(self, session):
        visual = self.visual_reference(session)
        return visual.model_dump(mode="json") if visual else None

    async def player_input(self, speech: SpeechInput, *, interrupt=False, speech_end=None, stt_ms=None):
        session = self.current(speech.session_id)
        fingerprint = speech.model_dump(exclude={"session_id", "input_id"})
        if speech.input_id in session.inputs:
            if session.input_payloads.get(speech.input_id) != fingerprint:
                raise ConnectionError("input ID reused with different content")
            return {"request_id": speech.input_id, "status": session.inputs[speech.input_id]}
        self._cancel_opening(session)
        if session.delivery and str(session.delivery.get("request_id", "")).startswith("opening-"):
            interrupt = True  # the player speaking over the greeting simply replaces it
        if interrupt:
            self.invalidate(session)
        if len(session.inputs) >= 128:
            raise ConnectionError("conversation turn limit reached")
        if session.request_id is not None or (not interrupt and session.delivery and session.delivery_status not in {"completed", "interrupted", "failed"}):
            raise ConnectionError("turn or playback busy; interrupt playback before speaking again")
        if self.state_store:
            scope = Scope(game_id="skyrim", world_id=self.settings.world_id, save_branch_id=memory_branch(self.settings.memory_scope, session.event))
            session.social_key = self.state_store.key(scope, npc_id(session.event.snapshot), "ref:" + session.event.snapshot.player.reference.form_id.lower())
            try:
                initial = self.state_store.ensure(session.social_key, Relationship(), "neutral")
                profile = profile_for(session.event.snapshot, self.profiles, self.rules)
                state, audit, cached = self.state_store.begin_turn(session.social_key, speech.input_id,
                    PlayerInput(text=speech.text, source=speech.source, confidence=speech.confidence), profile.persona, initial["revision"])
            except StoreFailure:
                self.error = "social_state_unavailable"
                raise ConnectionError("social state unavailable") from None
            session.social_state = state
            if cached:
                return {"request_id": speech.input_id, "status": "duplicate_recorded_turn"}
            log_event("SOCIAL_STATE", request=speech.input_id, revision=state["revision"], mood=state["mood"],
                      stimulus=audit["stimulus"], memory_exchanges=len(state["history"]))
        session.latency_request = speech.input_id
        session.latency_start = self.timer() if speech_end is None else speech_end
        session.latency_origin = "text_input" if speech_end is None else "speech_end"
        session.latency_timings = {"stt": stt_ms or 0, "cv_wait": 0.0}
        session.tts_outcome = "not_requested"
        session.input_payloads[speech.input_id] = fingerprint
        session.request_id = speech.input_id
        session.deadline = self.clock() + self.settings.timeout_s
        session.inputs[speech.input_id] = "pending"
        session.timings[speech.input_id] = {"t0": self.clock(), "source": speech.source,
                                            **({"stt_ms": stt_ms} if stt_ms is not None else {})}
        log_event("PLAYER_INPUT", session=speech.session_id, request=speech.input_id,
                  npc=npc_id(session.event.snapshot), source=speech.source, text=speech.text)
        if (self.cv_client and self.visual_reference(session) is None
                and (not session.context_task or session.context_task.done())):
            session.context_task = self.spawn(self.publish_context(session, "context_updated"))
        session.turn_task = self.spawn(self.run_turn(session, speech))
        return {"request_id": speech.input_id, "status": "pending"}

    async def run_turn(self, session, speech):
        waited = self.clock()
        try:
            previous = tuple(task for task in session.cancelled_tasks if not task.done() and task is not asyncio.current_task())
            if previous:
                async with asyncio.timeout(max(0.001, session.deadline - self.clock())):
                    await asyncio.shield(asyncio.gather(*previous, return_exceptions=True))
            session.cancelled_tasks = {task for task in session.cancelled_tasks if not task.done()}
            context_wait = self.timer()
            if session.context_task:
                async with asyncio.timeout(max(0.001, session.deadline - self.clock())):
                    await asyncio.shield(session.context_task)
            session.latency_timings["context_wait"] = max(0, (self.timer() - context_wait) * 1000)
            self._stage(session, speech.input_id, "context_wait_ms", waited)
            if self.session is not session or session.request_id != speech.input_id:
                return
            profile = profile_for(session.event.snapshot, self.profiles, self.rules)
            scenario = canonical_scenario(
                session.event, speech, profile, self.settings.world_id, session.revision,
                social_state=session.social_state, screenshot=session.screenshot,
                image_description=self.visual_payload(session))
            payload = {"kind": "player_turn", "session_id": speech.session_id,
                       "request_id": speech.input_id, "npc_id": scenario.turn.npc_id,
                       "revision": session.revision, "expires_at": session.deadline,
                       "player_input": speech.model_dump(mode="json"),
                       "scenario": scenario.model_dump(mode="json"),
                       "game": session.event.snapshot.model_dump(mode="json"),
                       "npc_profile": profile.model_dump(mode="json"),
                       "screenshot": session.screenshot, "image_description": self.visual_payload(session)}
            remaining = session.deadline - self.clock()
            if remaining <= 0:
                raise ConnectionError("turn expired")
            async with asyncio.timeout(remaining):
                await self.publish(payload)
                record = session.timings.get(speech.input_id)
                if record is not None:
                    record["published_at"] = self.clock()
                if self.settings.pipeline_mode == "preview":
                    if self.runner is None:
                        raise ConnectionError("preview runner not configured")
                    began = self.clock()
                    try:
                        preview = await self.runner.run(
                            scenario, visual_context=self.visual_reference(session)
                            if self.settings.attach_visual_reference else None)
                    finally:
                        self._stage(session, speech.input_id, "pipeline_ms", began)
                    if record is not None:
                        for name, value in (getattr(preview, "stage_ms", None) or {}).items():
                            record[name + "_ms"] = value
                        record["turn_type"] = getattr(preview, "turn_type", None) or "none"
                        record["move"] = getattr(getattr(preview, "plan", None), "move", None) or "none"
                    session.latency_timings.update(getattr(preview, "stage_ms", {}))
                    log_event("PIPELINE_RESULT", session=speech.session_id, request=speech.input_id,
                              route=getattr(getattr(preview, "plan", None), "route", "unknown"),
                              status=getattr(preview, "status", "unknown"),
                              turn_type=getattr(preview, "turn_type", None), move=getattr(getattr(preview, "plan", None), "move", None),
                              model=getattr(preview, "model", None), calls=getattr(preview, "model_calls", 0),
                              repair_attempted=getattr(preview, "model_calls", 0) == 2, stage_ms=getattr(preview, "stage_ms", {}),
                              input_tokens=getattr(preview, "input_tokens", 0), output_tokens=getattr(preview, "output_tokens", 0))
                    if preview.response:
                        await self.result(PipelineResult(session_id=speech.session_id, request_id=speech.input_id,
                                                        npc_id=scenario.turn.npc_id, mode=preview.response.mode,
                                                        text=preview.response.text, move=preview.plan.move,
                                                        evidence_ids=tuple(preview.response.evidence_ids or preview.evidence_used)))
                    else:
                        raise ConnectionError("pipeline returned no deliverable response")
        except asyncio.CancelledError:
            session.inputs[speech.input_id] = "cancelled"
            self.log_latency(session, speech.input_id, "cancelled")
            log_event("TURN_CANCELLED", session=speech.session_id, request=speech.input_id)
            self._timing_done(session, speech.input_id, "cancelled")
            raise
        except (ValueError, OSError, httpx.HTTPError, TimeoutError):
            session.inputs[speech.input_id] = "failed"
            if session.request_id == speech.input_id:
                session.request_id = None
            self.error = "pipeline_turn_failed"
            self.log_latency(session, speech.input_id, "failed")
            log_event("TURN_FAILED", session=speech.session_id, request=speech.input_id, error=self.error)
            self._timing_done(session, speech.input_id, "failed")
        except Exception:
            self._timing_done(session, speech.input_id, "failed")
            raise

    async def result(self, result: PipelineResult):
        session = self.current(result.session_id)
        if session.delivery and session.delivery["request_id"] == result.request_id:
            return {"delivery_id": session.delivery["delivery_id"], "status": session.delivery_status}
        if (result.request_id != session.request_id or self.clock() >= session.deadline
                or result.npc_id != npc_id(session.event.snapshot)):
            raise ConnectionError("stale or uncorrelated pipeline result")
        if ((result.mode == "speech" and not result.text.strip())
                or (result.mode == "silence" and (result.text or result.audio_path))):
            raise ConnectionError("invalid mode/text combination")
        # Only one callback may synthesize/stage this turn. Release on failure for explicit retry.
        if session.inputs[result.request_id] == "staging":
            raise ConnectionError("result already staging")
        session.inputs[result.request_id] = "staging"
        revision = session.revision
        raw = None
        record = session.timings.get(result.request_id)
        if record is not None and "pipeline_ms" not in record and "published_at" in record:
            self._stage(session, result.request_id, "pipeline_ms", record["published_at"])
        tts_began = self.clock()
        session.latency_timings["validated_response_ready"] = max(0, (self.timer() - session.latency_start) * 1000)
        session.tts_outcome = "subtitle_fallback" if result.text and self.settings.tts_url and not result.audio_path else "not_requested"
        session.latency_timings["tts"] = 0.0
        try:
            if result.audio_path:
                raw = await asyncio.to_thread(read_audio, result.audio_path, self.audio_root)
            elif result.text and self.settings.tts_url and session.deadline - self.clock() > 0.5:
                tts_started = self.timer()
                try:
                    # Leave time to deliver a text-only result if synthesis fails.
                    async with asyncio.timeout(max(0.001, min(self.settings.tts_timeout_s, session.deadline - self.clock() - 0.5))):
                        speech_request = {
                            "request_id": result.request_id, "text": result.text,
                            "voice": session.event.snapshot.npc.voice.model_dump() if session.event.snapshot.npc.voice else None,
                            "sex": session.event.snapshot.npc.sex,
                        }
                        if self.settings.tts_url == f"http://127.0.0.1:{self.settings.port}/v1/speech/tts":
                            speech_request["npc_base"] = session.event.snapshot.npc.base.model_dump()
                            speech_request["npc_ref"] = session.event.snapshot.npc.reference.model_dump()
                        async with self.client.stream("POST", self.settings.tts_url, json=speech_request) as response:
                            response.raise_for_status()
                            data = bytearray()
                            async for chunk in response.aiter_bytes():
                                data.extend(chunk)
                                if len(data) > MAX_WAV_BYTES:
                                    raise ConnectionError("TTS audio exceeds limit")
                            raw = bytes(data)
                            wav_duration(raw)
                except (ValueError, httpx.HTTPError, TimeoutError):
                    raw = None
                    self.error = "tts_unavailable_text_only"
                    log_event("TTS_FAILED", request=result.request_id, fallback="text_only")
                finally:
                    session.latency_timings["tts"] = max(0, (self.timer() - tts_started) * 1000)
                    session.tts_outcome = "ready" if raw else "subtitle_fallback"
            self._stage(session, result.request_id, "tts_ms", tts_began)
            staging_began = self.clock()
            if (self.session is not session or session.request_id != result.request_id
                    or session.revision != revision or self.clock() >= session.deadline):
                raise ConnectionError("result became stale during audio preparation")
            delivery_id = uuid.uuid4().hex
            envelope = await asyncio.to_thread(mouth_envelope, raw) if raw else []
            if (self.session is not session or session.request_id != result.request_id
                    or session.revision != revision or self.clock() >= session.deadline):
                raise ConnectionError("result became stale during audio analysis")
            playback, say = None, None
            if raw and self.settings.audio_output == "say" and not self._say_off:
                say = await self._stage_say(session, raw, result.text)
            if say:
                playback, wav_seconds = stage_playback(raw, self.output, delivery_id)  # standby copy for the fallback
                relative, duration = None, say["seconds"] + PLAYBACK_PAD_S
            elif raw and self.settings.audio_output in {"system", "say"}:
                playback, wav_seconds = stage_playback(raw, self.output, delivery_id)
                relative, duration = None, wav_seconds + PLAYBACK_PAD_S
            else:
                relative, duration = stage_audio(raw, self.game, delivery_id) if raw else (None, min(15, max(2, len(result.text) / 15)))
            if self.state_store and session.social_key and session.social_state:
                try:
                    self.state_store.stage_preview(session.social_key, result.request_id,
                        {"result": {"response": {"mode": result.mode, "text": result.text}}}, session.social_state["revision"],
                        move=result.move, evidence_ids=result.evidence_ids)
                except StoreFailure:
                    if relative:
                        (self.game / "Data" / relative).unlink(missing_ok=True)
                    if playback:
                        playback.unlink(missing_ok=True)
                    self.error = "social_state_unavailable"
                    raise ConnectionError("social state unavailable") from None
            session.delivery = {"kind": "speak", "delivery_id": delivery_id, "session_id": result.session_id,
                                "request_id": result.request_id, "npc_id": result.npc_id,
                                "snapshot_sequence": session.revision, "mode": result.mode,
                                "text": result.text, "audio_file": relative, "duration_s": duration, "mouth": envelope,
                                "expires_at_ms": int(session.deadline * 1000)}
            if say:
                session.delivery["topic_index"] = say["topic_index"]
            session.delivery_status = "pending"
            if playback:
                self._drop_playback()  # a new delivery replaces whatever was still playing
                self._playback[delivery_id] = {"path": playback, "seconds": duration - PLAYBACK_PAD_S, **({"standby": True} if say else {})}
            session.staged_at = self.timer()
            session.latency_timings["response_staged"] = max(0, (session.staged_at - session.latency_start) * 1000)
            session.delivery_status = "pending"
            self.log_latency(session, result.request_id, "staged")
            log_event("OUTPUT_STAGED", session=result.session_id, request=result.request_id,
                      delivery=delivery_id, npc=result.npc_id, mode=result.mode, text=result.text,
                      audio=bool(raw), delivered=False)
            session.inputs[result.request_id] = "staged"
            session.request_id = None
            self._stage(session, result.request_id, "staging_ms", staging_began)
            if record is not None:
                record["staged_at"] = self.clock()
            return {"delivery_id": delivery_id, "status": "pending", "audio": bool(raw)}
        except BaseException:
            if session.request_id == result.request_id:
                session.inputs[result.request_id] = "pending"
            raise

    def log_latency(self, session, request_id, status):
        if session.latency_request == request_id:
            log_event("TURN_LATENCY", session=session.event.session_id, request=request_id,
                      origin=session.latency_origin, status=status, stage_ms=session.latency_timings,
                      elapsed_ms=max(0, (self.timer() - session.latency_start) * 1000),
                      tts=session.tts_outcome)

    def command(self, session_id):
        session = self.current(session_id)
        command = session.delivery
        if not command or session.delivery_status in {"completed", "interrupted", "failed"}:
            return {"kind": "idle"}
        if command["kind"] == "stop":
            return command
        if session.delivery_status == "started":
            return {"kind": "idle"}
        if self.clock() * 1000 >= command["expires_at_ms"]:
            # A lost start ack is ambiguous: ask receiver to stop, never assume success.
            session.delivery = {**command, "kind": "stop"}
            return session.delivery
        return command

    async def listen(self, session):
        async with self._mic_gate:
            if self.session is not session:
                return
            # F8 is an explicit interruption: game-side already stops current audio.
            self._cancel_opening(session)
            self.invalidate(session)
            await asyncio.to_thread(self.microphone.start)
        await asyncio.sleep(15)
        await self.finish_listening(session)

    async def finish_listening(self, session):
        speech_end = self.timer()
        async with self._mic_gate:
            if not self.microphone.active:
                return
            raw = await asyncio.to_thread(self.microphone.stop)
        if self._microphone_task and self._microphone_task is not asyncio.current_task():
            self._microphone_task.cancel()
        if self.session is not session:
            return
        wav_duration(raw)
        input_id = uuid.uuid4().hex
        try:
            async with self.client.stream("POST", self.settings.stt_url, content=raw,
                                          headers={"Content-Type": "audio/wav"}) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 16384:
                        raise ConnectionError("STT response too large")
                parsed = json.loads(body)
            utterance = SpeechInput(session_id=session.event.session_id, input_id=input_id,
                                    text=parsed["text"], confidence=parsed.get("confidence", 1), source="stt")
        except BaseException:
            log_event("STT_LATENCY", session=session.event.session_id, request=input_id,
                      status="failed_or_cancelled", elapsed_ms=max(0, (self.timer() - speech_end) * 1000))
            raise
        log_event("STT_LATENCY", session=session.event.session_id, request=input_id,
                  status="ready", elapsed_ms=max(0, (self.timer() - speech_end) * 1000))
        await self.player_input(utterance, interrupt=True, speech_end=speech_end,
                                stt_ms=max(0, (self.timer() - speech_end) * 1000))
