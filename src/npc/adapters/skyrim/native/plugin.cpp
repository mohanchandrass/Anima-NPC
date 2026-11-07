// Independently authored. No Mantella source or binary dependency.
#include <RE/Skyrim.h>
#include <SKSE/SKSE.h>
#include <Windows.h>
#ifdef GetObject
#undef GetObject
#endif
#include <winhttp.h>
#include <nlohmann/json.hpp>
#include <spdlog/sinks/basic_file_sink.h>
#include <atomic>
#include <chrono>
#include <deque>
#include <filesystem>
#include <format>
#include <cmath>
#include <mutex>
#include <thread>
#include <unordered_set>
#include "delivery.h"
#include "runtime_support.h"
#include "version_metadata.h"
#include "ui_state.h"
#include "audio_state.h"
#include "speech_policy.h"
#include "launch_policy.h"
#include "direct_session.h"
#include "conversation_request.h"
#include "activation_policy.h"
#include "caption_text.h"

using json = nlohmann::json;
using Clock = std::chrono::steady_clock;
namespace {
std::int64_t UtcMs()
{
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
}
std::string Text(const char* value, std::size_t limit = 500)
{
    std::string text = value ? value : "";
    if (text.size() > limit) {
        auto end = limit;
        while (end && (static_cast<unsigned char>(text[end]) & 0xC0) == 0x80) --end;
        text.resize(end);
    }
    return text;
}
json Form(RE::TESForm* form)
{
    if (!form) return nullptr;
    auto file = form->GetFile(0);
    return {{"form_id", std::format("{:08X}", form->GetFormID())},
            {"local_id", std::format("{:08X}", file ? form->GetLocalFormID() : form->GetFormID())},
            {"plugin", file ? Text(file->fileName, 128) : ""}, {"name", Text(form->GetName())}};
}
json Actor(RE::Actor* actor, RE::Actor* player)
{
    auto base = actor->GetActorBase();
    json factions = json::array(), equipment = json::array();
    if (base) for (const auto& faction : base->factions)
        if (faction.faction && factions.size() < 64) factions.push_back(Form(faction.faction));
    for (bool left : {false, true})
        if (auto item = actor->GetEquippedObject(left)) equipment.push_back(Form(item));
    bool ignored = false;
    return {{"reference", Form(actor)}, {"base", Form(base)}, {"race", Form(actor->GetRace())},
            {"voice", Form(base ? base->voiceType : nullptr)},
            {"sex", base ? json(static_cast<int>(base->GetSex())) : json(nullptr)},
            {"in_combat", actor->IsInCombat()}, {"dead", actor->IsDead()}, {"weapon_drawn", actor->AsActorState()->IsWeaponDrawn()},
            {"sees_player", actor == player || actor->HasLineOfSight(player, ignored)},
            {"hostile_to_player", actor != player && actor->IsHostileToActor(player)},
            {"factions", factions}, {"equipment", equipment}};
}
class HttpHandle {
public:
    HINTERNET handle;
    explicit HttpHandle(HINTERNET value) : handle(value) {}
    ~HttpHandle() { if (handle) WinHttpCloseHandle(handle); }
    HttpHandle(const HttpHandle&) = delete;
};
struct HttpError : std::runtime_error {
    DWORD status;
    bool resync;
    HttpError(DWORD value, bool reset) : std::runtime_error(std::format("bridge HTTP {}", value)), status(value), resync(reset) {}
};
class DialogueLock {
    CRITICAL_SECTION* section;
public:
    explicit DialogueLock(RE::MenuTopicManager* manager) : section(reinterpret_cast<CRITICAL_SECTION*>(&manager->criticalSection)) { EnterCriticalSection(section); }
    ~DialogueLock() { LeaveCriticalSection(section); }
};

void BuildVoice(RE::BSSoundHandle& sound, const char* path)
{
    RE::BSResource::ID resource{};
    resource.GenerateFromPath(path);
    RE::BSAudioManager::GetSingleton()->GetSoundHandleByFile(sound, resource, 0x1A, 128);
    if (!sound.IsValid()) return;
    sound.SetVolume(1.0F);
    if (auto defaults = RE::BGSDefaultObjectManager::GetSingleton())
        if (auto output = defaults->GetObject<RE::BGSSoundOutput>(RE::DEFAULT_OBJECT::kDialogueOutputModel3D))
            sound.SetOutputModel(output);
}

class Connector final : public RE::BSTEventSink<RE::MenuOpenCloseEvent>, public RE::BSTEventSink<RE::InputEvent*>, public RE::BSTEventSink<SKSE::ModCallbackEvent>, public RE::BSTEventSink<RE::TESActivateEvent> {
public:
    static Connector& Get() { static auto* instance = new Connector; return *instance; }
    void OnConversationLine() { RequestConversation(); }
    // Diagnostic only: is the Start Game Enabled quest behind the "Talk with me." line running? A line of a quest that
    // is not running is never shown. Anima.esp quest local ID 0x800 (see scripts/build_anima_esp.py).
    void LogQuestState(const char* when)
    {
        auto data = RE::TESDataHandler::GetSingleton();
        auto quest = data ? data->LookupForm<RE::TESQuest>(0x800, "Anima.esp") : nullptr;
        if (!quest) { SKSE::log::warn("ANIMA quest check ({}): AnimaDialogue not found (is Anima.esp enabled?)", when); return; }
        SKSE::log::info("ANIMA quest check ({}): AnimaDialogue form={:08X} enabled={} running={}", when, quest->GetFormID(), quest->IsEnabled(), quest->IsRunning());
    }
    void Initialize()
    {
        auto path = std::filesystem::absolute("Data/SKSE/Plugins/AnimaConnector.ini").wstring();
        port = static_cast<INTERNET_PORT>(GetPrivateProfileIntW(L"Connection", L"Port", 8768, path.c_str()));
        pttKey = GetPrivateProfileIntW(L"Connection", L"PushToTalkKey", 66, path.c_str());
        menuKey = GetPrivateProfileIntW(L"Connection", L"ConversationMenuKey", 65, path.c_str());
        chatKey = GetPrivateProfileIntW(L"Connection", L"ChatKey", 35, path.c_str());
        SKSE::GetModCallbackEventSource()->AddEventSink(this);
        animateMouth = GetPrivateProfileIntW(L"Connection", L"AnimateMouth", 1, path.c_str()) != 0;
        suppressVanilla = GetPrivateProfileIntW(L"Connection", L"SuppressVanillaSpeech", 1, path.c_str()) != 0;
        autoStart = GetPrivateProfileIntW(L"Connection", L"AutoStartOnActivate", 1, path.c_str()) != 0;  // missing key -> default 1
        captionFallback = GetPrivateProfileIntW(L"Connection", L"CaptionFallback", 0, path.c_str()) != 0;  // missing key -> default 0
        engineSubtitle = GetPrivateProfileIntW(L"Connection", L"EngineSubtitle", 1, path.c_str()) != 0;  // missing key -> default 1
        ResetEpoch();
        StartConsole(path);
        RE::UI::GetSingleton()->AddEventSink<RE::MenuOpenCloseEvent>(this);
        if (auto input = RE::BSInputDeviceManager::GetSingleton()) input->AddEventSink(this);
        if (auto holder = RE::ScriptEventSourceHolder::GetSingleton()) holder->AddEventSink<RE::TESActivateEvent>(this);
        std::thread([this] { Worker(); }).detach();
        SKSE::log::info("AnimaConnector initialized on port {}; SuppressVanillaSpeech={} AutoStartOnActivate={} EngineSubtitle={} CaptionFallback={} ChatKey={}", port, suppressVanilla, autoStart, engineSubtitle, captionFallback, chatKey);
        // Diagnostics only, no dependency: other dialogue mods hooking subtitles/audio may explain odd playback.
        for (const char* module : {"MantellaDialogue.dll", "MantellaSubtitles.dll", "SKSE_HTTP.dll"})
            SKSE::log::info("ANIMA other dialogue module {}: {}", module, GetModuleHandleA(module) ? "loaded" : "not loaded");
    }
    // Opens AnimaConsole.exe (visible console showing all Anima logs) unless disabled or the bridge already runs.
    // Runs on a detached thread so a slow probe or CreateProcess never delays game load.
    void StartConsole(const std::wstring& iniPath)
    {
        const bool enabled = GetPrivateProfileIntW(L"Console", L"LaunchConsole", 0, iniPath.c_str()) != 0;
        if (!enabled) return;
        std::array<wchar_t, 4096> exe{};
        GetPrivateProfileStringW(L"Console", L"ConsoleExe", L"", exe.data(), static_cast<DWORD>(exe.size()), iniPath.c_str());
        const auto exePath = std::wstring(exe.data());
        const auto portValue = port;
        std::thread([exePath, portValue] {
            static std::atomic<bool> launched{false};
            std::error_code error;
            const bool configured = !exePath.empty(), exists = configured && std::filesystem::is_regular_file(exePath, error);
            const bool answering = configured && exists && BridgeAnswering(portValue);
            const auto decision = anima::DecideLaunch(true, configured, exists, launched.load(), answering);
            if (decision != anima::LaunchDecision::launch) {
                if (decision == anima::LaunchDecision::missing_exe || decision == anima::LaunchDecision::no_exe_configured)
                    SKSE::log::warn("ANIMA console: {}", anima::DescribeLaunch(decision));
                else SKSE::log::info("ANIMA console: {}", anima::DescribeLaunch(decision));
                return;
            }
            launched = true;
            auto commandLine = anima::ConsoleCommandLine(exePath, GetCurrentProcessId());
            STARTUPINFOW startup{}; startup.cb = sizeof(startup);
            PROCESS_INFORMATION process{};
            const auto directory = std::filesystem::path(exePath).parent_path().wstring();
            if (CreateProcessW(exePath.c_str(), commandLine.data(), nullptr, nullptr, FALSE, CREATE_NEW_CONSOLE, nullptr,
                               directory.c_str(), &startup, &process)) {
                CloseHandle(process.hThread); CloseHandle(process.hProcess);
                SKSE::log::info("ANIMA console: launched (pid {})", process.dwProcessId);
            } else SKSE::log::warn("ANIMA console: CreateProcess failed, error {}", GetLastError());
        }).detach();
    }
    static bool BridgeAnswering(INTERNET_PORT bridgePort)
    {
        HttpHandle http(WinHttpOpen(L"AnimaConnector/console", WINHTTP_ACCESS_TYPE_NO_PROXY, nullptr, nullptr, 0));
        if (!http.handle) return false;
        WinHttpSetTimeouts(http.handle, 250, 250, 250, 250);
        HttpHandle connection(WinHttpConnect(http.handle, L"127.0.0.1", bridgePort, 0));
        if (!connection.handle) return false;
        HttpHandle request(WinHttpOpenRequest(connection.handle, L"GET", L"/v1/health", nullptr, nullptr, nullptr, 0));
        if (!request.handle || !WinHttpSendRequest(request.handle, nullptr, 0, nullptr, 0, 0, 0) ||
            !WinHttpReceiveResponse(request.handle, nullptr)) return false;
        DWORD status = 0, size = sizeof(status);
        return WinHttpQueryHeaders(request.handle, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, nullptr, &status, &size, nullptr) && status == 200;
    }
    void ResetEpoch()
    {
        End(true, "state_changed");
        epoch = std::format("{}-{}-{}", GetCurrentProcessId(), UtcMs(), ++serial);
        open = false;
    }
    RE::BSEventNotifyControl ProcessEvent(const RE::MenuOpenCloseEvent* event, RE::BSTEventSource<RE::MenuOpenCloseEvent>*) override
    {
        if (event && event->menuName == RE::DialogueMenu::MENU_NAME) {
            open = event->opening;
            if (open) { direct = false; activationPending = false; activationMenuSeen = true; }  // a vanilla menu session takes over; menu_closed ends it as before
            openedAt = Clock::now();
            if (!open) End(true, "menu_closed");
        }
        return RE::BSEventNotifyControl::kContinue;
    }
    RE::BSEventNotifyControl ProcessEvent(RE::InputEvent* const* events, RE::BSTEventSource<RE::InputEvent*>*) override
    {
        if (!events) return RE::BSEventNotifyControl::kContinue;
        for (auto event = *events; event; event = event->next) {
            auto button = event->AsButtonEvent();
            if (!button || event->GetDevice() != RE::INPUT_DEVICE::kKeyboard) continue;
            if (button->IsDown() && !session.empty()) lastActivity = Clock::now();  // any key press restarts the idle timer
            if (button->idCode == chatKey) {
                const bool down = button->IsDown();
                const auto info = down ? Inspect() : TargetInfo{};
                const bool canStart = session.empty() && down && !MenuBlocksDirect() && info.valid;
                const bool customMenu = RE::UI::GetSingleton()->IsMenuOpen("CustomMenu");
                const auto action = anima::DecideChatKey(down, !session.empty(), customMenu, listening, canStart);
                if (down) LogPress("ChatKey", info, anima::ChatKeyName(action));
                switch (action) {
                case anima::ChatKeyAction::open_text: OpenUi(anima::Panel::text); break;
                case anima::ChatKeyAction::start_direct:
                    if (info.actor && StartDirect(info.actor) && anima::OpensTextBoxOnStart(anima::StartSource::chat_key)) OpenUi(anima::Panel::text);
                    break;
                case anima::ChatKeyAction::no_npc: Notify("ANIMA: talk to an NPC first."); break;
                default: break;
                }
                continue;
            }
            if (session.empty()) {
                if (button->idCode != pttKey) continue;
                const bool down = button->IsDown();
                const auto info = down ? Inspect() : TargetInfo{};
                const bool starts = anima::PttStartsDirect(down, false, MenuBlocksDirect(), info.valid);
                if (down) LogPress("F8", info, starts ? "start_direct" : "ignored_no_valid_target_or_blocked");
                if (!starts || !StartDirect(info.actor)) continue;
            }
            if (button->idCode == menuKey && button->IsDown()) { OpenUi(anima::Panel::options); continue; }
            if (button->idCode != pttKey || RE::UI::GetSingleton()->IsMenuOpen("CustomMenu")) continue;
            if (button->IsDown()) {
                if (!BargeIn()) continue; listening = true; Notify("ANIMA: listening while F8 is held..."); Queue(Event("listen_start"));
            } else if (button->IsUp() && listening) {
                listening = false; Notify("ANIMA: transcribing..."); Queue(Event("listen_stop"));
            }
        }
        return RE::BSEventNotifyControl::kContinue;
    }
    RE::BSEventNotifyControl ProcessEvent(const SKSE::ModCallbackEvent* event, RE::BSTEventSource<SKSE::ModCallbackEvent>*) override
    {
        if (!event) return RE::BSEventNotifyControl::kContinue;
        const std::string name = event->eventName.c_str(), text = event->strArg.c_str();
        if ((name.starts_with("UIListMenu_") || name.starts_with("UITextEntryMenu_")) && text.size() <= 4000) {
            std::scoped_lock lock(mutex);
            if (uiEvents.size() < 8) uiEvents.push_back({{"name", name}, {"text", text}});
        }
        return RE::BSEventNotifyControl::kContinue;
    }
    RE::BSEventNotifyControl ProcessEvent(const RE::TESActivateEvent* event, RE::BSTEventSource<RE::TESActivateEvent>*) override
    {
        auto player = RE::PlayerCharacter::GetSingleton();
        if (!event || !player || !event->objectActivated || !event->actionRef || event->actionRef.get() != player) return RE::BSEventNotifyControl::kContinue;
        auto actor = event->objectActivated->As<RE::Actor>();
        if (!actor) return RE::BSEventNotifyControl::kContinue;  // doors, containers, items: not our business
        const auto info = Inspect(actor);
        const bool inSession = !session.empty();
        const bool track = anima::TrackActivation(autoStart, true, true, actor == player, inSession, info.valid);
        SKSE::log::info("ANIMA activate: actor='{}' form={:08X} enabled={} in_session={} menu_open={} valid={} (dist={:.0f} dead={} hostile={} combat={} base={}) -> {}",
                        info.name, info.id, autoStart, inSession, open, info.valid, info.distance, info.dead, info.hostile, info.combat, info.hasBase,
                        track ? "waiting_for_dialogue_menu" : "ignored");
        if (!track) return RE::BSEventNotifyControl::kContinue;
        activationActor = actor->GetHandle(); activationAt = Clock::now(); activationPending = true; activationMenuSeen = open;
        return RE::BSEventNotifyControl::kContinue;
    }
private:
    std::atomic_bool taskPending{false}, resync{false};
    std::mutex mutex;
    std::deque<json> outgoing, commands, uiEvents;
    anima::UiState uiState;
    std::atomic<anima::Panel> uiRenderPanel{anima::Panel::none};
    std::atomic_uint64_t uiRenderGeneration{0}, uiProbeGeneration{0};
    std::atomic_bool uiTaskPending{false}, uiFilled{false};
    int nextUi = -1;
    Clock::time_point uiOpenedAt;
    std::string workerSession, session, epoch, lastSnapshot, delivery, subtitleText;
    RE::ActorHandle speaker;
    RE::BSSoundHandle sound;
    anima::AudioState audioState;
    std::string audioResult = "none", lastMaterial;
    std::int64_t plannedMs = 0;
    bool lastPlaying = false, startAcked = false, suppressVanilla = true, sayActive = false, sayPending = false, openingDelivery = false;
    Clock::time_point sayStart;
    anima::SpeechSuppressor suppressor;
    std::atomic_bool fastPoll{false};
    std::atomic<std::int64_t> fastUntilMs{0};
    Clock::time_point deliveryStart;
    std::unordered_set<std::string> seen;
    std::uint64_t serial = 0, sequence = 0, snapshotSequence = 0;
    INTERNET_PORT port = 8768;
    std::uint32_t pttKey = 66, menuKey = 65, chatKey = 35;
    bool direct = false, requestPending = false, autoStart = true, activationPending = false, activationMenuSeen = false;
    RE::ActorHandle activationActor;
    Clock::time_point activationAt;
    RE::ActorHandle requestActor;
    Clock::time_point requestAt;
    std::uint32_t directCell = 0;
    Clock::time_point lastActivity;
    bool open = false, listening = false, animateMouth = true, audioStarted = false, endAfter = false, mouthOwned = false;
    float originalMouth = 0;
    bool captionFallback = false, engineSubtitle = true;
    int captionReadds = 0;
    Clock::time_point captionCheckAt;
    std::vector<float> mouth;
    Clock::time_point openedAt, lastCapture, playbackStart, playbackEnd;

    // Top-left notices are off: only real problems reach the screen. Captions use the bottom subtitle line.
    void Notify(const char* text)
    {
        const std::string_view t(text ? text : "");
        if (t.find("unavailable") == std::string_view::npos && t.find("talk to an NPC first") == std::string_view::npos) return;
        auto vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> callback;
        if (vm) vm->DispatchStaticCall("Debug", "Notification", RE::MakeFunctionArguments(RE::BSFixedString(text)), callback);
    }
    bool Active() const { return anima::SessionActive(open, direct); }
    bool MenuBlocksDirect()
    {
        auto ui = RE::UI::GetSingleton();
        return open || !ui || ui->GameIsPaused() || ui->IsMenuOpen("CustomMenu");
    }
    struct TargetInfo {
        const char* source = "none";
        RE::Actor* actor = nullptr;
        std::string name;
        std::uint32_t id = 0;
        float distance = 0;
        bool hasRef = false, dead = false, hostile = false, combat = false, hasBase = false, valid = false;
    };
    // Crosshair reference: CrosshairPickData::target, then ::targetActor (both exist in the pinned CommonLibSSE-NG;
    // SE/AE builds have single handles, multi-runtime builds index per VR device and GetActiveTarget covers target).
    RE::Actor* CrosshairActor(const char*& source, bool& hasRef)
    {
        source = "none"; hasRef = false;
        auto pick = RE::CrosshairPickData::GetSingleton();
        if (!pick) return nullptr;
        auto reference = pick->GetActiveTarget().get();
        hasRef = reference != nullptr;
        if (auto actor = reference ? reference->As<RE::Actor>() : nullptr) { source = "target"; return actor; }
#if defined(EXCLUSIVE_SKYRIM_FLAT)
        auto second = pick->targetActor.get();
#else
        auto second = pick->targetActor[0].get();
#endif
        if (second) hasRef = true;
        if (auto actor = second ? second->As<RE::Actor>() : nullptr) { source = "targetActor"; return actor; }
        return nullptr;
    }
    // Facts and verdict for the given actor, or for the crosshair actor when none is given. Raw pointer valid for this tick only.
    TargetInfo Inspect(RE::Actor* given = nullptr)
    {
        TargetInfo info;
        auto player = RE::PlayerCharacter::GetSingleton();
        auto actor = given;
        if (given) info.source = "given";
        else actor = CrosshairActor(info.source, info.hasRef);
        if (!actor || !player) return info;
        info.actor = actor; info.hasRef = true; info.name = Text(actor->GetName(), 64); info.id = actor->GetFormID();
        info.hasBase = actor->GetActorBase() != nullptr; info.dead = actor->IsDead();
        info.hostile = actor != player && actor->IsHostileToActor(player); info.combat = actor->IsInCombat();
        info.distance = actor->GetPosition().GetDistance(player->GetPosition());
        info.valid = anima::ValidDirectTarget(true, actor == player, info.hasBase, info.dead, info.hostile, info.combat) &&
                     info.distance <= anima::kDirectMaxDistance;
        return info;
    }
    void LogPress(const char* key, const TargetInfo& info, const char* outcome)
    {
        auto ui = RE::UI::GetSingleton();
        std::string target = info.actor
            ? std::format("'{}' form={:08X} dist={:.0f} dead={} hostile={} combat={} base={} valid={} via={}", info.name, info.id, info.distance,
                          info.dead, info.hostile, info.combat, info.hasBase, info.valid, info.source)
            : std::format("none (crosshair has {})", info.hasRef ? "a non-actor reference" : "nothing");
        SKSE::log::info("ANIMA key {} down: session_empty={} dialogue_menu={} game_paused={} custom_menu={} listening={} target={} -> {}", key, session.empty(), open,
                        ui && ui->GameIsPaused(), ui && ui->IsMenuOpen("CustomMenu"), listening, target, outcome);
    }
    // Living, non-hostile actor under the crosshair, or null.
    RE::Actor* DirectTarget()
    {
        auto info = Inspect();
        return info.valid ? info.actor : nullptr;
    }
    bool IsDirectTarget(RE::Actor* actor) { return actor && Inspect(actor).valid; }
    void ProcessActivation(Clock::time_point now)
    {
        auto actor = activationActor.get();
        const auto age = std::chrono::duration_cast<std::chrono::milliseconds>(now - activationAt).count();
        const bool sessionActive = !session.empty() || open;
        const auto info = actor ? Inspect(actor.get()) : TargetInfo{};
        const auto action = anima::DecideActivation(age, activationMenuSeen || open, sessionActive, MenuBlocksDirect(), info.valid);
        if (action == anima::ActivationAction::wait) return;
        activationPending = false;
        if (action == anima::ActivationAction::cancel) {
            SKSE::log::info("ANIMA activate: not starting for form={:08X} after {} ms: {}", info.id, age,
                            anima::ActivationReason(activationMenuSeen || open, sessionActive, info.valid));
            return;
        }
        SKSE::log::info("ANIMA activate: no dialogue menu within {} ms for '{}' form={:08X}; starting direct session", age, info.name, info.id);
        if (StartDirect(actor.get())) {
            if (anima::OpensTextBoxOnStart(anima::StartSource::activation)) OpenUi(anima::Panel::text);
            else Notify(anima::ActivationHint(chatKey, pttKey, menuKey).c_str());  // shown once per started session; the box waits for the key
        }
    }
    // Called on the game thread from the Papyrus fragment of the Anima.esp "Talk with me." line.
    void RequestConversation()
    {
        auto manager = RE::MenuTopicManager::GetSingleton();
        auto reference = manager ? manager->speaker.get() : RE::NiPointer<RE::TESObjectREFR>();
        auto actor = reference ? reference->As<RE::Actor>() : nullptr;
        if (!actor || actor == RE::PlayerCharacter::GetSingleton()) actor = DirectTarget();
        if (!actor) { SKSE::log::warn("ANIMA conversation request ignored: no speaker"); Notify("ANIMA: talk to an NPC first."); return; }
        requestActor = actor->GetHandle(); requestPending = true; requestAt = Clock::now();
        SKSE::log::info("ANIMA conversation requested from dialogue line; actor={:08X}", actor->GetFormID());
    }
    void ProcessRequest(Clock::time_point now)
    {
        auto actor = requestActor.get();
        const auto age = std::chrono::duration_cast<std::chrono::milliseconds>(now - requestAt).count();
        const bool sameSpeaker = !session.empty() && speaker.get().get() == actor.get();
        const auto action = anima::DecideOpenRequest(requestPending && actor, age, MenuBlocksDirect(), sameSpeaker && direct,
                                                     IsDirectTarget(actor.get()), uiState.panel != anima::Panel::none);
        switch (action) {
        case anima::OpenRequestAction::start_direct:
            requestPending = false;
            if (StartDirect(actor.get()) && anima::OpensTextBoxOnStart(anima::StartSource::talk_line)) OpenUi(anima::Panel::text);
            break;
        case anima::OpenRequestAction::open_text: requestPending = false; OpenUi(anima::Panel::text); break;
        case anima::OpenRequestAction::expire:
            requestPending = false; SKSE::log::info("ANIMA conversation request ended without opening the text box"); break;
        default:
            if (requestPending && !actor) requestPending = false;
            break;
        }
    }
    // Like Mantella's conversation, the NPC stops walking and faces the player while we talk; End() lets go.
    RE::ActorHandle held;
    void Hold(RE::Actor* actor)
    {
        auto vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto policy = vm ? vm->GetObjectHandlePolicy() : nullptr;
        auto player = RE::PlayerCharacter::GetSingleton();
        if (!vm || !policy || !actor || !player || actor->IsPlayerRef()) return;
        const auto handle = policy->GetHandleForObject(actor->GetFormType(), actor);
        if (!handle) return;
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb;
        vm->DispatchMethodCall(handle, "Actor", "SetDontMove", RE::MakeFunctionArguments(true), cb);
        vm->DispatchMethodCall(handle, "Actor", "SetLookAt", RE::MakeFunctionArguments(static_cast<RE::TESObjectREFR*>(player), false), cb);
        held = actor->GetHandle();
    }
    void Unhold()
    {
        auto actor = held.get(); held = {};
        auto vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto policy = vm ? vm->GetObjectHandlePolicy() : nullptr;
        if (!actor || !vm || !policy) return;
        const auto handle = policy->GetHandleForObject(actor->GetFormType(), actor.get());
        if (!handle) return;
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> cb;
        vm->DispatchMethodCall(handle, "Actor", "SetDontMove", RE::MakeFunctionArguments(false), cb);
        vm->DispatchMethodCall(handle, "Actor", "ClearLookAt", RE::MakeFunctionArguments(), cb);
    }
    void BeginSession(RE::Actor* actor, Clock::time_point now, bool directStart = false)
    {
        End(true, "state_changed"); session = std::format("{}-{}", epoch, ++serial); sequence = 0;
        speaker = actor->GetHandle();
        Hold(actor);
        snapshotSequence = 1;
        auto data = Snapshot(actor); auto event = Event("start");
        event["direct"] = directStart;  // Anima started this session: the bridge may open it with a greeting
        event["snapshot"] = data; lastSnapshot = Fingerprint(data); lastMaterial = MaterialFingerprint(data); Queue(std::move(event));
        lastCapture = now; lastActivity = now;
        std::scoped_lock lock(mutex); workerSession = session;
    }
    bool StartDirect(RE::Actor* actor)
    {
        auto player = RE::PlayerCharacter::GetSingleton();
        if (!actor || !player) return false;
        BeginSession(actor, Clock::now(), true);
        direct = true;
        auto cell = player->GetParentCell(); directCell = cell ? cell->GetFormID() : 0;
        SKSE::log::info("ANIMA direct session started without dialogue menu; session={} actor={:08X}", session, actor->GetFormID());
        return true;
    }
    void TickDirect(Clock::time_point now)
    {
        auto actor = speaker.get(); auto player = RE::PlayerCharacter::GetSingleton();
        if (!actor || !player) { End(true, "actor_lost"); return; }
        auto cell = player->GetParentCell();
        const bool pending = !delivery.empty() || uiState.panel != anima::Panel::none;
        const std::int64_t idle = pending ? 0 : std::chrono::duration_cast<std::chrono::milliseconds>(now - lastActivity).count();
        const auto reason = anima::DecideDirectEnd(actor->GetPosition().GetDistance(player->GetPosition()), actor->IsInCombat(), actor->IsDead(),
                                                   (cell ? cell->GetFormID() : 0) == directCell, idle);
        if (reason != anima::DirectEnd::none) {
            SKSE::log::info("ANIMA direct session ended: {}", anima::DirectEndName(reason));
            End(true, anima::DirectEndName(reason)); return;
        }
        if (now - lastCapture >= std::chrono::seconds(1)) RefreshSnapshot(actor.get(), now);
    }
    void RefreshSnapshot(RE::Actor* actor, Clock::time_point now)
    {
        lastCapture = now;
        auto data = Snapshot(actor); auto fingerprint = Fingerprint(data);
        if (fingerprint != lastSnapshot) {
            auto material = MaterialFingerprint(data);
            if (material != lastMaterial) { Stop("state_changed", "interrupted"); ++snapshotSequence; lastMaterial = std::move(material); }
        }
        auto event = Event("snapshot"); event["snapshot"] = data;
        lastSnapshot = std::move(fingerprint); Queue(std::move(event));
    }
    bool CallUi(const char* function, const char* movie = nullptr)
    {
        auto vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> callback;
        return vm && vm->DispatchStaticCall("UI", function,
            movie ? RE::MakeFunctionArguments(RE::BSFixedString(movie), std::int32_t(0)) : RE::MakeFunctionArguments(), callback);
    }
    void OpenUi(anima::Panel panel)
    {
        if (!Active() || session.empty() || RE::UI::GetSingleton()->IsMenuOpen("CustomMenu")) return;
        if (!uiState.Open(session, panel)) return;
        uiOpenedAt = Clock::now();
        uiRenderPanel = panel; uiRenderGeneration = uiState.generation; uiFilled = false;
        if (!CallUi("OpenCustomMenu", panel == anima::Panel::text ? "textentrymenu" : "listmenu")) {
            CancelUi(); Notify("ANIMA: UIExtensions menu unavailable.");
        } else {
            Notify(panel == anima::Panel::text ? "ANIMA: type a message, Enter sends, Escape cancels." : "ANIMA conversation controls");
            SKSE::log::info("ANIMA menu opened; session={}", session);
        }
    }
    void CancelUi()
    {
        uiState.Cancel(); uiRenderPanel = anima::Panel::none; uiRenderGeneration = uiState.generation; uiFilled = false;
    }
    void RenderUi(std::uint64_t generation)
    {
        if (generation != uiRenderGeneration || uiFilled) return;
        auto panel = uiRenderPanel.load();
        auto menu = RE::UI::GetSingleton()->GetMenu("CustomMenu");
        if (panel == anima::Panel::none || !menu || !menu->uiMovie) return;
        RE::GFxValue root;
        const auto path = panel == anima::Panel::options ? "_root.listMenu" : "_root.textEntry";
        const bool found = menu->uiMovie->GetVariable(&root, path) && root.IsObject();
        if (uiProbeGeneration.exchange(generation) != generation)
            SKSE::log::info("ANIMA UI task executing; panel={} root={} available={}", static_cast<int>(panel), path, found);
        if (!found) return;
        if (generation != uiRenderGeneration) return;
        bool populated = false;
        if (panel == anima::Panel::options) {
            // Match SKSE UI.InvokeStringA: one scalar argument per entry, not one GFx array.
            std::array<RE::GFxValue, anima::MenuEntries.size()> entries;
            for (std::size_t i = 0; i < entries.size(); ++i) entries[i].SetString(anima::MenuEntries[i]);
            populated = menu->uiMovie->Invoke("_root.listMenu.LM_AddTreeEntries", nullptr, entries.data(), entries.size());
            RE::GFxValue sort(false); menu->uiMovie->Invoke("_root.listMenu.LM_SetSortingEnabled", nullptr, &sort, 1);
        } else {
            RE::GFxValue empty; empty.SetString("");
            populated = menu->uiMovie->Invoke("_root.textEntry.setTextEntryMenuText", nullptr, &empty, 1);
        }
        if (generation != uiRenderGeneration) return;
        uiFilled = populated;
        if (populated) SKSE::log::info("ANIMA UI-thread population succeeded; panel={} generation={}", static_cast<int>(panel), generation);
    }
    void ProcessUi()
    {
        std::deque<json> events;
        { std::scoped_lock lock(mutex); events.swap(uiEvents); }
        for (const auto& item : events) {
            const auto name = item.value("name", ""), text = item.value("text", "");
            if (uiState.Owns(session, anima::Panel::options)) {
                if (name == "UIListMenu_LoadMenu") {
                    SKSE::log::info("ANIMA UIExtensions list load callback received");
                } else if (name == "UIListMenu_SelectItem" && (text == "0" || text == "1" || text == "2")) {
                    nextUi = text[0] - '0'; CancelUi();
                } else if (name == "UIListMenu_CloseMenu") { CancelUi(); }
            } else if (uiState.Owns(session, anima::Panel::text)) {
                if (name == "UITextEntryMenu_LoadMenu") {
                    SKSE::log::info("ANIMA UIExtensions text load callback received");
                } else if (name == "UITextEntryMenu_TextChanged") {
                    if (uiState.CanSubmit(session, text) && BargeIn()) { Notify("ANIMA: sending your message..."); auto event = Event("player_text"); event["text"] = text; Queue(std::move(event)); }
                    CancelUi();  // the session stays open; the box reopens only on the next key press
                } else if (name == "UITextEntryMenu_CloseMenu") {
                    CancelUi();
                }
            }
        }
        if (!RE::UI::GetSingleton()->IsMenuOpen("CustomMenu")) {
            const auto choice = nextUi; nextUi = -1;
            if (choice == 0) OpenUi(anima::Panel::text);
            else if (choice == 1) Notify("ANIMA: hold F8 to speak; release to send.");
            else if (choice == 2) RE::UIMessageQueue::GetSingleton()->AddMessage(RE::DialogueMenu::MENU_NAME, RE::UI_MESSAGE_TYPE::kHide, nullptr);
            else if (uiState.panel != anima::Panel::none && Clock::now() - uiOpenedAt > std::chrono::seconds(5)) CancelUi();
        }
    }

    bool BargeIn()
    {
        auto actor = speaker.get();
        if (session.empty() || !Active() || !actor || actor->IsInCombat() || actor->IsDead()) return false;
        lastActivity = Clock::now();
        Stop("barge_in", "interrupted"); actor->StopCurrentDialogue();
        if (session.empty() || !Active()) return false;
        // Invalidate both downloaded and in-flight commands before admitting new input.
        ++snapshotSequence;
        auto data = Snapshot(actor.get()); lastSnapshot = Fingerprint(data); lastMaterial = MaterialFingerprint(data);
        auto event = Event("snapshot"); event["snapshot"] = data; Queue(std::move(event));
        lastCapture = Clock::now();
        return true;
    }

    json Event(const char* kind)
    {
        ++sequence;
        return {{"schema_version", "0.1"}, {"event_id", std::format("{}:{}", session, sequence)},
                {"session_id", session}, {"game_epoch", epoch}, {"sequence", sequence},
                {"state_revision", snapshotSequence}, {"captured_at_ms", UtcMs()},
                {"game_pid", GetCurrentProcessId()}, {"kind", kind}};
    }
    void Queue(json event)
    {
        if (event["kind"] == "player_text" || event["kind"] == "listen_stop")  // a turn is now pending: poll fast for up to 60 s
            fastUntilMs = std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch()).count() + 60000;
        std::scoped_lock lock(mutex);
        if (event["kind"] == "snapshot")
            std::erase_if(outgoing, [](const json& item) { return item["kind"] == "snapshot"; });
        if (outgoing.size() >= 32) { SKSE::log::error("Connector queue full"); return; }
        outgoing.push_back(std::move(event));
    }
    json Snapshot(RE::Actor* actor)
    {
        auto player = RE::PlayerCharacter::GetSingleton();
        auto manager = RE::MenuTopicManager::GetSingleton();
        json dialogue = json::array();
        bool complete = true;
        if (manager) {
        DialogueLock lock(manager);
        if (manager->dialogueList) {
            for (auto choice : *manager->dialogueList) {
                if (!choice || !choice->parentTopic) continue;
                if (dialogue.size() >= 16) { complete = false; break; }
                json responses = json::array();
                for (auto response : choice->responses) {
                    if (response && responses.size() < 8) responses.push_back(Text(response->text.c_str()));
                    else if (response) complete = false;
                }
                auto quest = choice->parentQuest;
                dialogue.push_back({{"topic", Form(choice->parentTopic)}, {"info", Form(choice->parentTopicInfo)},
                    {"player_text", Text(choice->topicText.c_str())}, {"responses", responses}, {"quest", Form(quest)},
                    {"quest_stage", quest ? json(quest->GetCurrentStageID()) : json(nullptr)}, {"provenance", "engine_dialogue_menu"}});
            }
        } else complete = false;
        } else complete = false;
        auto cell = actor->GetParentCell();
        bool interior = cell && cell->IsInteriorCell();
        auto sky = RE::Sky::GetSingleton();
        auto calendar = RE::Calendar::GetSingleton();
        return {{"npc", Actor(actor, player)}, {"player", Actor(player, player)},
                {"location", Form(actor->GetCurrentLocation())}, {"cell", Form(cell)}, {"interior", interior},
                {"hour", std::floor(calendar->GetHour() * 100) / 100}, {"game_days", calendar->GetDaysPassed()},
                {"weather", !interior && sky ? Form(sky->currentWeather) : json(nullptr)},
                {"weather_label", interior || !sky ? json(nullptr) : json(sky->IsSnowing() ? "snowing" : sky->IsRaining() ? "raining" : "not_raining_or_snowing")},
                {"dialogue", dialogue}, {"dialogue_complete", complete}};
    }
    static std::string Fingerprint(json value)
    {
        value.erase("game_days"); value.erase("hour");
        return value.dump();
    }
    // Only these changes interrupt playback and invalidate in-flight work. Everything else
    // (sees_player, dialogue list, weather, equipment) still updates the bridge's snapshot.
    static std::string MaterialFingerprint(const json& data)
    {
        json material = json::object();
        for (const char* who : {"npc", "player"}) {
            const auto& actor = data[who];
            material[who] = {{"in_combat", actor["in_combat"]}, {"dead", actor["dead"]}, {"weapon_drawn", actor["weapon_drawn"]},
                             {"hostile_to_player", actor["hostile_to_player"]}};
        }
        const auto id = [&](const char* key) { return data[key].is_object() ? data[key]["form_id"] : json(nullptr); };
        material["speaker"] = data["npc"]["reference"].is_object() ? data["npc"]["reference"]["form_id"] : json(nullptr);
        material["location"] = id("location");
        material["cell"] = id("cell");
        material["interior"] = data["interior"];
        return material.dump();
    }
    std::int64_t ElapsedMs(Clock::time_point now) const
    {
        return std::chrono::duration_cast<std::chrono::milliseconds>(now - deliveryStart).count();
    }
    void Tick()
    {
        if (resync.exchange(false)) {
            End(false, "bridge_stop");
            std::scoped_lock lock(mutex); outgoing.clear();
        }
        auto now = Clock::now();
        ProcessUi();
        if (open && now - openedAt > std::chrono::milliseconds(350)) {
            auto manager = RE::MenuTopicManager::GetSingleton();
            auto reference = manager ? manager->speaker.get() : RE::NiPointer<RE::TESObjectREFR>();
            auto actor = reference ? reference->As<RE::Actor>() : nullptr;
            if (actor && actor != RE::PlayerCharacter::GetSingleton() && actor->GetActorBase()) {
                if (actor->IsInCombat() || actor->IsDead()) { End(true, "combat"); return; }
                if (session.empty() || speaker.get().get() != actor) BeginSession(actor, now);
                else if (now - lastCapture >= std::chrono::seconds(1)) RefreshSnapshot(actor, now);
            } else if (!session.empty()) End(true, "actor_lost");
        } else if (direct && !open && !session.empty()) TickDirect(now);
        if (requestPending) ProcessRequest(now);
        if (activationPending) ProcessActivation(now);
        std::deque<json> ready;
        { std::scoped_lock lock(mutex); ready.swap(commands); }
        for (const auto& command : ready) {
            try { Apply(command); }
            catch (const std::exception& e) { SKSE::log::error("Rejected command: {}", e.what()); }
        }
        fastPoll = !delivery.empty() || std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count() < fastUntilMs.load();
        if (delivery.empty()) return;
        auto actor = speaker.get();
        if (actor && !sayActive && !openingDelivery && Active() && !session.empty()) SuppressVanillaSpeech(actor.get(), now);  // the engine's own Say line is not vanilla speech
        if (actor) KeepSubtitle(actor.get(), now);
        if (!actor || !Active() || actor->IsInCombat() || actor->IsDead())
            Stop(!actor ? "actor_lost" : !Active() ? "menu_closed" : "combat", "interrupted");
        else if (sayPending) {
            // Say gives no receipt: only an engine signal that this actor is speaking proves the line started.
            const auto waited = std::chrono::duration_cast<std::chrono::milliseconds>(now - sayStart).count();
            const auto proof = anima::DecideSayProof(VanillaSpeaking(actor.get()), waited);
            if (proof == anima::SayProof::started) {
                sayPending = false; audioResult = "full"; startAcked = true;
                playbackStart = now; playbackEnd = now + std::chrono::milliseconds(plannedMs);
                SKSE::log::info("ANIMA say: actor speaking after {} ms; delivery={}", waited, delivery);
                Ack(delivery, "started");
            } else if (proof == anima::SayProof::failed) {
                sayPending = false;
                SKSE::log::error("ANIMA say: actor not speaking within {} ms; delivery={}", anima::kSayProofMs, delivery);
                Stop("audio_failed", "failed", "failed");
            }
        }
        else if (audioStarted) {
            const auto current = std::chrono::duration_cast<std::chrono::milliseconds>(now.time_since_epoch()).count();
            const bool playing = sound.IsPlaying();
            if (playing != lastPlaying) {
                lastPlaying = playing;
                SKSE::log::info("ANIMA IsPlaying -> {}; delivery={} soundID={} elapsed_ms={}", playing, delivery, sound.soundID, ElapsedMs(now));
            }
            const auto decision = audioState.Update(current, playing);
            if (decision == anima::AudioDecision::started) {
                playbackStart = now;
                SKSE::log::info("ANIMA observed native audio playing; delivery={} soundID={}", delivery, sound.soundID);
                startAcked = true; Ack(delivery, "started");
            } else if (decision == anima::AudioDecision::failed) {
                SKSE::log::error("ANIMA audio failed: observedPlaying={} soundID={}", audioState.observed, sound.soundID);
                if (audioState.observed) FinishDelivery("audio_failed", "full");  // heard, but ran far past its plan
                else DowngradeToSubtitle("failed");
            } else if (decision == anima::AudioDecision::ended_early) {
                SKSE::log::warn("ANIMA audio ended early; delivery={} soundID={} elapsed_ms={} planned_ms={}", delivery, sound.soundID, ElapsedMs(now), plannedMs);
                DowngradeToSubtitle("ended_early");
            } else if (decision == anima::AudioDecision::completed) FinishDelivery("end", "full");
        }
        else if (now >= playbackEnd) FinishDelivery("end", audioResult.c_str());
        if (!delivery.empty() && mouthOwned && (!audioStarted || audioState.observed) && !mouth.empty()) {
            auto face = actor->GetFaceGenAnimationData();
            auto index = static_cast<std::size_t>(std::chrono::duration<float>(now - playbackStart).count() * 20);
            if (face && face->phenomeKeyFrame.count && face->phenomeKeyFrame.values) {
                RE::BSSpinLockGuard lock(face->lock);
                face->phenomeKeyFrame.SetValue(0, index < mouth.size() ? mouth[index] : 0.0F);
            }
        }
    }
    // Cheap engine signals of vanilla speech from this NPC, none of which is Anima's sound handle:
    // MenuTopicManager::currentTopicInfo (set only while a menu-selected topic or greeting is being spoken)
    // and a subtitle from the speaker that is not our own text (barks, greetings, scripted Say).
    bool VanillaSpeaking(RE::Actor* actor)
    {
        if (auto topics = RE::MenuTopicManager::GetSingleton(); topics && topics->currentTopicInfo && topics->speaker == speaker) return true;
        if (auto manager = RE::SubtitleManager::GetSingleton()) {
            RE::BSSpinLockGuard lock(manager->lock);
            const auto handle = actor->GetHandle();
            for (const auto& info : manager->subtitles)
                if (info.speaker == handle && subtitleText != info.subtitle.c_str()) return true;
        }
        return false;
    }
    void SuppressVanillaSpeech(RE::Actor* actor, Clock::time_point now)
    {
        const auto action = suppressor.Decide(suppressVanilla, !delivery.empty(), suppressVanilla && VanillaSpeaking(actor));
        if (action == anima::SuppressAction::none) return;
        actor->StopCurrentDialogue();
        if (action == anima::SuppressAction::stop_and_log)
            SKSE::log::info("ANIMA suppressed vanilla speech: reason=vanilla_dialogue_during_reply delivery={} elapsed_ms={}", delivery, ElapsedMs(now));
    }
    // Mantella's NpcSpeak route: the actor says an Anima.esp topic whose voice and lip files the bridge wrote.
    // Dispatched as ObjectReference.Say through the Papyrus VM. False (with the reason logged) when Anima.esp or
    // the topic is not loaded or the call cannot be queued, so the bridge can fall back to system audio.
    bool SayTopic(RE::Actor* actor, long long index)
    {
        const auto local = anima::SayTopicLocalId(index);
        auto data = RE::TESDataHandler::GetSingleton();
        auto topic = local && data ? data->LookupForm<RE::TESTopic>(local, "Anima.esp") : nullptr;
        if (!topic) { SKSE::log::error("ANIMA say: Anima.esp topic {} not found (is Anima.esp enabled?); falling back", index); return false; }
        auto vm = RE::BSScript::Internal::VirtualMachine::GetSingleton();
        auto policy = vm ? vm->GetObjectHandlePolicy() : nullptr;
        const auto handle = policy ? policy->GetHandleForObject(actor->GetFormType(), actor) : RE::VMHandle{};
        RE::BSTSmartPointer<RE::BSScript::IStackCallbackFunctor> callback;
        if (!handle || !vm->DispatchMethodCall(handle, "ObjectReference", "Say",
                RE::MakeFunctionArguments(static_cast<RE::TESTopic*>(topic), static_cast<RE::Actor*>(nullptr), false), callback)) {
            SKSE::log::error("ANIMA say: ObjectReference.Say could not be dispatched for actor={:08X}", actor->GetFormID());
            return false;
        }
        sayActive = true;
        SKSE::log::info("ANIMA say: topic {} dispatched for actor={:08X}", index, actor->GetFormID());
        return true;
    }
    // Caller holds manager->lock. total = all entries, theirs = this speaker's entries, ours = our text for this speaker.
    struct SubtitleCounts { std::size_t total = 0, theirs = 0, ours = 0; };
    SubtitleCounts CountSubtitles(RE::SubtitleManager* manager, RE::ObjectRefHandle handle) const
    {
        SubtitleCounts counts;
        for (const auto& info : manager->subtitles) {
            ++counts.total;
            if (info.speaker == handle) { ++counts.theirs; if (!subtitleText.empty() && subtitleText == info.subtitle.c_str()) ++counts.ours; }
        }
        return counts;
    }
    // Resets the engine's subtitle state and removes every entry of this speaker (a stale entry or a stale
    // currentSpeaker makes the engine ignore new entries). KillSubtitles takes the manager lock itself, so it runs
    // before ours is taken. Returns the number of removed entries.
    std::size_t ClearSubtitles(RE::SubtitleManager* manager, RE::ObjectRefHandle handle)
    {
        manager->KillSubtitles();
        RE::BSSpinLockGuard lock(manager->lock);
        std::size_t removed = 0;
        for (auto it = manager->subtitles.begin(); it != manager->subtitles.end();) {
            if (it->speaker == handle) { it = manager->subtitles.erase(it); ++removed; }
            else ++it;
        }
        if (manager->currentSpeaker == handle || manager->subtitles.empty()) manager->currentSpeaker = RE::ObjectRefHandle{};
        return removed;
    }
    // Clears stale state (see ClearSubtitles), then adds ours. Returns whether our entry is present afterwards.
    bool PushSubtitle(RE::SubtitleManager* manager, RE::Actor* actor, const std::string& text, const char* why)
    {
        const auto handle = actor->GetHandle();
        SubtitleCounts before;
        bool speakerSetBefore = false;
        { RE::BSSpinLockGuard lock(manager->lock); before = CountSubtitles(manager, handle); speakerSetBefore = manager->currentSpeaker.get() != nullptr; }
        const auto removed = ClearSubtitles(manager, handle);
        RE::BSSpinLockGuard lock(manager->lock);
        RE::SubtitleInfo info{};
        info.speaker = handle; info.subtitle = text.c_str(); info.targetDistance = 10000; info.forceDisplay = true;
        manager->subtitles.push_back(info); subtitleText = text;
        const auto after = CountSubtitles(manager, handle);
        SKSE::log::info("ANIMA caption: {} mode=engine_subtitle before total={} speaker={} currentSpeakerSet={} killed=1 removed={} after total={} speaker={} ours={} currentSpeakerSet={}",
                        why, before.total, before.theirs, speakerSetBefore, removed, after.total, after.theirs, after.ours, manager->currentSpeaker.get() != nullptr);
        return after.ours > 0;
    }
    void AddSubtitle(RE::Actor* actor, const std::string& text)
    {
        if (text.empty()) return;
        captionReadds = 0; captionCheckAt = Clock::now();
        auto manager = RE::SubtitleManager::GetSingleton();
        bool confirmed = false;
        if (!manager) SKSE::log::warn("ANIMA caption: SubtitleManager unavailable");
        else if (engineSubtitle) confirmed = PushSubtitle(manager, actor, text, "add");
        else {
            const auto removed = ClearSubtitles(manager, actor->GetHandle());
            SKSE::log::info("ANIMA caption: add mode=message_only removed={}", removed);
        }
        if (!anima::ShowCaptionMessage(engineSubtitle, captionFallback, manager != nullptr)) return;
        const auto lines = anima::CaptionLines(Text(actor->GetName(), 64), text);
        for (const auto& line : lines) Notify(line.c_str());
        SKSE::log::info("ANIMA caption: on-screen message shown lines={} engine_entry_confirmed={}", lines.size(), confirmed);
    }
    // While a delivery is active, an engine that drops our entry gets it back (at most 3 times).
    void KeepSubtitle(RE::Actor* actor, Clock::time_point now)
    {
        if (subtitleText.empty() || captionReadds >= 3 || now - captionCheckAt < std::chrono::milliseconds(250)) return;
        captionCheckAt = now;
        auto manager = RE::SubtitleManager::GetSingleton();
        if (!manager) return;
        {
            RE::BSSpinLockGuard lock(manager->lock);
            if (CountSubtitles(manager, actor->GetHandle()).ours > 0) return;
        }
        ++captionReadds;
        const auto text = subtitleText;
        SKSE::log::warn("ANIMA caption: entry dropped by engine at {} ms; re-adding ({}/3); delivery={}", ElapsedMs(now), captionReadds, delivery);
        PushSubtitle(manager, actor, text, "readd");
    }
    void Apply(const json& command)
    {
        auto kind = command.value("kind", "idle");
        if (kind == "idle" || command.value("session_id", "") != session || session.empty()) return;
        auto id = command.value("delivery_id", "");
        if (kind == "stop") {
            if (delivery == id) Stop("bridge_stop", "interrupted");
            else if (!seen.contains(id)) Ack(id, "interrupted");
            seen.insert(id); return;
        }
        if (kind != "speak" || seen.contains(id)) return;
        auto actor = speaker.get();
        const auto target = command.value("npc_id", "");
        const auto expected = actor ? std::format("ref:{:08x}", actor->GetFormID()) : "";
        const auto requestSession = command.value("session_id", "");
        const bool ready = actor && Active() && !actor->IsInCombat() && !actor->IsDead() && seen.size() < 128;
        auto manager = RE::MenuTopicManager::GetSingleton();
        const auto targetHandle = actor ? static_cast<RE::TESObjectREFR*>(actor.get())->GetHandle() : RE::ObjectRefHandle{};
        // Menu sessions require the menu's selected speaker; direct sessions have no menu, so the actor's own speech decides.
        const bool menuSession = open;
        const bool actorSpeaking = !menuSession && actor && VanillaSpeaking(actor.get());
        const auto expiresAt = command.value("expires_at_ms", 0LL);
        const auto now = UtcMs();
        const auto decision = anima::EvaluateDialogue(
            {requestSession, target, id, command.value("snapshot_sequence", 0ULL), expiresAt, command.value("text", "").size()},
            {session, expected, snapshotSequence, now, ready, actorSpeaking, seen.contains(id)}, manager, targetHandle, menuSession);
        if (decision == anima::Decision::ignore || decision == anima::Decision::wait) return;
        if (decision == anima::Decision::reject) {
            SKSE::log::warn("ANIMA delivery rejected: delivery={} mode={} actor={} target={} expected={} activeSession={} combat={} dead={} seen={} "
                            "snapshot_seq={}/{} expires_in_ms={} text_bytes={}",
                            id, menuSession ? "menu" : "direct", actor != nullptr, target, expected, Active(),
                            actor && actor->IsInCombat(), actor && actor->IsDead(), seen.size(),
                            command.value("snapshot_sequence", 0ULL), snapshotSequence, expiresAt - now, command.value("text", "").size());
            Ack(id, "failed"); seen.insert(id); return;
        }
        if (command.value("mode", "speech") == "silence") { seen.insert(id); Ack(id, "completed", "end", "none"); return; }
        const auto topicIndex = command.contains("topic_index") && command["topic_index"].is_number_integer() ? command["topic_index"].get<long long>() : 0LL;
        const bool opening = id.starts_with("opening-");
        if (anima::MayStopCurrentDialogue(opening)) actor->StopCurrentDialogue();  // an opening must never cut off the NPC's own greeting or quest lines
        Stop("superseded", "interrupted"); openingDelivery = opening; delivery = id; seen.insert(id); audioStarted = false;
        audioResult = "none"; startAcked = false; lastPlaying = false; deliveryStart = Clock::now(); suppressor.Begin();
        if (command.contains("audio_file") && !command["audio_file"].is_null()) {
            auto path = command["audio_file"].get<std::string>();
            if (path != std::format("Sound/Voice/AnimaConnector/{}.wav", id) || !std::filesystem::exists(std::filesystem::path("Data") / path)) {
                Stop("audio_failed", "failed", "failed"); return;
            }
            std::replace(path.begin(), path.end(), '/', '\\');
            RE::BSResourceNiBinaryStream resource(path);
            SKSE::log::info("ANIMA audio resource: delivery={} good={} error={} priority=128", id, resource.good(), static_cast<int>(resource.lastError));
            sound = RE::BSSoundHandle{};
            if (resource.good()) {
                BuildVoice(sound, path.c_str());
                sound.SetPosition(actor->GetPosition());
                if (auto node = actor->Get3D()) sound.SetObjectToFollow(node);
                if (sound.IsValid() && sound.Play()) audioStarted = true;
            }
            // Audio that cannot start still leaves the player a subtitle to read.
            audioResult = audioStarted ? "full" : "failed";
            if (!audioStarted) SKSE::log::error("ANIMA audio could not start; subtitle only. delivery={}", id);
        }
        if (topicIndex && !SayTopic(actor.get(), topicIndex)) { Stop("say_unavailable", "failed", "none"); return; }
        AddSubtitle(actor.get(), command.value("text", "")); mouth.clear();
        if (animateMouth && !sayActive && command.contains("mouth") && command["mouth"].is_array() && command["mouth"].size() <= 600) {
            for (const auto& value : command["mouth"]) mouth.push_back(std::clamp(value.get<float>(), 0.0F, 1.0F));
            if (auto face = actor->GetFaceGenAnimationData(); face && face->phenomeKeyFrame.count && face->phenomeKeyFrame.values) {
                RE::BSSpinLockGuard lock(face->lock); originalMouth = face->phenomeKeyFrame.values[0]; mouthOwned = true;
            }
        }
        auto duration = std::clamp(command.value("duration_s", 3.0), 0.05, 30.0);
        lastActivity = Clock::now();
        playbackStart = Clock::now(); playbackEnd = playbackStart + std::chrono::milliseconds(static_cast<int>(duration * 1000));
        endAfter = command.value("mode", "speech") == "end";
        plannedMs = static_cast<std::int64_t>(duration * 1000);
        if (sayActive) { audioResult = "none"; sayPending = true; sayStart = Clock::now(); }  // started/full only once the actor is seen speaking
        else if (audioStarted) {
            audioState.Begin(std::chrono::duration_cast<std::chrono::milliseconds>(playbackStart.time_since_epoch()).count(), plannedMs);
        } else { startAcked = true; Ack(id, "started"); }
    }
    void Ack(const std::string& id, const char* status, const char* reason = nullptr, const char* audio = nullptr)
    {
        if (session.empty() || id.empty()) return;
        auto event = Event("delivery"); event["delivery_id"] = id; event["status"] = status;
        if (reason) event["reason"] = reason;
        if (audio) event["audio"] = audio;
        Queue(std::move(event));
    }
    // Audio stopped being the clock (ended early or never started): drop mouth animation, keep the subtitle
    // up to the planned duration, then report completed. Never waits for expires_at_ms.
    void DowngradeToSubtitle(const char* audio)
    {
        SKSE::log::info("ANIMA subtitle-only continuation; delivery={} audio={} soundID={} elapsed_ms={} planned_ms={}",
                        delivery, audio, sound.soundID, ElapsedMs(Clock::now()), plannedMs);
        if (audioStarted) sound.Stop();
        ReleaseMouth();
        audioStarted = false; audioResult = audio;
        if (!startAcked) { startAcked = true; Ack(delivery, "started"); }
    }
    void FinishDelivery(const char* reason, const char* audio)
    {
        const auto closeMenu = endAfter;
        Stop(reason, "completed", audio); lastActivity = Clock::now();
        if (closeMenu) RE::UIMessageQueue::GetSingleton()->AddMessage(RE::DialogueMenu::MENU_NAME, RE::UI_MESSAGE_TYPE::kHide, nullptr);
    }
    void ReleaseMouth()
    {
        auto actor = speaker.get();
        if (mouthOwned && actor) {
            if (auto face = actor->GetFaceGenAnimationData(); face && face->phenomeKeyFrame.count && face->phenomeKeyFrame.values) {
                RE::BSSpinLockGuard lock(face->lock); face->phenomeKeyFrame.SetValue(0, originalMouth);
            }
        }
        mouthOwned = false;
    }
    void RemoveSubtitle()
    {
        if (subtitleText.empty()) return;
        if (auto manager = RE::SubtitleManager::GetSingleton()) {
            const auto removed = ClearSubtitles(manager, speaker);  // erase ours, reset currentSpeaker, KillSubtitles
            SKSE::log::info("ANIMA caption: remove removed={} killed=1", removed);
        }
        subtitleText.clear();
    }
    // Every stop names its reason and logs the context needed to diagnose it from the plugin log.
    // `audio` is reported to the bridge only when the outcome of the audio is known.
    void Stop(const char* reason, const char* status, const char* audio = nullptr)
    {
        if (delivery.empty()) return;
        SKSE::log::info("ANIMA stop: reason={} status={} audio={} delivery={} soundID={} elapsed_ms={} planned_ms={}",
                        reason, status, audio ? audio : "-", delivery, audioStarted ? sound.soundID : 0u,
                        ElapsedMs(Clock::now()), plannedMs);
        if (audioStarted) sound.Stop();
        if (sayActive && !openingDelivery) { if (auto actor = speaker.get()) actor->StopCurrentDialogue(); }
        sayActive = false; sayPending = false;
        ReleaseMouth();
        RemoveSubtitle();  // the subtitle lives exactly as long as the delivery
        subtitleText.clear(); audioStarted = false; Ack(delivery, status, reason, audio); delivery.clear();
    }
    void End(bool notify = true, const char* reason = "state_changed")
    {
        if (uiState.panel != anima::Panel::none) CallUi("CloseCustomMenu");
        CancelUi(); nextUi = -1;
        Stop(reason, "interrupted");
        if (notify && !session.empty()) Queue(Event("end"));
        Unhold();
        session.clear(); seen.clear(); listening = false; direct = false;
        std::scoped_lock lock(mutex); workerSession.clear(); commands.clear();
    }
    json Request(const wchar_t* method, const std::wstring& path, const std::string& body = "")
    {
        HttpHandle http(WinHttpOpen(L"AnimaConnector/0.1", WINHTTP_ACCESS_TYPE_NO_PROXY, nullptr, nullptr, 0));
        if (!http.handle) throw std::runtime_error("WinHTTP failed");
        WinHttpSetTimeouts(http.handle, 300, 300, 500, 500);
        HttpHandle connection(WinHttpConnect(http.handle, L"127.0.0.1", port, 0));
        HttpHandle request(WinHttpOpenRequest(connection.handle, method, path.c_str(), nullptr, nullptr, nullptr, 0));
        if (!request.handle || !WinHttpSendRequest(request.handle, L"Content-Type: application/json\r\n", static_cast<DWORD>(-1L),
            body.empty() ? WINHTTP_NO_REQUEST_DATA : const_cast<char*>(body.data()), static_cast<DWORD>(body.size()),
            static_cast<DWORD>(body.size()), 0) || !WinHttpReceiveResponse(request.handle, nullptr)) throw std::runtime_error("bridge unavailable");
        DWORD status = 0, size = sizeof(status);
        WinHttpQueryHeaders(request.handle, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, nullptr, &status, &size, nullptr);
        std::string received; char buffer[4096]; DWORD read = 0;
        while (WinHttpReadData(request.handle, buffer, sizeof(buffer), &read) && read) {
            received.append(buffer, read);
            if (received.size() > 65536) throw std::runtime_error("oversized command");
        }
        if (status < 200 || status >= 300) {
            const auto error = json::parse(received, nullptr, false);
            const auto reason = error.is_object() && error.contains("error") && error["error"].is_string() ? error["error"].get<std::string>() : "";
            throw HttpError(status, anima::RequiresResync(status, reason));
        }
        return received.empty() ? json::object() : json::parse(received);
    }
    void Worker()
    {
        auto lastWarning = Clock::now() - std::chrono::seconds(30);
        unsigned failures = 0;
        while (true) {
            if (uiRenderPanel != anima::Panel::none && !uiFilled && !uiTaskPending.exchange(true)) {
                auto generation = uiRenderGeneration.load();
                SKSE::GetTaskInterface()->AddUITask([this, generation] {
                    try { RenderUi(generation); } catch (const std::exception& e) { SKSE::log::error("ANIMA UI render failed: {}", e.what()); }
                    uiTaskPending = false;
                });
            }
            if (!taskPending.exchange(true)) SKSE::GetTaskInterface()->AddTask([this] {
                try { Tick(); } catch (const std::exception& e) { SKSE::log::error("Observation failed: {}", e.what()); }
                taskPending = false;
            });
            json next; std::string current;
            { std::scoped_lock lock(mutex); if (!outgoing.empty()) next = outgoing.front(); current = workerSession; }
            try {
                if (!next.is_null()) {
                    Request(L"POST", L"/v1/game/event", next.dump(-1, ' ', false, json::error_handler_t::replace));
                    std::scoped_lock lock(mutex);
                    if (!outgoing.empty() && outgoing.front()["event_id"] == next["event_id"]) outgoing.pop_front();
                }
                if (!current.empty()) {
                    auto result = Request(L"GET", L"/v1/game/command?session_id=" + std::wstring(current.begin(), current.end()));
                    if (result.value("kind", "idle") != "idle") { std::scoped_lock lock(mutex); if (commands.size() < 4) commands.push_back(std::move(result)); }
                }
                failures = 0;
            } catch (const HttpError& e) {
                failures = std::min(failures + 1, 16u);
                SKSE::log::warn("Bridge rejected request: {}", e.what());
                if (e.status >= 400 && e.status < 500) {
                    std::scoped_lock lock(mutex);
                    if (!outgoing.empty() && !next.is_null() && outgoing.front()["event_id"] == next["event_id"]) outgoing.pop_front();
                    if (e.resync) resync = true;
                }
            } catch (const std::exception& e) {
                failures = std::min(failures + 1, 16u);
                if (Clock::now() - lastWarning > std::chrono::seconds(10)) { SKSE::log::warn("Bridge: {}", e.what()); lastWarning = Clock::now(); }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(anima::PollDelayMs(fastPoll.load(), failures)));
        }
    }
};
}
namespace {
void OpenConversationNative(RE::StaticFunctionTag*)
{
    SKSE::GetTaskInterface()->AddTask([] { Connector::Get().OnConversationLine(); });
}
bool RegisterPapyrus(RE::BSScript::IVirtualMachine* vm)
{
    vm->RegisterFunction("OpenConversation", "AnimaNative", OpenConversationNative);
    return true;
}
}
extern "C" __declspec(dllexport) constinit SKSE::PluginVersionData SKSEPlugin_Version = anima::VersionMetadata();
extern "C" __declspec(dllexport) bool SKSEPlugin_Load(const SKSE::LoadInterface* skse)
{
    // Admission uses SKSE's supplied runtime before touching engine relocations.
    const auto version = skse->RuntimeVersion();
    if (!anima::SupportsRuntime({version.major(), version.minor(), version.patch(), version.build()})) {
        OutputDebugStringA("AnimaConnector: unsupported Skyrim runtime; supported: 1.6.318.0 and 1.7.104.0\n");
        return false;
    }
    if (auto directory = SKSE::log::log_directory()) {
        auto sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>((*directory / "AnimaConnector.log").string(), true);
        spdlog::set_default_logger(std::make_shared<spdlog::logger>("AnimaConnector", sink)); spdlog::flush_on(spdlog::level::info);
    }
    std::array<wchar_t,32768> executable{};
    const auto length = GetModuleFileNameW(nullptr, executable.data(), static_cast<DWORD>(executable.size()));
    if (!length || length >= executable.size()) {
        SKSE::log::error("Cannot locate the game executable for Address Library preflight");
        return false;
    }
    const auto library = std::filesystem::path(executable.data()).parent_path() / "Data/SKSE/Plugins" /
        std::format("versionlib-{}-{}-{}-{}.bin",version.major(),version.minor(),version.patch(),version.build());
    const auto check = anima::CheckAddressLibrary(library,{version.major(),version.minor(),version.patch(),version.build()});
    if (check != anima::LibraryCheck::ready) {
        SKSE::log::error("Address Library preflight failed ({}) for {}",static_cast<int>(check),library.string());
        return false;
    }
    SKSE::Init(skse);
    SKSE::GetPapyrusInterface()->Register(RegisterPapyrus);
    SKSE::log::info("AnimaConnector 0.5.6 loading on Skyrim {}; Address Library format {}", version.string(),
                   anima::AddressLibraryFormat({version.major(),version.minor(),version.patch(),version.build()}));
    return SKSE::GetMessagingInterface()->RegisterListener([](SKSE::MessagingInterface::Message* message) {
        if (message->type == SKSE::MessagingInterface::kDataLoaded) Connector::Get().Initialize();
        else if (message->type == SKSE::MessagingInterface::kPostLoadGame) Connector::Get().LogQuestState("post_load_game");
        else if (message->type == SKSE::MessagingInterface::kPreLoadGame || message->type == SKSE::MessagingInterface::kNewGame) Connector::Get().ResetEpoch();
    });
}
