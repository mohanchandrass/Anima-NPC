#include "delivery.h"
#include <iostream>

struct Topics {
    bool menuOpen = true;
    int speaker = 7;
    const void* currentTopicInfo = nullptr;
    bool IsCurrentSpeaker(const int& handle) const { return menuOpen && speaker == handle; }
};

int main()
{
    using anima::Decision;
    anima::Delivery request{"session", "npc", "0123456789abcdef0123456789abcdef", 4, 2000, 20};
    anima::Receiver receiver{"session", "npc", 4, 1000, true, false, false};
    int failures = 0;
    auto check = [&](Decision expected, const char* label) {
        if (anima::Evaluate(request, receiver) != expected) { std::cerr << label << '\n'; ++failures; }
    };
    check(Decision::play, "valid delivery");
    receiver.duplicate = true; check(Decision::ignore, "duplicate must never replay"); receiver.duplicate = false;
    receiver.vanillaTalking = true; check(Decision::wait, "wait for authored speech"); receiver.vanillaTalking = false;
    receiver.revision = 5; check(Decision::reject, "changed state"); receiver.revision = 4;
    receiver.now = 2000; check(Decision::reject, "start expiry boundary"); receiver.now = 1000;
    receiver.npc = "other"; check(Decision::reject, "wrong NPC"); receiver.npc = "npc";
    receiver.session = "other"; check(Decision::ignore, "old session"); receiver.session = "session";
    receiver.actorReady = false; check(Decision::reject, "combat or missing actor"); receiver.actorReady = true;
    // Exercise the same engine-boundary mapping used by the native command receiver.
    Topics topics;
    int line = 1;
    auto dialogue = [&](Decision expected, const char* label) {
        if (anima::EvaluateDialogue(request, receiver, &topics, 7) != expected) {
            std::cerr << label << '\n'; ++failures;
        }
    };
    topics.currentTopicInfo = &line;
    dialogue(Decision::wait, "active vanilla line must keep the command pending");
    dialogue(Decision::wait, "repeated polls during vanilla speech must not start playback");
    topics.currentTopicInfo = nullptr;
    dialogue(Decision::play, "same command may start after vanilla speech finishes");
    topics.currentTopicInfo = &line;
    receiver.now = 2000;
    dialogue(Decision::reject, "expiry still wins while vanilla speech is active");
    topics.currentTopicInfo = nullptr;
    dialogue(Decision::reject, "finishing vanilla speech cannot revive expired output");
    receiver.now = 1000;
    topics.speaker = 8;
    dialogue(Decision::reject, "speaker changed before the next snapshot");
    topics.speaker = 7; topics.menuOpen = false;
    dialogue(Decision::reject, "closed engine menu must not start speech");
    if (anima::EvaluateDialogue(request, receiver, static_cast<Topics*>(nullptr), 7) != Decision::reject) {
        std::cerr << "missing dialogue manager must fail closed\n"; ++failures;
    }
    // Direct (menu-less) sessions: no menu speaker is required, the actor's own speech decides.
    topics.menuOpen = false; topics.currentTopicInfo = nullptr;
    auto direct = [&](Decision expected, const char* label) {
        if (anima::EvaluateDialogue(request, receiver, &topics, 7, false) != expected) {
            std::cerr << label << '\n'; ++failures;
        }
    };
    direct(Decision::play, "direct session without a dialogue menu must deliver");
    if (anima::EvaluateDialogue(request, receiver, static_cast<Topics*>(nullptr), 7, false) != Decision::play) {
        std::cerr << "direct session needs no dialogue manager\n"; ++failures;
    }
    topics.menuOpen = true; topics.speaker = 8;
    direct(Decision::play, "direct session ignores the menu speaker");
    topics.menuOpen = false; topics.speaker = 7;
    receiver.vanillaTalking = true; direct(Decision::wait, "direct session waits for the actor's vanilla line"); receiver.vanillaTalking = false;
    receiver.now = 2000; direct(Decision::reject, "direct expired delivery"); receiver.now = 1000;
    receiver.npc = "other"; direct(Decision::reject, "direct changed speaker"); receiver.npc = "npc";
    receiver.revision = 5; direct(Decision::reject, "direct changed state"); receiver.revision = 4;
    receiver.actorReady = false; direct(Decision::reject, "direct combat or death"); receiver.actorReady = true;
    receiver.duplicate = true; direct(Decision::ignore, "direct duplicate"); receiver.duplicate = false;
    // The default stays the menu rule.
    if (anima::EvaluateDialogue(request, receiver, &topics, 7) != Decision::reject) {
        std::cerr << "menu rule must remain the default\n"; ++failures;
    }
    // Menu session with the menu open still gets the vanilla-line rules.
    topics.menuOpen = true; topics.currentTopicInfo = &line;
    if (anima::EvaluateDialogue(request, receiver, &topics, 7, true) != Decision::wait) {
        std::cerr << "menu session must still wait for a vanilla line\n"; ++failures;
    }
    topics.currentTopicInfo = nullptr;
    request.id = "../../outside"; check(Decision::reject, "invalid delivery path ID");
    return failures ? 1 : 0;
}
