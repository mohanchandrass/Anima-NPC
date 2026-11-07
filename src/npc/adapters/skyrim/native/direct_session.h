#pragma once
#include <cstdint>
namespace anima {
// A "direct" session talks to an actor picked with the crosshair, with no vanilla DialogueMenu open.
inline constexpr float kDirectMaxDistance = 600.0F;
inline constexpr std::int64_t kDirectIdleMs = 120000;  // the owner may wait to type; any key press also restarts the idle timer

// Living, non-hostile, non-player actor that is not in combat and has a base record.
inline bool ValidDirectTarget(bool isActor, bool isPlayer, bool hasBase, bool dead, bool hostile, bool inCombat) {
    return isActor && !isPlayer && hasBase && !dead && !hostile && !inCombat;
}

enum class DirectEnd { none, distance, combat, dead, cell_changed, idle };
// Order: dead, combat, cell change, distance, idle. Idle time only counts while nothing is pending
// (callers pass 0 while a delivery plays or the text box is open).
inline DirectEnd DecideDirectEnd(float distance, bool inCombat, bool dead, bool sameCell, std::int64_t idleMs) {
    if (dead) return DirectEnd::dead;
    if (inCombat) return DirectEnd::combat;
    if (!sameCell) return DirectEnd::cell_changed;
    if (distance > kDirectMaxDistance) return DirectEnd::distance;
    if (idleMs > kDirectIdleMs) return DirectEnd::idle;
    return DirectEnd::none;
}
inline const char* DirectEndName(DirectEnd reason) {
    switch (reason) {
    case DirectEnd::distance: return "too_far";
    case DirectEnd::combat: return "combat";
    case DirectEnd::dead: return "dead";
    case DirectEnd::cell_changed: return "cell_changed";
    case DirectEnd::idle: return "idle";
    default: return "none";
    }
}
// A vanilla menu session or a direct session counts as an active conversation.
inline bool SessionActive(bool menuOpen, bool direct) { return menuOpen || direct; }
// Holding F8 with no session starts a direct session first only when a valid target is under the crosshair.
inline bool PttStartsDirect(bool keyDown, bool hasSession, bool menuOpen, bool validTarget) {
    return keyDown && !hasSession && !menuOpen && validTarget;
}
}
