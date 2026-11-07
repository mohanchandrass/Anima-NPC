from pathlib import Path

import pytest

from npc.generation.config import ConfigurationFailure, load_provider
from npc.generation.provider import ChatCompletionsProvider, OfflineProvider


@pytest.mark.parametrize("profile,key_var,host", [
    ("openrouter", "OPENROUTER_API_KEY", "openrouter.ai/api/v1"),
    ("gemini", "GEMINI_API_KEY", "generativelanguage.googleapis.com/v1beta/openai"),
])
def test_public_profiles_use_local_secrets_and_models(profile, key_var, host):
    provider = load_provider(Path(f"configs/providers/{profile}.yaml"), environ={key_var: "PRIVATE_KEY", "NPC_LLM_MODEL": "chosen-model"})
    assert isinstance(provider, ChatCompletionsProvider)
    assert provider.config.model == "chosen-model"
    assert host in provider.config.base_url
    assert "PRIVATE_KEY" not in repr(provider.config)


def test_offline_profile_requires_no_credentials():
    assert isinstance(load_provider(Path("configs/providers/offline.yaml"), environ={}), OfflineProvider)


def test_missing_config_is_actionable_without_secret_values():
    with pytest.raises(ConfigurationFailure, match="NPC_LLM_MODEL"):
        load_provider(Path("configs/providers/gemini.yaml"), environ={})


def test_profiles_reject_embedded_secret_fields(tmp_path):
    path = tmp_path/"bad.yaml"
    path.write_text("provider: chat_completions\napi_key: PRIVATE_KEY")
    with pytest.raises(ConfigurationFailure) as error:
        load_provider(path, environ={})
    assert "PRIVATE_KEY" not in str(error.value)


def test_environment_overrides_profile_and_generic_key_is_supported():
    provider = load_provider(Path("configs/providers/openrouter.yaml"), environ={
        "NPC_LLM_BASE_URL": "http://127.0.0.1:1234/v1", "NPC_LLM_MODEL": "local-model", "NPC_LLM_API_KEY": "placeholder",
        "NPC_LLM_RESPONSE_FORMAT": "none",
    })
    assert provider.config.base_url == "http://127.0.0.1:1234/v1"
    assert provider.config.response_format == "none"


def test_gemini_reasoning_profile_and_override_are_validated():
    settings = {"GEMINI_API_KEY": "PRIVATE_KEY", "NPC_LLM_MODEL": "test-model"}
    path = Path("configs/providers/gemini.yaml")
    assert load_provider(path, environ=settings).config.reasoning_effort == "minimal"
    assert load_provider(path, environ={**settings, "NPC_LLM_REASONING_EFFORT": "low"}).config.reasoning_effort == "low"
    with pytest.raises(ConfigurationFailure):
        load_provider(path, environ={**settings, "NPC_LLM_REASONING_EFFORT": "invalid"})


def test_openrouter_reserves_short_output_allowance_for_answer_text():
    provider = load_provider(Path("configs/providers/openrouter.yaml"), environ={
        "OPENROUTER_API_KEY": "PRIVATE_KEY", "NPC_LLM_MODEL": "nvidia/nemotron-3.5-lightning:free"})
    assert provider.config.reasoning_enabled is False
    assert provider.config.reasoning_effort is None
    gemini = load_provider(Path("configs/providers/gemini.yaml"), environ={
        "GEMINI_API_KEY": "PRIVATE_KEY", "NPC_LLM_MODEL": "gemini-test"})
    assert gemini.config.reasoning_enabled is None
    assert gemini.config.reasoning_effort == "minimal"


def test_reasoning_profile_toggle_requires_a_boolean(tmp_path):
    path = tmp_path / "provider.yaml"
    path.write_text('provider: chat_completions\nbase_url: https://example.test/v1\nreasoning_enabled: "false"\n')
    with pytest.raises(ConfigurationFailure):
        load_provider(path, environ={"NPC_LLM_API_KEY": "PRIVATE", "NPC_LLM_MODEL": "test"})


def test_strict_schema_is_an_explicit_profile_option(tmp_path):
    path = tmp_path / "strict.yaml"
    path.write_text('provider: chat_completions\nbase_url: https://example.test/v1\nresponse_format: json_schema\n')
    provider = load_provider(path, environ={"NPC_LLM_MODEL": "test", "NPC_LLM_API_KEY": "nonsecret"})
    assert provider.config.response_format == "json_schema"
