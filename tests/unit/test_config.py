from pathlib import Path

import pytest
from pydantic import ValidationError

import atlas_agent.config as config_module
from atlas_agent.config import Settings


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    values = {
        "data_dir": tmp_path / "data",
        "workspace_dir": tmp_path / "workspace",
        "memory_enabled": False,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def test_settings_create_only_owned_directories(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.ensure_directories()

    assert settings.checkpoint_path == tmp_path / "data" / "checkpoints.sqlite"
    assert settings.vector_path == tmp_path / "data" / "memory"
    assert settings.vector_path.is_dir()
    assert settings.sandbox_path.is_dir()
    assert settings.thread_lock_dir.is_dir()
    assert settings.file_lock_dir.is_dir()
    assert settings.workspace_dir.is_dir()


def test_checkpoint_thread_id_is_stable_and_tenant_scoped(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    first = settings.checkpoint_thread_id("alice", "thread-1")

    assert first == settings.checkpoint_thread_id("alice", "thread-1")
    assert first != settings.checkpoint_thread_id("bob", "thread-1")
    assert first != settings.checkpoint_thread_id("alice", "thread-2")
    assert first.startswith("atlas-")
    assert "alice" not in first
    assert settings.thread_lock_path("alice", "thread-1").name == f"{first}.lock"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "gpt-without-provider"),
        ("model", "openai:"),
        ("model", ":gpt-4.1-mini"),
        ("model", "open ai:gpt-4.1-mini"),
        ("model", "openai:gpt 4.1 mini"),
        ("max_agent_iterations", 0),
        ("max_review_cycles", 10),
        ("code_timeout_seconds", 31),
        ("thread_lock_timeout_seconds", 0),
        ("code_execution_backend", "shell"),
    ],
)
def test_invalid_settings_fail_closed(tmp_path: Path, field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        make_settings(tmp_path, **{field: value})


def test_paths_expand_user_without_creating_on_validation(tmp_path: Path) -> None:
    target = tmp_path / "not-created"
    settings = Settings(
        _env_file=None,
        data_dir=target,
        workspace_dir=tmp_path / "workspace",
        memory_enabled=False,
    )

    assert settings.data_dir == target
    assert not target.exists()


def test_openai_configuration_status_checks_presence_without_exposing_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    missing = make_settings(tmp_path)
    assert missing.model_provider == "openai"
    assert missing.model_name == "gpt-4.1-mini"
    assert missing.model_credential_is_configured is False
    assert missing.model_is_configured is False

    monkeypatch.setenv("OPENAI_API_KEY", "test-only-value")
    configured = make_settings(tmp_path)
    assert configured.model_credential_is_configured is True
    assert configured.model_integration_is_available is True
    assert configured.model_is_configured is True
    assert configured.model_api_key is not None
    assert "test-only-value" not in repr(configured)


def test_dotenv_provider_key_is_loaded_for_the_model_client(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=dotenv-test-value\n", encoding="utf-8")

    settings = Settings(
        _env_file=env_file,
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        memory_enabled=False,
    )

    assert settings.model_is_configured is True
    assert settings.model_api_key is not None
    assert settings.model_api_key.get_secret_value() == "dotenv-test-value"


def test_blank_dotenv_credentials_use_keyless_fallbacks(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENAI_API_KEY=\nANTHROPIC_API_KEY=   \nTAVILY_API_KEY=\n",
        encoding="utf-8",
    )

    settings = Settings(
        _env_file=env_file,
        data_dir=tmp_path / "data",
        workspace_dir=tmp_path / "workspace",
        memory_enabled=False,
    )

    assert settings.model_is_configured is False
    assert settings.model_api_key is None
    assert settings.tavily_api_key is None


def test_safe_defaults_require_explicit_code_execution_opt_in(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    assert settings.code_execution_backend == "disabled"


def test_unknown_model_provider_is_not_reported_as_ready(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, model="custom:local-model")

    assert settings.model_provider == "custom"
    assert settings.model_is_configured is False
    assert settings.model_api_key is None


def test_custom_provider_requires_explicit_readiness_opt_in(tmp_path: Path) -> None:
    settings = make_settings(
        tmp_path,
        model="custom:local-model",
        custom_model_configured=True,
    )

    assert settings.model_is_configured is True


def test_anthropic_requires_its_optional_integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        config_module.importlib.util,
        "find_spec",
        lambda module: None if module == "langchain_anthropic" else object(),
    )
    settings = make_settings(
        tmp_path,
        model="anthropic:claude-sonnet-4-5",
        ANTHROPIC_API_KEY="test-only-value",
    )

    assert settings.model_credential_is_configured is True
    assert settings.model_integration_is_available is False
    assert settings.model_is_configured is False


def test_model_setup_action_points_to_the_selected_provider_key(
    tmp_path: Path,
) -> None:
    openai = make_settings(
        tmp_path,
        model="openai:gpt-4.1-mini",
        OPENAI_API_KEY="",
        TAVILY_API_KEY="setup-action-secret",
    )
    anthropic = make_settings(
        tmp_path,
        model="anthropic:claude-sonnet-4-5",
        ANTHROPIC_API_KEY="",
        TAVILY_API_KEY="setup-action-secret",
    )

    assert openai.model_setup_action == "Add OPENAI_API_KEY to .env, then restart Atlas."
    assert anthropic.model_setup_action == "Add ANTHROPIC_API_KEY to .env, then restart Atlas."
    assert "setup-action-secret" not in openai.model_setup_action
    assert "setup-action-secret" not in anthropic.model_setup_action


def test_model_setup_action_explains_a_missing_anthropic_integration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        config_module.importlib.util,
        "find_spec",
        lambda module: None if module == "langchain_anthropic" else object(),
    )
    settings = make_settings(
        tmp_path,
        model="anthropic:claude-sonnet-4-5",
        ANTHROPIC_API_KEY="integration-test-secret",
    )

    assert settings.model_setup_action == (
        "Install the Anthropic integration with uv sync --locked --extra anthropic."
    )
    assert "integration-test-secret" not in settings.model_setup_action


def test_model_setup_action_explains_custom_provider_opt_in(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, model="custom:local-model")

    assert settings.model_setup_action == (
        "Configure the 'custom' integration, then set ATLAS_CUSTOM_MODEL_CONFIGURED=true."
    )


def test_model_setup_action_is_none_when_the_selected_provider_is_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module.importlib.util, "find_spec", lambda module: object())
    openai = make_settings(
        tmp_path,
        model="openai:gpt-4.1-mini",
        OPENAI_API_KEY="configured-test-secret",
    )
    custom = make_settings(
        tmp_path,
        model="fixture:deterministic",
        custom_model_configured=True,
    )

    assert openai.model_setup_action is None
    assert custom.model_setup_action is None
