"""Resolve a Skyrim voice-type form to its editor ID by reading the VTYP group of a game plugin.

The running game does not expose editor IDs to the plugin (a voice type's in-game name is empty), so the bridge
reads the top-level VTYP group of the plugin that owns the form. Only that group is parsed; every other group is
skipped by seeking. The parser is bounded and rejects malformed plugins.
"""

import re
import struct
import zlib
from pathlib import Path

PLUGIN_NAME = re.compile(r"^[A-Za-z0-9 _.\-()'\[\]]{1,120}\.(?:esm|esp|esl)$", re.IGNORECASE)
MAX_RECORD = 65536
MAX_VOICE_TYPES = 4096


def parse_voice_types(plugin: Path) -> dict[str, str]:
    """Map a record's 24-bit local ID (8 lowercase hex digits) to its lowercase editor ID."""
    result: dict[str, str] = {}
    size = plugin.stat().st_size
    with plugin.open("rb") as stream:
        while stream.tell() < size:
            header = stream.read(24)
            if len(header) != 24:
                raise ValueError("truncated plugin header")
            tag, amount = struct.unpack_from("<4sI", header)
            if tag == b"GRUP":
                if amount < 24 or stream.tell() + amount - 24 > size:
                    raise ValueError("invalid plugin group")
                end = stream.tell() + amount - 24
                if header[8:12] == b"VTYP":
                    while stream.tell() < end:
                        record = stream.read(24)
                        if len(record) != 24:
                            raise ValueError("truncated voice record")
                        kind, length, flags, form = struct.unpack_from("<4sIII", record)
                        if length > MAX_RECORD or stream.tell() + length > end:
                            raise ValueError("voice record exceeds bounds")
                        raw = stream.read(length)
                        if kind != b"VTYP":
                            continue
                        if flags & 0x40000:
                            raw = zlib.decompressobj().decompress(raw[4:], MAX_RECORD + 1)
                            if len(raw) > MAX_RECORD:
                                raise ValueError("expanded voice record exceeds bounds")
                        cursor = 0
                        while cursor + 6 <= len(raw):
                            field, count = struct.unpack_from("<4sH", raw, cursor)
                            cursor += 6
                            if cursor + count > len(raw):
                                raise ValueError("truncated voice field")
                            if field == b"EDID" and len(result) < MAX_VOICE_TYPES:
                                result[f"{form & 0xffffff:08x}"] = raw[cursor:cursor + count].rstrip(b"\0").decode("utf-8").lower()
                            cursor += count
                stream.seek(end)
            else:
                if stream.tell() + amount > size:
                    raise ValueError("invalid plugin record")
                stream.seek(amount, 1)
    return result


class VoiceTypeResolver:
    """Cached lookup of voice-type editor IDs in `<game>/Data/<plugin>`. Never raises; unknown means None."""

    def __init__(self, data_directory: Path | None):
        self.data = data_directory
        self._cache: dict[str, dict[str, str]] = {}

    def resolve(self, plugin: str, local_id: str) -> str | None:
        if self.data is None or not isinstance(plugin, str) or not PLUGIN_NAME.match(plugin):
            return None
        key = plugin.lower()
        if key not in self._cache:
            path = self.data / plugin
            try:
                if not path.is_file():
                    # Skyrim plugin identities are case-insensitive on every host.
                    matches = [entry for entry in self.data.iterdir() if entry.name.lower() == key and entry.is_file()]
                    path = matches[0] if len(matches) == 1 else path
                self._cache[key] = parse_voice_types(path) if path.is_file() else {}
            except (OSError, ValueError, zlib.error, UnicodeError, struct.error):
                self._cache[key] = {}
        return self._cache[key].get(str(local_id).lower())
