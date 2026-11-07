import pytest

from npc.adapters.skyrim.provider_choice import choose_profile, looks_like_openrouter


@pytest.mark.parametrize("env,expected", [
    ({"NPC_LLM_API_KEY": "sk-or-abc", "NPC_LLM_MODEL": "nvidia/nemotron-3.5-lightning:free"}, True),
    ({"NPC_LLM_API_KEY": "AIzaXYZ", "NPC_LLM_MODEL": "gemini-3.5-flash-lite"}, False),
    ({"OPENROUTER_API_KEY": "x", "NPC_LLM_MODEL": "m"}, True),
    ({"GEMINI_API_KEY": "g", "NPC_LLM_MODEL": "gemini-3.5-flash-lite"}, False),
    ({"NPC_LLM_API_KEY": "k", "NPC_LLM_MODEL": "meta/llama:free"}, True),
    ({"NPC_LLM_API_KEY": "k", "NPC_LLM_MODEL": "gemini-3.5-flash-lite"}, False),
    ({}, False),
])
def test_detects_the_provider(env, expected):
    assert looks_like_openrouter(env) is expected


def test_a_gemini_key_beats_a_slash_in_the_model_name():
    assert looks_like_openrouter({"NPC_LLM_API_KEY": "AIzaK", "NPC_LLM_MODEL": "models/gemini-x"}) is False


def test_auto_picks_next_to_the_requested_path():
    env = {"NPC_LLM_API_KEY": "sk-or-1", "NPC_LLM_MODEL": "a/b"}
    assert choose_profile("configs/providers/auto.yaml", env) == "configs/providers/openrouter.yaml"
    assert choose_profile("configs/providers/auto.yaml", {"NPC_LLM_MODEL": "gemini-x"}) == "configs/providers/gemini.yaml"


def test_explicit_profiles_always_win():
    env = {"NPC_LLM_API_KEY": "sk-or-1", "NPC_LLM_MODEL": "a/b"}
    assert choose_profile("configs/providers/gemini.yaml", env) == "configs/providers/gemini.yaml"
    assert choose_profile("configs/providers/offline.yaml", env) == "configs/providers/offline.yaml"


def test_reads_the_dotenv_when_no_env_is_given(tmp_path, monkeypatch):
    for name in ("OPENROUTER_API_KEY", "GEMINI_API_KEY", "NPC_LLM_API_KEY", "NPC_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text("NPC_LLM_API_KEY=sk-or-test\nNPC_LLM_MODEL=vendor/model:free\n", encoding="utf-8")
    assert choose_profile("configs/providers/auto.yaml", root=tmp_path).endswith("openrouter.yaml")
