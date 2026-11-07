"""Versioned game/bridge contracts. Raw Skyrim observations stay at this boundary."""

from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

import hashlib
import re

from pydantic import Field, StrictBool, StrictInt, field_validator

from .personas import build_scene
from npc.contracts.context import (
    AdapterContext, Contract, ContextScenario, Identifier, ObservedFact, Persona, PlayerInput,
    Scope, StateSnapshot, TurnInput, NpcState,
)

Text = Annotated[str, Field(strict=True, max_length=2000)]
HexID = Annotated[str, Field(strict=True, pattern=r"^[0-9A-Fa-f]{8}$")]


class Form(Contract):
    form_id: HexID
    local_id: HexID
    plugin: Annotated[str, Field(strict=True, max_length=128)] = ""
    name: Annotated[str, Field(strict=True, max_length=512)] = ""

    @property
    def definition_key(self):
        return f"{self.plugin.lower()}:{self.local_id.lower()}"


class ActorData(Contract):
    reference: Form
    base: Form
    race: Form | None = None
    voice: Form | None = None
    sex: StrictInt | None = None
    in_combat: bool
    dead: bool
    weapon_drawn: bool
    sees_player: bool | None = None
    hostile_to_player: bool | None = None
    factions: Annotated[tuple[Form, ...], Field(max_length=64)] = ()
    equipment: Annotated[tuple[Form, ...], Field(max_length=2)] = ()


class DialogueOption(Contract):
    topic: Form
    info: Form | None = None
    player_text: Text
    responses: Annotated[tuple[Text, ...], Field(max_length=32)] = ()
    quest: Form | None = None
    quest_stage: StrictInt | None = None
    provenance: Literal["engine_dialogue_menu"] = "engine_dialogue_menu"


class GameSnapshot(Contract):
    npc: ActorData
    player: ActorData
    location: Form | None = None
    cell: Form | None = None
    interior: bool
    hour: float = Field(ge=0, lt=24, allow_inf_nan=False)
    game_days: float = Field(ge=0, allow_inf_nan=False)
    weather: Form | None = None
    weather_label: Annotated[str, Field(strict=True, max_length=128)] | None = None
    dialogue: Annotated[tuple[DialogueOption, ...], Field(max_length=64)] = ()
    dialogue_complete: bool = False


class GameEvent(Contract):
    schema_version: Literal["0.1"] = "0.1"
    event_id: Identifier
    session_id: Identifier
    game_epoch: Identifier
    sequence: Annotated[StrictInt, Field(ge=0)]
    state_revision: Annotated[StrictInt, Field(ge=0)] = 0
    captured_at_ms: Annotated[StrictInt, Field(ge=0)]
    game_pid: Annotated[StrictInt, Field(gt=0)]
    kind: Literal["start", "snapshot", "end", "listen_start", "listen_stop", "delivery", "player_text"]
    # Plugin >= 0.5.3 on "start": True when Anima began the session itself (no vanilla menu). Only those get an opening line.
    direct: StrictBool = False
    snapshot: GameSnapshot | None = None
    text: Annotated[str, Field(strict=True, min_length=1, max_length=4000)] | None = None
    delivery_id: Identifier | None = None
    status: Literal["started", "completed", "interrupted", "failed"] | None = None
    # Optional receipt detail from plugin >= 0.4.0; older plugins omit both.
    # reason: why a delivery stopped (barge_in, menu_closed, combat, actor_lost, state_changed,
    # superseded, bridge_stop, audio_failed, end). Kept open-ended so a newer plugin never loses a receipt.
    reason: Annotated[str, Field(strict=True, pattern=r"^[a-z_]{1,32}$")] | None = None
    # audio: full | ended_early | failed | none. Absent when the audio outcome is unknown (interruptions).
    audio: Literal["full", "ended_early", "failed", "none"] | None = None


class SpeechInput(Contract):
    session_id: Identifier
    input_id: Identifier
    text: Annotated[str, Field(strict=True, min_length=1, max_length=4000)]
    source: Literal["text", "stt"] = "text"
    confidence: float = Field(default=1, ge=0, le=1, allow_inf_nan=False)


class PipelineResult(Contract):
    session_id: Identifier
    request_id: Identifier
    npc_id: Identifier
    mode: Literal["speech", "silence", "end"] = "speech"
    text: Text = ""
    # Trusted local pipeline artifact, constrained to the configured audio root.
    audio_path: Annotated[str, Field(strict=True, max_length=1024)] | None = None
    # Optional conversation details; pipelines that omit them still work (turn type comes from begin_turn).
    move: Identifier | None = None
    evidence_ids: Annotated[tuple[Identifier, ...], Field(max_length=16)] = ()


class NpcProfile(Contract):
    persona: Persona
    # Explicitly authored knowledge bindings; game mechanics do not imply knowledge.
    knowledge_sources: Annotated[tuple[Text, ...], Field(max_length=16)] = ()


def memory_branch(scope, event):
    """Save-branch key for social memory. `character` is stable across launches and separates player names."""
    if scope == "session":
        return event.game_epoch
    player = event.snapshot.player if event.snapshot else None
    name = " ".join(((player.reference.name or player.base.name) if player else "").split()).casefold()
    if not name:
        return event.game_epoch  # unknown character: never merge it into someone else's memory
    slug = re.sub(r"[^a-z0-9]+", "-", name).strip("-")[:48] or "player"
    return f"player-{slug}-{hashlib.sha256(name.encode()).hexdigest()[:10]}"


class Settings(Contract):
    game_directory: str
    output_directory: str = "output/skyrim"
    profiles_file: str | None = None
    # Default characters for NPCs without an authored profile; a missing file keeps the plain "unknown" persona.
    role_rules_file: str | None = "configs/skyrim/role_rules.json"
    # Deliver an authored greeting when a conversation opens and the player has not spoken (no model call).
    opening_lines: bool = False
    # character: the NPC remembers a player character across game launches (key from the player's name).
    # session: memory is private to one game launch (the old behaviour).
    memory_scope: Literal["character", "session"] = "character"
    opening_delay_s: float = Field(default=1.2, ge=0, le=10)
    world_id: Identifier = "local-skyrim"
    pipeline_mode: Literal["inbox", "http", "preview"] = "inbox"
    pipeline_url: str | None = None
    tts_url: str | None = None
    stt_url: str | None = None
    # system: the bridge plays TTS audio through Windows and sends the plugin a subtitle-only command.
    # engine: the WAV is staged in the game folder and played by the plugin's engine sound handle.
    # say: like Mantella, the bridge writes voice + lip files under Data/Sound/Voice/Anima.esp and the plugin
    # makes the NPC say an Anima.esp topic (real mouth animation, positional audio). Needs a live game to prove.
    audio_output: Literal["system", "engine", "say"] = "system"
    # Folder with FaceFXWrapper.exe and FonixData.cdf for `say` lip files. Empty: no lip file is written.
    facefx_directory: str = ""
    # Optional Mantella-format skyrim_characters.csv (read in place): picks the Piper voice per character.
    voice_table: str | None = None
    # Authored voice per NPC: key is "plugin:localid" of the base form or the exact NPC name, value a Piper model.
    voice_overrides: dict[str, str] = {}
    # Folder with piper.exe and models/skyrim/low/*.onnx. Only read from where the owner points it; nothing is copied.
    piper_directory: str = "assets/private/npc_lab/piper"
    cv_url: str | None = None
    cv_timeout_s: float = Field(default=8, ge=1, le=15)
    visual_ttl_s: float = Field(default=15, ge=1, le=60)
    attach_visual_reference: bool = False
    rag_config: str | None = None
    provider_profile: str = "configs/providers/offline.yaml"
    audio_root: str = "output/pipeline/audio"
    state_db: str | None = "output/skyrim/social.sqlite3"
    port: int = Field(default=8768, ge=1024, le=65535)
    timeout_s: float = Field(default=30, ge=1, le=60)
    # Real-time profile: a model call slower than this is abandoned and retried once (transient failures only).
    model_cutoff_s: float = Field(default=4, ge=0.5, le=30)
    tts_timeout_s: float = Field(default=4, ge=0.1, le=20)
    capture_screenshots: bool = True
    max_screenshots: int = Field(default=100, ge=1, le=1000)
    max_output_bytes: int = Field(default=250 * 1024 * 1024, ge=1024, le=2 * 1024**3)

    @field_validator("cv_url")
    @classmethod
    def cv_origin(cls, value):
        from npc.vision.client import validate_cv_url
        return validate_cv_url(value) if value is not None else None

    @field_validator("pipeline_url", "tts_url", "stt_url")
    @classmethod
    def local_endpoint(cls, value):
        if value is None:
            return value
        parsed = urlsplit(value)
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.fragment or parsed.query):
            raise ValueError("connector endpoints must be credential-free loopback HTTP URLs")
        return value


def npc_id(snapshot: GameSnapshot) -> str:
    return "ref:" + snapshot.npc.reference.form_id.lower()


def profile_for(snapshot: GameSnapshot, profiles: dict[str, NpcProfile], rules: dict | None = None) -> NpcProfile:
    profile = profiles.get(snapshot.npc.base.definition_key)
    if profile:
        return profile  # authored profiles always win
    if rules:
        from .personas import derive_persona
        return NpcProfile(persona=derive_persona(snapshot, rules))
    return NpcProfile(persona=Persona(
        name=snapshot.npc.reference.name or snapshot.npc.base.name or "Unknown NPC",
        occupation="unknown", social_role="unknown", speech_style=(),
        boundaries=("Background and private knowledge are unknown unless explicitly supplied.",),
    ))


def canonical_scenario(event: GameEvent, speech: SpeechInput, profile: NpcProfile,
                       world_id: str, revision: int, *, now: datetime | None = None, social_state=None,
                       screenshot=None, image_description=None) -> ContextScenario:
    """Canonical subset plus complete adapter observations for pipeline ingress."""
    data = event.snapshot
    if data is None:
        raise ValueError("snapshot required")
    now = now or datetime.now(timezone.utc)
    npc, player = npc_id(data), "ref:" + data.player.reference.form_id.lower()
    scope = Scope(game_id="skyrim", world_id=world_id, save_branch_id=event.game_epoch)
    facts = []

    observed_at = datetime.fromtimestamp(event.captured_at_ms / 1000, timezone.utc)

    def add(path, value, subject=None):
        facts.append(ObservedFact(
            id=f"{speech.input_id}:{len(facts)}", path=path, value=value, source="anima-skse",
            source_kind="telemetry", subject_id=subject, captured_at=observed_at,
            simulation_time_s=float(data.game_days * 86400), ttl_ms=5000,
            confidence=1.0, perceivers=(npc,),
        ))

    add("npc.in_combat", data.npc.in_combat, npc)
    if data.npc.sees_player:
        add("player.weapon_drawn", data.player.weapon_drawn, player)
    if data.location and data.location.name:
        add("world.location", data.location.name)
    add("world.hour", data.hour)
    if not data.interior and data.weather_label:
        add("world.weather", data.weather_label)
    turn = TurnInput(scope=scope, request_id=speech.input_id, session_id=event.session_id,
                     npc_id=npc, player_id=player, sequence=event.sequence,
                     expected_dialogue_revision=revision, captured_at=now,
                     simulation_time_s=float(data.game_days * 86400),
                     player_input=PlayerInput(text=speech.text, source=speech.source,
                                              confidence=speech.confidence), facts=tuple(facts),
                     scene=build_scene(data))
    state = StateSnapshot(scope=scope, npc_id=npc, player_id=player,
                          storage_revision=social_state["revision"] if social_state else revision,
                          dialogue_revision=revision, turn_epoch=revision,
                          persona=profile.persona,
                          **({"relationship": social_state["relationship"], "recent_dialogue": social_state["history"],
                              "conversation": social_state["conversation"], "reading": social_state.get("reading"),
                              "npc_state": NpcState(observed_at=now, ttl_ms=60000, mood=social_state["mood"])}
                             if social_state else {}))
    return ContextScenario(now=now, turn=turn, snapshot=state,
                           adapter_context=AdapterContext(source="anima-skse", payload={
                               "game_event": event.model_dump(mode="json"),
                               "npc_profile": profile.model_dump(mode="json"),
                               "screenshot": screenshot,
                               "image_description": image_description,
                           }))
