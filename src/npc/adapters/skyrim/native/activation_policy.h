#pragma once
#include <cstdint>
#include <string>
namespace anima {
// Menu-less NPCs answer E with a static bark and open no DialogueMenu. When the player activates an actor and no
// menu opens within kActivationMenuWaitMs, the activation is treated as "no menu" and a direct session starts.
inline constexpr std::int64_t kActivationMenuWaitMs = 700;
inline constexpr std::int64_t kActivationTimeoutMs = 3000;  // give up when a pause or custom menu keeps blocking the start

// Should this TESActivateEvent be remembered at all? Evaluated when the event arrives.
inline bool TrackActivation(bool enabled, bool byPlayer, bool targetIsActor, bool targetIsPlayer, bool alreadyInSession, bool validTarget) {
    return enabled && byPlayer && targetIsActor && !targetIsPlayer && !alreadyInSession && validTarget;
}

enum class ActivationAction { wait, cancel, start };
// Evaluated every tick for a remembered activation.
// menuOpened: a DialogueMenu opened (or is open) since the activation. sessionActive: any session now exists.
// blocked: paused or custom menu open. validTarget: the actor still passes the F6 rules (alive, not hostile/combat, in range).
inline ActivationAction DecideActivation(std::int64_t ageMs, bool menuOpened, bool sessionActive, bool blocked, bool validTarget) {
    if (menuOpened || sessionActive || !validTarget) return ActivationAction::cancel;
    if (ageMs < kActivationMenuWaitMs) return ActivationAction::wait;
    if (blocked) return ageMs > kActivationTimeoutMs ? ActivationAction::cancel : ActivationAction::wait;
    return ActivationAction::start;
}
inline const char* ActivationReason(bool menuOpened, bool sessionActive, bool validTarget) {
    if (menuOpened) return "dialogue_menu_opened";
    if (sessionActive) return "session_active";
    if (!validTarget) return "invalid_target";
    return "timeout";
}

// Who started a session decides whether the text box opens with it. Only an explicit action opens the box.
enum class StartSource { activation, chat_key, talk_line, push_to_talk };
inline bool OpensTextBoxOnStart(StartSource source) {
    return source == StartSource::chat_key || source == StartSource::talk_line;
}
// Hint shown once when an activation starts a session (no text box).
inline std::string KeyLabel(unsigned code) {
    static const char* const top = "QWERTYUIOP";   // DirectInput 16..25
    static const char* const home = "ASDFGHJKL";   // 30..38
    static const char* const bottom = "ZXCVBNM";   // 44..50
    if (code >= 16 && code <= 25) return std::string(1, top[code - 16]);
    if (code >= 30 && code <= 38) return std::string(1, home[code - 30]);
    if (code >= 44 && code <= 50) return std::string(1, bottom[code - 44]);
    if (code >= 59 && code <= 68) return "F" + std::to_string(code - 58);
    if (code == 87 || code == 88) return "F" + std::to_string(code - 76);
    return "key " + std::to_string(code);
}
inline std::string ActivationHint(unsigned chatKey, unsigned pttKey, unsigned menuKey) {
    return "ANIMA: press " + KeyLabel(chatKey) + " to type, hold " + KeyLabel(pttKey) + " to talk, " + KeyLabel(menuKey) + " for options";
}
}
