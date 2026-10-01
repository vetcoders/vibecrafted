from __future__ import annotations

import stat
from pathlib import Path

import pytest
from vibecrafted_core.server_config import (
    CopilotProviderConfig,
    HousekeepingConfig,
    ServerConfig,
    ServerConfigError,
    copilot_provider_env_lines,
    load_copilot_provider_config,
    load_housekeeping_config,
    load_server_config,
    load_tool_destinations,
    seed_server_config,
)


def test_server_config_defaults_when_file_is_absent(tmp_path: Path) -> None:
    config = load_server_config(tmp_path / "missing.toml")

    assert config.bind_host == "127.0.0.1"
    assert config.port == 3024
    assert config.public_url == "http://127.0.0.1:3024"


def test_seed_server_config_preserves_other_tables_and_existing_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[runtime]\nhorse = "wezterm"\n', encoding="utf-8")
    path.chmod(0o640)
    tailnet = ServerConfig(
        bind_host="100.82.232.70",
        port=3025,
        public_url="http://100.82.232.70:3025",
    )

    seeded, created = seed_server_config(tailnet, path)
    retained, replaced = seed_server_config(ServerConfig(), path)

    assert created
    assert not replaced
    assert seeded == tailnet
    assert retained == tailnet
    assert '[runtime]\nhorse = "wezterm"' in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_server_config_keeps_bind_and_public_origin_distinct(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[server]\n"
        'bind_host = "0.0.0.0"\n'
        "port = 3025\n"
        'public_url = "https://observer.tailnet.example"\n',
        encoding="utf-8",
    )

    config = load_server_config(path)

    assert config.bind_addr == "0.0.0.0:3025"
    assert config.public_url == "https://observer.tailnet.example"


@pytest.mark.parametrize(
    "body, message",
    [
        ("[server]\nport = 0\n", "between 1 and 65535"),
        ("[server]\nport = true\n", "must be an integer"),
        (
            '[server]\npublic_url = "http://user:secret@example.com/path"\n',
            "must be an HTTP",
        ),
        ("[server]\nunknown = 1\n", r"unsupported \[server\] key"),
    ],
)
def test_server_config_rejects_invalid_contract(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ServerConfigError, match=message):
        load_server_config(path)


def test_tool_destinations_are_optional_and_share_the_config_owner(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    assert load_tool_destinations(path) == {}
    path.write_text("[server]\nport = 3025\n", encoding="utf-8")
    assert load_tool_destinations(path) == {}
    assert load_server_config(path).port == 3025

    path.write_text(
        "[server]\nport = 3025\n\n"
        '[tools.slack-console]\nurl = "http://100.82.232.70:4300/console"\n',
        encoding="utf-8",
    )
    assert load_tool_destinations(path) == {
        "slack-console": "http://100.82.232.70:4300/console"
    }
    # The [server] owner is unaffected by the sibling table.
    assert load_server_config(path).port == 3025

    path.write_text(
        "[server]\nport = 3025\n\n"
        '[tools.slack-console]\nurl = "http://100.82.232.70:4300/console"\n\n'
        '[tools.vc-frame]\nurl = "http://127.0.0.1:8082/"\n',
        encoding="utf-8",
    )
    assert load_tool_destinations(path) == {
        "slack-console": "http://100.82.232.70:4300/console",
        "vc-frame": "http://127.0.0.1:8082/",
    }

    # An explicitly empty url reads as "not configured", not as an error.
    path.write_text('[tools.slack-console]\nurl = ""\n', encoding="utf-8")
    assert load_tool_destinations(path) == {}


@pytest.mark.parametrize(
    "body, message",
    [
        (
            "[tools]\nslack-console = 1\n",
            r"\[tools.slack-console\] must be a TOML table",
        ),
        ('[tools.portal]\nurl = "http://x/"\n', r"unsupported \[tools\] key"),
        ('[tools.slack-console]\nurl = "ftp://x/console"\n', "must be an HTTP"),
        ('[tools.slack-console]\nurl = "http://u:p@x/console"\n', "must be an HTTP"),
        (
            '[tools.slack-console]\nurl = "http://x/console?token=1"\n',
            "must be an HTTP",
        ),
        (
            '[tools.slack-console]\nurl = "http://x/console"\nport = 4300\n',
            r"unsupported \[tools.slack-console\] key",
        ),
        ("tools = 3\n", r"\[tools\] must be a TOML table"),
    ],
)
def test_tool_destinations_reject_invalid_contract(
    tmp_path: Path, body: str, message: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ServerConfigError, match=message):
        load_tool_destinations(path)


def test_housekeeping_config_defaults_to_disabled_when_file_is_absent(
    tmp_path: Path,
) -> None:
    config = load_housekeeping_config(tmp_path / "missing.toml")

    assert config == HousekeepingConfig()
    assert config.enabled is False
    assert config.auto_execute is False
    assert config.retention_days == 7
    assert config.interval_hours == 24


def test_housekeeping_config_is_the_founders_own_opt_in(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[housekeeping]\nenabled = true\nretention_days = 14\ninterval_hours = 12\n",
        encoding="utf-8",
    )

    config = load_housekeeping_config(path)

    assert config.enabled is True
    # Scheduling is opted into, but deletion is not -- a second, separate
    # opt-in is required before the schedule may ever delete anything.
    assert config.auto_execute is False
    assert config.retention_days == 14
    assert config.interval_hours == 12


def test_housekeeping_config_auto_execute_requires_its_own_explicit_flag(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[housekeeping]\nenabled = true\nauto_execute = true\n", encoding="utf-8"
    )

    config = load_housekeeping_config(path)

    assert config.enabled is True
    assert config.auto_execute is True


def test_housekeeping_config_is_independent_of_the_server_table(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[server]\nport = 3025\n\n[housekeeping]\nenabled = true\n",
        encoding="utf-8",
    )

    assert load_housekeeping_config(path).enabled is True
    assert load_server_config(path).port == 3025


@pytest.mark.parametrize(
    "body, message",
    [
        ("[housekeeping]\nenabled = 1\n", "housekeeping.enabled must be a boolean"),
        (
            "[housekeeping]\nauto_execute = 1\n",
            "housekeeping.auto_execute must be a boolean",
        ),
        (
            "[housekeeping]\nretention_days = 0\n",
            "housekeeping.retention_days must be at least 1",
        ),
        (
            "[housekeeping]\nretention_days = true\n",
            "housekeeping.retention_days must be an integer",
        ),
        (
            "[housekeeping]\ninterval_hours = -1\n",
            "housekeeping.interval_hours must be at least 1",
        ),
        (
            "[housekeeping]\nunknown = 1\n",
            r"unsupported \[housekeeping\] key",
        ),
        ("housekeeping = 3\n", r"\[housekeeping\] must be a TOML table"),
    ],
)
def test_housekeeping_config_rejects_invalid_contract(
    tmp_path: Path, body: str, message: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ServerConfigError, match=message):
        load_housekeeping_config(path)


def test_copilot_provider_config_defaults_unconfigured_when_file_is_absent(
    tmp_path: Path,
) -> None:
    config = load_copilot_provider_config(tmp_path / "missing.toml")

    assert config == CopilotProviderConfig()
    assert config.configured is False
    assert config.env() == {}


def test_copilot_provider_config_is_the_ollama_byok_example(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[agents.copilot.provider]\n"
        'base_url = "http://localhost:11434/v1"\n'
        'model = "kimi-k3:cloud"\n',
        encoding="utf-8",
    )

    config = load_copilot_provider_config(path)

    assert config.configured is True
    assert config.base_url == "http://localhost:11434/v1"
    assert config.model == "kimi-k3:cloud"
    assert config.env() == {
        "COPILOT_PROVIDER_BASE_URL": "http://localhost:11434/v1",
        "COPILOT_MODEL": "kimi-k3:cloud",
    }


def test_copilot_provider_config_is_independent_of_the_server_table(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[server]\nport = 3025\n\n"
        "[agents.copilot.provider]\n"
        'base_url = "http://localhost:11434/v1"\n',
        encoding="utf-8",
    )

    assert load_copilot_provider_config(path).configured is True
    assert load_server_config(path).port == 3025


def test_copilot_provider_config_renders_every_documented_field(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[agents.copilot.provider]\n"
        'base_url = "https://example.test/v1"\n'
        'model = "custom-model"\n'
        'type = "openai"\n'
        'wire_api = "responses"\n'
        'transport = "http"\n'
        'api_key = "sk-secret"\n'
        'api_key_command = "op read secret"\n'
        'bearer_token = "bearer-secret"\n'
        'headers = "Authorization: Bearer xyz"\n'
        'model_id = "custom-model-id"\n'
        'wire_model = "custom-wire-model"\n'
        "max_prompt_tokens = 128000\n"
        "max_output_tokens = 8192\n",
        encoding="utf-8",
    )

    config = load_copilot_provider_config(path)

    assert config.env() == {
        "COPILOT_PROVIDER_BASE_URL": "https://example.test/v1",
        "COPILOT_MODEL": "custom-model",
        "COPILOT_PROVIDER_TYPE": "openai",
        "COPILOT_PROVIDER_WIRE_API": "responses",
        "COPILOT_PROVIDER_TRANSPORT": "http",
        "COPILOT_PROVIDER_API_KEY": "sk-secret",
        "COPILOT_PROVIDER_API_KEY_COMMAND": "op read secret",
        "COPILOT_PROVIDER_BEARER_TOKEN": "bearer-secret",
        "COPILOT_PROVIDER_HEADERS": "Authorization: Bearer xyz",
        "COPILOT_PROVIDER_MODEL_ID": "custom-model-id",
        "COPILOT_PROVIDER_WIRE_MODEL": "custom-wire-model",
        "COPILOT_PROVIDER_MAX_PROMPT_TOKENS": "128000",
        "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS": "8192",
    }


@pytest.mark.parametrize(
    "body, message",
    [
        (
            '[agents.copilot.provider]\nmodel = "kimi-k3:cloud"\n',
            "base_url is required",
        ),
        (
            '[agents.copilot.provider]\nbase_url = "http://x/v1"\nbogus = 1\n',
            r"unsupported \[agents.copilot.provider\] key",
        ),
        (
            "[agents.copilot.provider]\nbase_url = 1\n",
            "base_url must be a string",
        ),
        (
            (
                '[agents.copilot.provider]\nbase_url = "http://x/v1"\n'
                "max_prompt_tokens = true\n"
            ),
            "max_prompt_tokens must be a string or integer",
        ),
        (
            "[agents.copilot]\nprovider = 1\n",
            r"\[agents.copilot.provider\] must be a TOML table",
        ),
        ("[agents]\ncopilot = 1\n", r"\[agents.copilot\] must be a TOML table"),
        ("agents = 1\n", r"\[agents\] must be a TOML table"),
    ],
)
def test_copilot_provider_config_rejects_invalid_contract(
    tmp_path: Path, body: str, message: str
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ServerConfigError, match=message):
        load_copilot_provider_config(path)


def test_copilot_provider_env_lines_let_explicit_export_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOT_PROVIDER_BASE_URL", "http://operator-override/v1")
    config = CopilotProviderConfig(
        base_url="http://localhost:11434/v1", model="kimi-k3:cloud"
    )

    lines = copilot_provider_env_lines(config)

    assert lines == ["export COPILOT_MODEL=kimi-k3:cloud"]


def test_copilot_provider_env_lines_empty_when_unconfigured() -> None:
    assert copilot_provider_env_lines(CopilotProviderConfig()) == []
