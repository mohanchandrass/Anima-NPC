import importlib.util
import struct
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    import sys
    sys.path.insert(0, str(SCRIPTS))
    spec.loader.exec_module(module)
    return module


build = load("build_anima_esp")
check = load("check_anima_esp")


def test_seq_lists_the_start_game_enabled_quest():
    assert build.build_seq() == struct.pack("<I", build.QUEST_ID)


def test_talk_line_has_no_conditions_and_is_a_visible_goodbye_topic(tmp_path):
    esp = tmp_path / "Anima.esp"
    esp.write_bytes(build.build_esp())
    plugin = check.Plugin(esp, "AnimaTalkTopic")
    assert plugin.quest.sub("DNAM")[0] == struct.pack("<HBBII", 0x0011, 0, 0xFF, 0, 6)  # Start Game Enabled, same as Mantella's quest
    assert plugin.branch.sub("DNAM")[0] == struct.pack("<I", 1) and plugin.branch.sub("SNAM")[0] == struct.pack("<I", plugin.topic.form_id)
    assert plugin.topic.sub("DATA")[0] == b"\0\0\0\0" and plugin.topic.sub("SNAM")[0] == b"CUST"
    assert len(plugin.infos) == 1 and plugin.infos[0].sub("ENAM")[0] == struct.pack("<HH", 0x0201, 0)
    assert not plugin.infos[0].sub("CTDA") and not plugin.quest.sub("CTDA")  # conditions can only hide the line
    assert plugin.header.flags == 0 and [m for m in plugin.header.subrecords if m[0] == "MAST"][0][1] == b"Skyrim.esm\0"


def test_check_script_reports_without_mantella(tmp_path, capsys, monkeypatch):
    esp = tmp_path / "Anima.esp"
    esp.write_bytes(build.build_esp())
    (tmp_path / "Seq").mkdir()
    (tmp_path / "Seq" / "Anima.seq").write_bytes(build.build_seq())
    monkeypatch.setattr("sys.argv", ["check_anima_esp.py", "--anima", str(esp), "--mantella", str(tmp_path / "missing.esp")])
    check.main()
    out = capsys.readouterr().out
    assert "Mantella not found" in out and "contains the quest" in out and "info0" in out
