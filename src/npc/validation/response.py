import json
import re

from pydantic import ValidationError

from npc.contracts.context import Situation
from npc.context.conversation import content, tokens
from npc.context.focus import resolve_question
from npc.contracts.dialogue import CurrentState, InteractionPlan, ProviderReply, ResponseBody, ValidationResult, ViolationCode


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _boundary_topic_words(text):
    # High-precision overlap check, not a general semantic relevance classifier.
    control = {"never", "always", "do", "not", "don", "t", "must", "should", "disclose", "share", "reveal", "give", "out", "without", "the", "a", "an", "to", "of", "and", "or"}
    words = set(re.findall(r"[a-z]+", text.casefold())) - control
    return {word[:-1] if len(word) > 3 and word.endswith("s") else word for word in words}


REPLY_SIMILARITY = 0.7  # token Jaccard (all words) against a recent NPC reply
OPENING_WORDS = 6  # shared opening words that count as a repeated reply
MIN_COMPARE_WORDS = 6  # shorter replies (refusals, acknowledgements) skip the similarity check
COMPARE_LAST = 2
COMMON_CAPITALS = frozenset("""i aye ah oh well yes no now look listen hmm right fine please sorry thanks welcome greetings hello hey mind keep watch
gods god by if but and so then perhaps maybe sir madam lord lady friend stranger traveller traveler""".split())


def _recent_replies(situation):
    return [m.npc_text for m in situation.recent_dialogue if m.npc_text] + list(situation.conversation.replies)


def repeated_reply(text, situation):
    words = tokens(text)
    for old in list(dict.fromkeys(_recent_replies(situation)))[-COMPARE_LAST:]:
        before = tokens(old)
        if len(words) >= MIN_COMPARE_WORDS and len(before) >= MIN_COMPARE_WORDS and _jaccard(words, before) >= REPLY_SIMILARITY:
            return True
        if len(words) >= OPENING_WORDS and len(before) >= OPENING_WORDS and words[:OPENING_WORDS] == before[:OPENING_WORDS]:
            return True
    return False


def _jaccard(a, b):
    x, y = set(a), set(b)
    return len(x & y) / len(x | y) if x | y else 0.0


def _name_pool(situation, evidence):
    scene = [f"{item.label} {item.value}" for item in situation.scene]
    persona = situation.persona
    sources = [situation.player_input.text, persona.name, persona.occupation, persona.social_role, *persona.speech_style,
               *persona.goals, *persona.boundaries, situation.current_goal or "", persona.location or "", *persona.common_knowledge, *persona.services,
               *(m.player_text + " " + m.npc_text for m in situation.recent_dialogue), *situation.conversation.claims,
               *situation.conversation.replies, *(hit.text for hit in evidence),
               *(str(f.value) for f in situation.perceived_facts), *scene]
    if situation.visual_context is not None:
        sources += [str(situation.visual_context.prompt_payload())]
    return {w for text in sources for w in tokens(text)}


def unsupported_names(text, situation, evidence=(), allowlist=()):
    """Capitalised words in mid-sentence that appear nowhere in the supplied context.

    `allowlist` is game/config vocabulary (world terms the NPC may name freely); core holds none.
    """
    allowed = _name_pool(situation, evidence) | COMMON_CAPITALS | {w for term in allowlist for w in tokens(term)}
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = re.findall(r"[A-Za-z][A-Za-z'’\-]*", sentence)
        for word in words[1:]:
            base = re.sub(r"['’]s$", "", word)
            if base[:1].isupper() and len(base) > 1 and not base.isupper() and base.casefold() not in allowed:
                found.append(base)
    return found


def validate_response(reply: ProviderReply, situation: Situation, plan: InteractionPlan, current: CurrentState, *, evidence=(), evidence_aliases=None,
                      check_conversation=True, name_allowlist=()) -> ValidationResult:
    if (current.dialogue_revision, current.turn_epoch) != (situation.dialogue_revision, situation.turn_epoch):
        return ValidationResult(accepted=False, violations=("stale_state",))
    violations: list[ViolationCode] = []
    if reply.finish_reason != "stop":
        violations.append("incomplete_generation")
    try:
        data = json.loads(reply.content, object_pairs_hook=_unique_object)
    except (ValueError, RecursionError):
        return ValidationResult(accepted=False, violations=tuple(violations + ["invalid_json"]))
    try:
        response = ResponseBody.model_validate(data)
    except ValidationError:
        return ValidationResult(accepted=False, violations=tuple(violations + ["invalid_schema"]))
    if response.mode not in plan.allowed_modes:
        violations.append("wrong_mode")
    if response.mode == "speech":
        words = re.findall(r"\b\w+(?:['’\-]\w+)*\b", response.text, flags=re.UNICODE)
        if not words:
            violations.append("empty_speech")
        if len(words) > plan.max_words:
            violations.append("too_many_words")
        sentences = [part for part in re.split(r"[.!?]+", response.text) if part.strip()]
        if len(sentences) > plan.max_sentences:
            violations.append("too_many_sentences")
        if len(response.text) > max(32, plan.max_words * 16):
            violations.append("too_many_characters")
    elif response.text.strip():
        violations.append("unexpected_text")
    if response.cue is not None and response.cue not in plan.allowed_cues:
        violations.append("unsupported_cue")
    if response.mode == "speech" and plan.route == "generate":
        request_words = _boundary_topic_words(resolve_question(situation))
        answer_words = _boundary_topic_words(response.text)
        for boundary in situation.persona.boundaries:
            topic = _boundary_topic_words(boundary)
            if len(topic) >= 2 and topic <= answer_words and not topic.intersection(request_words):
                violations.append("irrelevant_boundary")
                break
    if check_conversation and response.mode == "speech" and plan.route == "generate":
        if repeated_reply(response.text, situation):
            violations.append("repeated_reply")
        if plan.move == "expand":
            known = set().union(*(content(t) for t in _recent_replies(situation)), content(situation.player_input.text))
            if len(content(response.text) - known) < 2:
                violations.append("move_not_made")
        if unsupported_names(response.text, situation, evidence, name_allowlist):
            violations.append("unsupported_name")
    allowed_ids = {fact.id for fact in situation.perceived_facts} | {hit.id for hit in evidence}
    aliases = evidence_aliases or {}
    resolved_ids = tuple(aliases.get(evidence_id, evidence_id) for evidence_id in response.evidence_ids)
    if any(evidence_id not in allowed_ids for evidence_id in resolved_ids):
        violations.append("unknown_evidence")
    if re.search(r"\b(as an ai|language model|system prompt|developer instructions)\b|https?://", response.text, re.I):
        violations.append("assistant_scaffolding")
    return ValidationResult(accepted=not violations, violations=tuple(violations), response=response.model_copy(update={"evidence_ids": resolved_ids}) if not violations else None)
