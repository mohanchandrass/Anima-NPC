#pragma once
#include <cstddef>
#include <string>
#include <string_view>
#include <vector>
namespace anima {
inline constexpr std::size_t kCaptionWidth = 70;
inline constexpr std::size_t kCaptionMaxLines = 3;

// Collapses runs of whitespace (including newlines) to one space and trims both ends.
inline std::string CollapseSpaces(std::string_view text) {
    std::string out;
    bool pendingSpace = false;
    for (char c : text) {
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') { pendingSpace = !out.empty(); continue; }
        if (pendingSpace) { out += ' '; pendingSpace = false; }
        out += c;
    }
    return out;
}
// Never split a UTF-8 sequence: move the cut back to a character start.
inline std::size_t Utf8Boundary(const std::string& text, std::size_t cut) {
    while (cut > 0 && cut < text.size() && (static_cast<unsigned char>(text[cut]) & 0xC0) == 0x80) --cut;
    return cut;
}
// On-screen message lines: "Name: text", broken at spaces into at most kCaptionMaxLines lines of about kCaptionWidth
// characters. Longer text is cut at a word boundary and ends with "...". Empty text gives no lines.
inline std::vector<std::string> CaptionLines(std::string_view name, std::string_view text) {
    std::vector<std::string> lines;
    const auto body = CollapseSpaces(text);
    if (body.empty()) return lines;
    const auto who = CollapseSpaces(name);
    std::string rest = who.empty() ? body : who + ": " + body;
    while (!rest.empty()) {
        const bool last = lines.size() + 1 == kCaptionMaxLines;
        if (rest.size() <= kCaptionWidth) { lines.push_back(rest); break; }
        const std::size_t limit = last ? kCaptionWidth - 3 : kCaptionWidth;
        auto cut = rest.rfind(' ', limit);
        if (cut == std::string::npos || cut < limit / 2) cut = Utf8Boundary(rest, limit);
        auto line = rest.substr(0, cut);
        while (!line.empty() && line.back() == ' ') line.pop_back();
        if (last) { lines.push_back(line + "..."); break; }
        lines.push_back(line);
        rest.erase(0, cut);
        while (!rest.empty() && rest.front() == ' ') rest.erase(0, 1);
    }
    return lines;
}

// EngineSubtitle=1 (default): the engine subtitle is the caption. EngineSubtitle=0: the on-screen message is the only
// caption. CaptionFallback=1 (default 0) adds the message to the engine subtitle. A missing SubtitleManager also
// falls back to the message so the reply is never invisible. The message is otherwise never shown.
inline bool ShowCaptionMessage(bool engineSubtitle, bool captionFallback, bool managerAvailable) {
    return !engineSubtitle || captionFallback || !managerAvailable;
}
}
