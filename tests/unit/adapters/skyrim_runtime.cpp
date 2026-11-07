#include "runtime_support.h"
#include "ui_state.h"
#include "speech_policy.h"
#include "launch_policy.h"
#include "direct_session.h"
#include "conversation_request.h"
#include "audio_state.h"
#include "version_metadata.h"
#include <chrono>
#include <iostream>
int main()
{
    int failures = 0;
    auto check = [&](bool ok,const char* label) { if (!ok) { std::cerr << label << '\n'; ++failures; } };


    check(anima::RequiresResync(409, "unknown or ended session"), "lost bridge session requests resync");
    check(!anima::RequiresResync(409, "turn or playback busy; interrupt playback before speaking again"), "busy is not session loss");
    check(!anima::RequiresResync(409, "unknown delivery"), "late delivery receipt does not reset conversation");
    check(!anima::RequiresResync(500, "unknown or ended session"), "server failure retries without reset");
    anima::AudioState audio;
    audio.Begin(100, 3000);
    check(audio.Update(600, false) == anima::AudioDecision::waiting, "queued Play is not observed start");
    check(audio.Update(2099, false) == anima::AudioDecision::waiting, "bounded decoder startup grace");
    check(audio.Update(2100, false) == anima::AudioDecision::failed, "never-started sound fails truthfully");
    // Normal completion.
    audio.Begin(100, 3000);
    check(audio.Update(200, true) == anima::AudioDecision::started, "observed IsPlaying starts receipt");
    check(audio.Update(300, true) == anima::AudioDecision::playing, "playing sound stays active");
    check(audio.Update(3200, false) == anima::AudioDecision::completed, "observed full playback completes");
    audio.Begin(100, 3000); audio.Update(200, true);
    check(audio.Update(4800, true) == anima::AudioDecision::failed, "unexpected overlong audio is bounded");
    // A brief false reading under 300 ms is ignored, and a later true resets the silence clock.
    audio.Begin(100, 3000); audio.Update(200, true);
    check(audio.Update(800, false) == anima::AudioDecision::playing, "first false reading is not a verdict");
    check(audio.Update(1099, false) == anima::AudioDecision::playing, "false for 299 ms is ignored");
    check(audio.Update(1150, true) == anima::AudioDecision::playing, "recovery after brief false");
    check(audio.Update(1200, false) == anima::AudioDecision::playing, "silence clock restarted");
    check(audio.Update(1499, false) == anima::AudioDecision::playing, "restarted clock still under 300 ms");
    check(audio.Update(1500, false) == anima::AudioDecision::ended_early, "false for 300 ms before the end is ended_early");
    // Sustained false at exactly 300 ms.
    audio.Begin(100, 3000); audio.Update(200, true);
    audio.Update(800, false);
    check(audio.Update(1100, false) == anima::AudioDecision::ended_early, "300 ms of false ends early");
    // False inside the final 150 ms is completion, not early.
    audio.Begin(100, 3000); audio.Update(200, true);
    check(audio.Update(3050, false) == anima::AudioDecision::completed, "false within the tail window completes");
    // Begin resets state for the next delivery.
    audio.Begin(5000, 1000);
    check(!audio.observed && audio.silentSince < 0, "Begin clears previous observation");
    using anima::ChatKeyAction; using anima::DecideChatKey;
    check(DecideChatKey(true, true, false, false) == ChatKeyAction::open_text, "chat key opens text box in dialogue");
    check(DecideChatKey(true, false, false, false) == ChatKeyAction::no_npc, "chat key outside dialogue hints");
    check(DecideChatKey(false, true, false, false) == ChatKeyAction::ignore, "chat key acts on key down only");
    check(DecideChatKey(false, false, false, false) == ChatKeyAction::ignore, "no hint spam on release or hold");
    check(DecideChatKey(true, true, true, false) == ChatKeyAction::ignore, "chat key ignored while CustomMenu open");
    check(DecideChatKey(true, false, true, false) == ChatKeyAction::ignore, "no hint while CustomMenu open");
    check(DecideChatKey(true, true, false, true) == ChatKeyAction::ignore, "chat key ignored while listening");
    anima::UiState ui;
    check(!ui.Open("", anima::Panel::options), "UI rejects empty session");
    check(ui.Open("session-a", anima::Panel::text), "UI text ownership");
    check(!ui.Open("session-b", anima::Panel::options), "UI rejects competing owner");
    check(!ui.CanSubmit("session-b", "HELLO"), "UI rejects stale-session text");
    check(!ui.CanSubmit("session-a", " "), "UI rejects blank text");
    check(!ui.CanSubmit("session-a", std::string(4001, 'x')), "UI bounds text");
    check(ui.CanSubmit("session-a", "HELLO"), "UI accepts owned text");
    auto generation = ui.generation;
    ui.Cancel(); check(ui.generation != generation, "cancel invalidates queued UI generation"); check(!ui.CanSubmit("session-a", "HELLO"), "UI closes ownership");
    check(anima::SupportsRuntime({1,6,318,0}),"retain original AE runtime");
    check(anima::SupportsRuntime({1,7,104,0}),"accept installed AE runtime");
    for (auto v : {anima::RuntimeVersion{1,6,318,1},{1,7,104,1},{1,7,99,0},{1,6,1170,0},{1,4,15,0},{0,0,0,0}})
        check(!anima::SupportsRuntime(v),"reject unverified runtime/build");
    {
        using anima::SuppressAction; anima::SpeechSuppressor sup;
        check(sup.Decide(true,true,false)==SuppressAction::none,"no suppression when vanilla is silent");
        check(sup.Decide(true,false,true)==SuppressAction::none,"vanilla speech allowed without a delivery");
        check(sup.Decide(false,true,true)==SuppressAction::none,"switch off disables suppression");
        check(sup.Decide(true,true,true)==SuppressAction::stop_and_log,"first suppression stops and logs");
        check(sup.Decide(true,true,true)==SuppressAction::stop && sup.Decide(true,true,true)==SuppressAction::stop,"later suppressions stop without logging");
        sup.Begin(); check(sup.Decide(true,true,true)==SuppressAction::stop_and_log,"new delivery logs again");
        check(anima::PollDelayMs(true,0)==25 && anima::PollDelayMs(false,0)==100,"poll 25 ms pending, 100 ms idle");
        check(anima::PollDelayMs(true,1)==250 && anima::PollDelayMs(true,2)==500 && anima::PollDelayMs(false,3)==1000,"errors back off");
        check(anima::PollDelayMs(true,16)==2000 && anima::PollDelayMs(true,1000)==2000,"backoff is bounded at 2 s");
    }
    {
        using anima::DecideLaunch; using anima::LaunchDecision;
        check(DecideLaunch(false,true,true,false,false)==LaunchDecision::disabled,"disabled never launches");
        check(DecideLaunch(false,false,false,true,true)==LaunchDecision::disabled,"disabled wins over every other state");
        check(DecideLaunch(true,false,false,false,false)==LaunchDecision::no_exe_configured,"empty exe path is reported");
        check(DecideLaunch(true,true,false,false,false)==LaunchDecision::missing_exe,"missing exe warns and continues");
        check(DecideLaunch(true,true,true,false,true)==LaunchDecision::already_running,"running bridge is left alone");
        check(DecideLaunch(true,true,true,true,false)==LaunchDecision::already_launched,"never launch twice per session");
        check(DecideLaunch(true,true,true,false,false)==LaunchDecision::launch,"enabled, present and bridge absent launches");
        check(anima::ConsoleCommandLine(L"D:/My Mods/AnimaConsole.exe",4242)==L"\"D:/My Mods/AnimaConsole.exe\" --parent-pid 4242","command line quotes the path and passes the game pid");
    }
    {
        using namespace anima; using A = OpenRequestAction;
        check(DecideOpenRequest(false,0,false,false,true,false)==A::none,"no request does nothing");
        check(DecideOpenRequest(true,100,true,false,true,false)==A::wait,"request waits while the Goodbye menu is still open");
        check(DecideOpenRequest(true,100,true,true,true,false)==A::wait,"menu open never opens the text box early");
        check(DecideOpenRequest(true,500,false,false,true,false)==A::start_direct,"menu closed starts a menu-less session");
        check(DecideOpenRequest(true,500,false,true,true,false)==A::open_text,"existing session for the speaker just opens text");
        check(DecideOpenRequest(true,500,false,false,false,false)==A::wait,"invalid speaker keeps waiting");
        check(DecideOpenRequest(true,kOpenRequestTimeoutMs+1,false,false,true,false)==A::expire,"request expires");
        check(DecideOpenRequest(true,kOpenRequestTimeoutMs,false,false,true,false)==A::start_direct,"request still valid at the timeout boundary");
        check(DecideOpenRequest(true,10,false,true,true,true)==A::expire,"text box already open ends the request");
    }
    {
        using namespace anima;
        check(ValidDirectTarget(true,false,true,false,false,false),"living friendly actor is a valid direct target");
        check(!ValidDirectTarget(false,false,true,false,false,false),"non-actor is not a target");
        check(!ValidDirectTarget(true,true,true,false,false,false),"player is not a target");
        check(!ValidDirectTarget(true,false,false,false,false,false),"actor without base is not a target");
        check(!ValidDirectTarget(true,false,true,true,false,false),"dead actor is not a target");
        check(!ValidDirectTarget(true,false,true,false,true,false),"hostile actor is not a target");
        check(!ValidDirectTarget(true,false,true,false,false,true),"actor in combat is not a target");
        check(DecideDirectEnd(100,false,false,true,0)==DirectEnd::none,"nearby calm session continues");
        check(DecideDirectEnd(600,false,false,true,0)==DirectEnd::none && DecideDirectEnd(601,false,false,true,0)==DirectEnd::distance,"distance limit is 600 units");
        check(DecideDirectEnd(10,true,false,true,0)==DirectEnd::combat,"combat ends direct session");
        check(DecideDirectEnd(10,false,true,true,0)==DirectEnd::dead,"death ends direct session");
        check(DecideDirectEnd(10,false,false,false,0)==DirectEnd::cell_changed,"cell change ends direct session");
        check(DecideDirectEnd(10,false,false,true,60000)==DirectEnd::none && DecideDirectEnd(10,false,false,true,60001)==DirectEnd::idle,"idle limit is 60 s");
        check(std::string(DirectEndName(DirectEnd::distance))=="too_far","end reasons are named");
        check(SessionActive(false,true) && SessionActive(true,false) && !SessionActive(false,false),"menu or direct session is active");
        check(PttStartsDirect(true,false,false,true) && !PttStartsDirect(true,false,false,false) && !PttStartsDirect(true,true,false,true)
              && !PttStartsDirect(true,false,true,true) && !PttStartsDirect(false,false,false,true),"F8 starts direct only with target, no session, no menu");
        check(DecideChatKey(true,false,false,false,true)==ChatKeyAction::start_direct,"chat key starts direct on a valid target");
        check(DecideChatKey(true,false,false,false,false)==ChatKeyAction::no_npc,"chat key without target keeps the talk-to-an-NPC message");
        check(DecideChatKey(true,true,false,false,true)==ChatKeyAction::open_text,"existing session opens the text box");
        check(DecideChatKey(true,false,true,false,true)==ChatKeyAction::ignore && DecideChatKey(false,false,false,false,true)==ChatKeyAction::ignore,"custom menu or key-up ignored");
    }
    check(anima::SayTopicLocalId(1)==0x804 && anima::SayTopicLocalId(2)==0x806 && anima::SayTopicLocalId(0)==0 && anima::SayTopicLocalId(3)==0 && anima::SayTopicLocalId(-1)==0,"say topics map to Anima.esp locals");
    check(anima::DecideSayProof(false,0)==anima::SayProof::waiting && anima::DecideSayProof(false,1999)==anima::SayProof::waiting,"say waits for proof of speech");
    check(anima::DecideSayProof(true,50)==anima::SayProof::started,"say starts only once the actor is speaking");
    check(anima::DecideSayProof(false,2000)==anima::SayProof::failed && anima::DecideSayProof(true,5000)==anima::SayProof::started,"say with no speech after 2 s fails");
    check(!anima::MayStopCurrentDialogue(true) && anima::MayStopCurrentDialogue(false),"an opening never stops the NPC's current dialogue");
    constexpr auto metadata=anima::VersionMetadata();
    check(metadata.pluginVersion==REL::Version{0,5,3,0}.pack(),"plugin version is 0.5.3");
    check(metadata.versionIndependence==0,"legacy loader must not see unknown post-629 flags");
    check(metadata.versionIndependenceEx==SKSE::PluginVersionData::kVersionIndependentEx_AddressLibraryV5,"V5 flag without false NoStructUse claim");
    check(metadata.compatibleVersions[0]==REL::Version{1,6,318,0}.pack(),"legacy export target");
    check(metadata.compatibleVersions[1]==REL::Version{1,7,104,0}.pack() && metadata.compatibleVersions[2]==0,"new export target and terminator");
    namespace fs=std::filesystem;
    const auto root=fs::current_path() / ("anima-runtime-test-"+std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    fs::create_directory(root);
    const auto library=root/"version.bin";
    check(anima::CheckAddressLibrary(library,{1,7,104,0})==anima::LibraryCheck::missing,"missing library fails before engine calls");
    { std::ofstream f(library,std::ios::binary); f.put(2);f.put(0);f.put(0);f.put(0); }
    check(anima::CheckAddressLibrary(library,{1,6,318,0})==anima::LibraryCheck::ready,"legacy format 2 retained");
    check(anima::CheckAddressLibrary(library,{1,7,104,0})==anima::LibraryCheck::wrong_format,"legacy library cannot service new runtime");
    { std::ofstream f(library,std::ios::binary); f.put(5);f.put(0);f.put(0);f.put(0); }
    check(anima::CheckAddressLibrary(library,{1,7,104,0})==anima::LibraryCheck::ready,"format 5 accepted");
    check(anima::CheckAddressLibrary(library,{1,6,318,0})==anima::LibraryCheck::wrong_format,"new library cannot service old runtime");
    { std::ofstream f(library,std::ios::binary); f.put(5); }
    check(anima::CheckAddressLibrary(library,{1,7,104,0})==anima::LibraryCheck::wrong_format,"truncated header rejected");
    check(anima::CheckAddressLibrary(library,{9,9,9,9})==anima::LibraryCheck::unsupported_runtime,"unknown version has no library assumption");
    fs::remove(library); fs::remove(root);
    return failures ? 1 : 0;
}
