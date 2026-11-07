"""Generate Anima.esp: one "Talk with me." player dialogue line that starts an Anima conversation.

Structure copied from Mantella's "MantellaConversationControl" line (top-level branch, custom topic, quest with Start
Game Enabled, Goodbye info without conditions on the speaker, Skyrim's "Of course." shared response, VMAD fragment).
Also two speech topics ("AnimaSpeechLine1"/"2", one unconditional INFO each) that the plugin's `audio_output: "say"`
mode has an actor say, copied from Mantella's MantellaDialogueLine topics. Plain ESP (not ESL), like Mantella, so the
voice file names carry predictable form IDs. Dependency free and deterministic (no timestamps), ids from 0x800.
"""

import argparse
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "output/skyrim-mod/Anima.esp"
MASTER = "Skyrim.esm"
FORM_VERSION = 44
OWN = 0x01000000  # one master, so this plugin's records use load-order index 01 in the file
QUEST_ID, BRANCH_ID, TOPIC_ID, INFO_ID = (OWN | 0x800, OWN | 0x801, OWN | 0x802, OWN | 0x803)
# Speech topics: (topic id, info id, topic editor ID, DATA subtype, SNAM). Subtype/SNAM pairs are Mantella's own.
SPEECH_TOPICS = ((OWN | 0x804, OWN | 0x805, "AnimaSpeechLine1", 0x56, b"STOF"),
                 (OWN | 0x806, OWN | 0x807, "AnimaSpeechLine2", 0x55, b"DEOB"))
NEXT_OBJECT_ID = 0x808
SPEECH_TRDT = bytes.fromhex("000000003200000000000000010000000000000001000000")
SHARED_INFO_OF_COURSE = 0x000DBA22  # Skyrim.esm INFO "OfCourse", the same shared response Mantella uses
PROMPT = "Talk with me."
SCRIPT = "AnimaStartDialogue"
FRAGMENT = "Fragment_0"


def sub(kind, data=b""):
    assert len(data) <= 0xFFFF
    return kind.encode("ascii") + struct.pack("<H", len(data)) + data


def cstr(text):
    return text.encode("utf-8") + b"\0"


def wstr(text):
    raw = text.encode("ascii")
    return struct.pack("<H", len(raw)) + raw


def record(kind, form_id, subrecords, flags=0):
    body = b"".join(subrecords)
    return kind.encode("ascii") + struct.pack("<IIIIHH", len(body), flags, form_id, 0, FORM_VERSION, 0) + body


def group(label, group_type, children):
    body = b"".join(children)
    if isinstance(label, str):
        label = label.encode("ascii")
    return b"GRUP" + struct.pack("<I", 24 + len(body)) + label + struct.pack("<IHHI", group_type, 0, 0, 0) + body


def u32(value):
    return struct.pack("<I", value)


def vmad():
    """Papyrus fragment data: object format 2, script AnimaStartDialogue, begin fragment only."""
    data = struct.pack("<hhH", 5, 2, 1) + wstr(SCRIPT) + struct.pack("<BH", 0, 0)
    data += struct.pack("<BB", 2, 1) + wstr(SCRIPT)  # fragment block: unknown=2, flags=1 (begin), script file name
    data += struct.pack("<B", 1) + wstr(SCRIPT) + wstr(FRAGMENT)
    return data


def build_esp():
    quest = record("QUST", QUEST_ID, [
        sub("EDID", cstr("AnimaDialogue")),
        sub("DNAM", struct.pack("<HBBII", 0x0011, 0, 0xFF, 0, 6)),  # Start Game Enabled + Allow Repeated Stages, type Misc
        sub("FLTR", cstr("Anima\\")),
        sub("NEXT"),
        sub("ANAM", u32(0)),
    ])
    branch = record("DLBR", BRANCH_ID, [
        sub("EDID", cstr("AnimaConversationBranch")),
        sub("QNAM", u32(QUEST_ID)),
        sub("TNAM", u32(0)),
        sub("DNAM", u32(1)),  # top-level branch
        sub("SNAM", u32(TOPIC_ID)),
    ])
    topic = record("DIAL", TOPIC_ID, [
        sub("EDID", cstr("AnimaTalkTopic")),
        sub("FULL", cstr(PROMPT)),
        sub("PNAM", struct.pack("<f", 50.0)),
        sub("BNAM", u32(BRANCH_ID)),
        sub("QNAM", u32(QUEST_ID)),
        sub("DATA", struct.pack("<BBH", 0, 0, 0)),  # category Topic, subtype Custom
        sub("SNAM", b"CUST"),
        sub("TIFC", u32(1)),
    ])
    info = record("INFO", INFO_ID, [
        sub("VMAD", vmad()),
        sub("ENAM", struct.pack("<HH", 0x0201, 0)),  # Goodbye (closes the dialogue menu) + Force Subtitle
        sub("PNAM", u32(0)),
        sub("CNAM", b"\0"),
        sub("DNAM", u32(SHARED_INFO_OF_COURSE)),
        sub("RNAM", cstr(PROMPT)),
    ])
    dial = [topic, group(struct.pack("<I", TOPIC_ID), 7, [info])]
    for topic_id, info_id, editor_id, subtype, snam in SPEECH_TOPICS:
        dial.append(record("DIAL", topic_id, [
            sub("EDID", cstr(editor_id)),
            sub("PNAM", struct.pack("<f", 50.0)),
            sub("QNAM", u32(QUEST_ID)),
            sub("DATA", struct.pack("<BBH", 0, 7, subtype)),  # category Miscellaneous
            sub("SNAM", snam),
            sub("TIFC", u32(1)),
        ]))
        dial.append(group(struct.pack("<I", topic_id), 7, [record("INFO", info_id, [
            sub("ENAM", struct.pack("<HH", 0, 0)),
            sub("TPIC", u32(topic_id)),
            sub("PNAM", u32(0)),
            sub("CNAM", b"\0"),
            sub("TRDT", SPEECH_TRDT),
            sub("NAM1", cstr("...")),
            sub("NAM2", cstr("")),
            sub("NAM3", cstr("")),
        ])]))
    groups = [
        group(b"DIAL", 0, dial),
        group(b"QUST", 0, [quest]),
        group(b"DLBR", 0, [branch]),
    ]
    record_count = 8 + 6  # records plus the groups that contain them
    header = record("TES4", 0, [
        sub("HEDR", struct.pack("<fII", 1.7, record_count, NEXT_OBJECT_ID)),
        sub("CNAM", cstr("Anima")),
        sub("MAST", cstr(MASTER)),
        sub("DATA", b"\0" * 8),
        sub("INTV", u32(1)),
    ])
    return header + b"".join(groups)


def build_seq():
    """Data/Seq/Anima.seq: the Start Game Enabled quest IDs, as the Creation Kit writes them (Mantella ships the same
    kind of file). Without it the engine may never start the quest in an existing save, and a dialogue line of a quest
    that is not running is never shown."""
    return u32(QUEST_ID)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(build_esp())
    (args.output.parent / "Seq").mkdir(exist_ok=True)
    (args.output.parent / "Seq" / "Anima.seq").write_bytes(build_seq())
    print(args.output)


if __name__ == "__main__":
    main()
