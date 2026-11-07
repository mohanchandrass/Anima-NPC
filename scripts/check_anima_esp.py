"""Dump the generated Anima.esp next to Mantella's start line and list every difference that could change visibility.

    .venv/Scripts/python.exe scripts/check_anima_esp.py
    .venv/Scripts/python.exe scripts/check_anima_esp.py --anima output/skyrim-mod/Anima.esp --mantella D:/MO2/mods/Mantella/Mantella.esp

Read-only. Mantella is optional: without it only Anima.esp is shown. Pairs by role: quest, branch, topic and the INFOs
of the topic ("Talk with me." against MantellaConversationControlTopic). Form IDs that point at a paired record are
shown as <role>, so only real differences remain. Expected differences (names, ids, scripts, text) are marked
"by design"; everything else is marked DIFF.
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dump_esp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ANIMA = ROOT / "output/skyrim-mod/Anima.esp"
DEFAULT_MANTELLA = Path("D:/MO2/mods/Mantella/Mantella.esp")
BY_DESIGN = {"EDID", "FULL", "RNAM", "VMAD", "FLTR", "NAM1"}
COMPARE_OPS = {0: "==", 1: "!=", 2: ">", 3: ">=", 4: "<", 5: "<="}
FUNCTIONS = {629: "GetVMQuestVariable", 56: "GetQuestRunning", 71: "GetInFaction"}


def records(items):
    found = {}
    for item in items:
        if isinstance(item, dump_esp.Group):
            found.update(records(item.children))
        else:
            found[item.form_id] = item
    return found


def info_records(items, topic_id):
    """INFO records inside the type-7 group labelled with this topic."""
    for item in items:
        if isinstance(item, dump_esp.Group):
            if item.group_type == 7 and struct.unpack("<I", item.label)[0] == topic_id:
                return [c for c in item.children if not isinstance(c, dump_esp.Group)]
            found = info_records(item.children, topic_id)
            if found:
                return found
    return []


class Plugin:
    def __init__(self, path: Path, topic_edid: str):
        self.path = path
        self.items = dump_esp.parse_esp(path.read_bytes())
        self.header = self.items[0]
        self.records = records(self.items[1:])
        self.topic = next(r for r in self.records.values() if r.type == "DIAL" and self.edid(r) == topic_edid)
        self.quest = self.records[struct.unpack("<I", self.topic.sub("QNAM")[0])[0]]
        self.branch = self.records[struct.unpack("<I", self.topic.sub("BNAM")[0])[0]]
        self.infos = info_records(self.items[1:], self.topic.form_id)
        self.roles = {self.quest.form_id: "<quest>", self.branch.form_id: "<branch>", self.topic.form_id: "<topic>"}
        for number, info in enumerate(self.infos):
            self.roles[info.form_id] = f"<info{number}>"

    @staticmethod
    def edid(record) -> str:
        return dump_esp.cstring(record.sub("EDID")[0]) if record.sub("EDID") else ""

    def render(self, kind: str, data: bytes) -> str:
        """Subrecord text with paired form IDs replaced by role names and known layouts decoded."""
        if kind == "DNAM" and len(data) == 12:
            flags, priority, version, _, quest_type = struct.unpack("<HBBII", data)
            return f"quest flags={flags:#06x} priority={priority} formver={version:#x} type={quest_type}"
        if kind == "ENAM" and len(data) == 4:
            flags, reset = struct.unpack("<HH", data)
            return f"info flags={flags:#06x} reset_hours={reset}"
        if kind == "CTDA" and len(data) >= 28:
            op, compare, function, p1, p2, run_on, reference = struct.unpack_from("<B3xfH2xIIII", data)
            return (f"{FUNCTIONS.get(function, f'func{function}')}({p1:#010x}, {p2:#x}) {COMPARE_OPS.get(op >> 5, '?')} {compare:g}"
                    f" [type={op:#04x} run_on={run_on} ref={reference:#x}]")
        if len(data) == 4 and kind in {"QNAM", "BNAM", "SNAM", "TNAM", "TPIC", "PNAM"}:
            value = struct.unpack("<I", data)[0]
            if value in self.roles:
                return self.roles[value]
        text = dump_esp.cstring(data) if kind in {"EDID", "FULL", "RNAM", "FLTR", "NAM1"} and data[-1:] == b"\0" else ""
        return data[:40].hex() + (f" {text!r}" if text else "")


def compare_record(role: str, a: Plugin, ra, b: Plugin | None, rb) -> list[str]:
    lines = [f"== {role}: {ra.type} {ra.form_id:08X}" + (f"  vs  {rb.type} {rb.form_id:08X}" if rb else "")]
    if rb is None:
        lines += [f"   {kind:5} {a.render(kind, data)}" for kind, data in ra.subrecords]
        return lines
    for field, left, right in (("record flags", ra.flags, rb.flags), ("form version", ra.version, rb.version)):
        lines.append(f"   {field}: {left:#x} vs {right:#x}  {'same' if left == right else 'DIFF'}")
    kinds = list(dict.fromkeys([k for k, _ in ra.subrecords] + [k for k, _ in rb.subrecords]))
    for kind in kinds:
        left = [a.render(kind, d) for k, d in ra.subrecords if k == kind]
        right = [b.render(kind, d) for k, d in rb.subrecords if k == kind]
        if left == right:
            lines.append(f"   {kind:5} same: {left[0] if left else ''}" + (f" (x{len(left)})" if len(left) > 1 else ""))
            continue
        label = "by design" if kind in BY_DESIGN else "DIFF"
        if not left:
            label = "DIFF (info count, follows the content)" if kind == "TIFC" else "DIFF (Mantella only; Anima has none)"
        elif not right:
            label = "DIFF (Anima only)"
        lines.append(f"   {kind:5} {label}")
        lines += [f"         Anima    : {value}" for value in left]
        lines += [f"         Mantella : {value}" for value in right]
    return lines


def header_lines(a: Plugin, b: Plugin | None) -> list[str]:
    def facts(plugin):
        masters = [dump_esp.cstring(d) for k, d in plugin.header.subrecords if k == "MAST"]
        hedr = struct.unpack("<fII", plugin.header.sub("HEDR")[0])
        return {"TES4 record flags": f"{plugin.header.flags:#x}", "TES4 record version": plugin.header.version,
                "HEDR version": round(hedr[0], 2), "masters": masters, "own form ID prefix": f"{len(masters):02X}"}

    left, right = facts(a), facts(b) if b else {}
    lines = ["== plugin header (flag 0x1 master, 0x200 light; neither is set on either side)"]
    for key, value in left.items():
        other = right.get(key)
        if b:
            mark = "same" if other == value else ("by design (Anima needs only Skyrim.esm)" if key in {"masters", "own form ID prefix"} else "DIFF")
            lines.append(f"   {key}: {value}  vs  {other}  {mark}")
        else:
            lines.append(f"   {key}: {value}")
    return lines


def read_seq(path: Path) -> list[str]:
    return [f"{value:08X}" for value in struct.unpack(f"<{path.stat().st_size // 4}I", path.read_bytes())]


def seq_lines(anima: Path, mantella: Path | None, quest_id: int) -> list[str]:
    lines = ["== SEQ file (lists Start Game Enabled quests; Mantella ships one in Data/Seq)"]
    found = next((p for p in (anima.with_name("Seq") / "Anima.seq", anima.with_name("Anima.seq")) if p.is_file()), None)
    if found:
        ids = read_seq(found)
        lines.append(f"   Anima.seq ({found}): {ids}  {'contains the quest' if f'{quest_id:08X}' in ids else 'DIFF: quest missing'}")
    else:
        lines.append("   Anima.seq: not found next to the plugin (DIFF: the install needs Data/Seq/Anima.seq)")
    if mantella and (mantella.parent / "Seq" / "Mantella.seq").is_file():
        seq = mantella.parent / "Seq" / "Mantella.seq"
        lines.append(f"   Mantella.seq ({seq}): {read_seq(seq)}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--anima", type=Path, default=DEFAULT_ANIMA)
    parser.add_argument("--mantella", type=Path, default=DEFAULT_MANTELLA)
    parser.add_argument("--full-dump", action="store_true", help="also print the raw record dump of Anima.esp")
    args = parser.parse_args()
    if not args.anima.is_file():
        parser.exit(1, f"{args.anima} not found; run scripts/build_skyrim_connector.py first\n")
    anima = Plugin(args.anima, "AnimaTalkTopic")
    mantella = Plugin(args.mantella, "MantellaConversationControlTopic") if args.mantella.is_file() else None
    if args.full_dump:
        for item in anima.items:
            print("\n".join(dump_esp.describe(item)))
    if mantella is None:
        print(f"Mantella not found at {args.mantella}: showing Anima.esp only.")
    out = header_lines(anima, mantella)
    pairs = [("quest", anima.quest, mantella.quest if mantella else None), ("branch", anima.branch, mantella.branch if mantella else None),
             ("topic", anima.topic, mantella.topic if mantella else None)]
    for index, info in enumerate(anima.infos):
        pairs.append((f"info{index}", info, mantella.infos[index] if mantella and index < len(mantella.infos) else None))
    for role, left, right in pairs:
        out += compare_record(role, anima, left, mantella, right)
    if mantella:
        for index, info in enumerate(mantella.infos[len(anima.infos):], len(anima.infos)):
            out.append(f"== info{index}: Mantella only ({info.form_id:08X}); Anima has no counterpart")
            out += [f"   {kind:5} {mantella.render(kind, data)}" for kind, data in info.subrecords if kind in {"CTDA", "PNAM", "RNAM"}]
    out += seq_lines(args.anima, args.mantella if mantella else None, anima.quest.form_id)
    print("\n".join(out))
    print("\nConditions can only hide a line: a condition Mantella has and Anima lacks cannot be why Anima's line is missing.")


if __name__ == "__main__":
    main()
