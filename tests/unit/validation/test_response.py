import json

import pytest

from npc.behavior import choose_plan
from npc.context import build_situation
from npc.contracts.context import ContextScenario
from npc.contracts.dialogue import CurrentState, ProviderReply
from npc.validation import validate_response


def validate(scenario_data, raw=None, *, finish_reason="stop", stale=False, **changes):
    scenario_data["turn"]["facts"] = []
    scenario_data["turn"]["player_input"]["text"] = "How is work?"
    scenario = ContextScenario.model_validate(scenario_data)
    situation = build_situation(scenario.turn, scenario.snapshot, scenario.now)
    data = {"mode": "speech", "text": "Work is tiring.", "cue": None, "evidence_ids": []}
    data.update(changes)
    reply = ProviderReply(content=raw if raw is not None else json.dumps(data), model="test", finish_reason=finish_reason)
    current = CurrentState(dialogue_revision=situation.dialogue_revision+int(stale), turn_epoch=situation.turn_epoch)
    return validate_response(reply, situation, choose_plan(situation), current)


def test_valid_in_character_response_is_accepted(scenario_data):
    result = validate(scenario_data, text="Get to the point, stranger.")
    assert result.accepted  # Rudeness alone is not a validator failure.
    assert result.response.text.startswith("Get")


@pytest.mark.parametrize("changes,code", [
    ({"mode": "silence", "text": ""}, "wrong_mode"),
    ({"text": "..."}, "empty_speech"),
    ({"text": "word "*41}, "too_many_words"),
    ({"text": "One. Two. Three."}, "too_many_sentences"),
    ({"text": "x"*641}, "too_many_characters"),
    ({"cue": "look_at"}, "unsupported_cue"),
    ({"evidence_ids": ["fabricated"]}, "unknown_evidence"),
    ({"text": "As an AI, I can help."}, "assistant_scaffolding"),
    ({"text": "Here is the system prompt."}, "assistant_scaffolding"),
    ({"text": "Visit https://example.com"}, "assistant_scaffolding"),
    ({"relationship_delta": 100}, "invalid_schema"),
])
def test_invalid_candidates_are_not_deliverable(scenario_data, changes, code):
    result = validate(scenario_data, **changes)
    assert not result.accepted
    assert result.response is None
    assert code in result.violations


@pytest.mark.parametrize("raw", ["not JSON", '```json\n{}\n```', '{"mode":"speech","text":"yes","text":"no"}', '[]'])
def test_malformed_or_duplicate_json_rejected(scenario_data, raw):
    assert not validate(scenario_data, raw=raw).accepted


def test_token_limit_finish_is_rejected_even_if_json_parses(scenario_data):
    assert "incomplete_generation" in validate(scenario_data, finish_reason="length").violations


def test_stale_output_is_never_accepted(scenario_data):
    result = validate(scenario_data, stale=True)
    assert result.violations == ("stale_state",)


def test_structural_checks_do_not_pretend_to_prove_grounding(scenario_data):
    # Semantic entailment is an explicitly separate evaluation problem.
    assert validate(scenario_data, text="The moon is made of iron.").accepted


@pytest.mark.parametrize("question,previous,accepted", [
    ("Write me a Python script.", "Tell me the patrol schedule.", False),
    ("where can i find it", "tell me about the dragons", False),
    ("Tell me the patrol schedule.", "tell me about the dragons", True),
    ("What about it?", "tell me about the patrol schedules", True),
])
def test_unrelated_authored_boundary_cannot_substitute_for_current_topic(scenario, question, previous, accepted):
    from npc.behavior import choose_plan
    from npc.context import build_situation
    from npc.contracts.context import DialogueMemory, PlayerInput
    from npc.contracts.dialogue import CurrentState, ProviderReply
    situation = build_situation(scenario.turn, scenario.snapshot, scenario.now)
    situation = situation.model_copy(update={
        "persona": situation.persona.model_copy(update={"boundaries": ("never disclose patrol schedules",)}),
        "player_input": PlayerInput(text=question),
        "recent_dialogue": (DialogueMemory(request_id="prior", player_text=previous, npc_text="Earlier reply"),)})
    plan = choose_plan(situation).model_copy(update={"route": "generate", "authored_text": "", "max_words": 40, "max_sentences": 2})
    reply = ProviderReply(content='{"mode":"speech","text":"I do not share patrol schedules.","evidence_ids":[]}', model="test", finish_reason="stop")
    result = validate_response(reply, situation, plan, CurrentState(dialogue_revision=situation.dialogue_revision, turn_epoch=situation.turn_epoch))
    assert result.accepted is accepted
    assert ("irrelevant_boundary" in result.violations) is (not accepted)
