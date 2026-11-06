"""Consumer-visible contracts for explicit native MCP profiles (no provider calls)."""

from __future__ import annotations

import hashlib
import json
import stat
import tomllib
from pathlib import Path

import pytest
from vibecrafted_core.mcp_isolation import prepare_isolated_mcp, validate_mcp_config

_SECRET = "private-worker-token-never-in-argv"


def _profile(path: Path, servers: dict | None = None) -> Path:
    path.write_text(
        json.dumps(
            {
                "mcpServers": servers
                if servers is not None
                else {
                    "private-mock": {
                        "type": "http",
                        "url": "https://mock.example/mcp",
                        "headers": {"Authorization": "Bearer " + _SECRET},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _prepare(
    tmp_path: Path,
    agent: str,
    *,
    environment: dict | None = None,
    command: list | None = None,
):
    profile = _profile(tmp_path / "input.json")
    home = tmp_path / "real-home"
    home.mkdir(exist_ok=True)
    return prepare_isolated_mcp(
        agent,
        command
        or [agent, "exec" if agent == "codex" else "-p", "--model", "pinned-model"],
        profile,
        run_dir=tmp_path / "run",
        environment=environment or {"HOME": str(home)},
    )


def test_claude_uses_native_strict_registry_without_changing_auth_or_home(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    inherited = home / ".claude.json"
    inherited.write_text(
        json.dumps(
            {"mcpServers": {"official-course": {"url": "https://official.example"}}}
        )
    )
    environment = {
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / "custom-claude"),
        "ANTHROPIC_API_KEY": "provider-only-auth",
    }
    command = [
        "/native/claude",
        "-p",
        "--model",
        "haiku",
        "--permission-mode",
        "bypassPermissions",
    ]
    result = _prepare(tmp_path, "claude", environment=environment, command=command)
    assert result.environment == environment
    assert environment["HOME"] == str(home)
    assert result.command == [
        command[0],
        "--mcp-config",
        result.receipt["config_path"],
        "--strict-mcp-config",
        *command[1:],
    ]
    frozen = json.loads(Path(result.receipt["config_path"]).read_text())
    assert set(frozen["mcpServers"]) == {"private-mock"}
    assert "official-course" in inherited.read_text()
    assert not (Path(result.receipt["profile_dir"]) / ".credentials.json").exists()


def test_codex_excludes_inherited_servers_and_oauth_but_preserves_provider_routing(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    source = home / ".codex"
    source.mkdir(parents=True)
    (source / "config.toml").write_text(
        """model = "pinned-model"
model_provider = "private-provider"
profile = "route"
cli_auth_credentials_store = "keyring"
[mcp_servers.official-course]
url = "https://official.example/mcp"
[model_providers.private-provider]
name = "Private provider"
base_url = "https://provider.example/v1"
env_key = "PROVIDER_API_KEY"
[profiles.route]
model_reasoning_effort = "high"
[profiles.route.mcp_servers.sneaky]
url = "https://inherited.example"
""",
        encoding="utf-8",
    )
    auth = source / "auth.json"
    auth.write_text('{"OPENAI_API_KEY":"provider-only-auth"}')
    (source / ".credentials.json").write_text('{"mcp-oauth":"must-not-inherit"}')
    env = {
        "HOME": str(home),
        "CODEX_HOME": str(source),
        "PROVIDER_API_KEY": "provider-only-auth",
    }
    command = ["codex", "exec", "--json", "--model", "override-model", "-"]
    result = _prepare(tmp_path, "codex", environment=env, command=command)
    isolated = Path(result.environment["CODEX_HOME"])
    config = tomllib.loads((isolated / "config.toml").read_text())
    assert set(config["mcp_servers"]) == {"private-mock"}
    assert (
        config["mcp_servers"]["private-mock"]["http_headers"]["Authorization"]
        == "Bearer " + _SECRET
    )
    assert config["model"] == "pinned-model"
    assert config["model_provider"] == "private-provider"
    assert (
        config["model_providers"]["private-provider"]["env_key"] == "PROVIDER_API_KEY"
    )
    assert config["profiles"]["route"] == {"model_reasoning_effort": "high"}
    assert config["cli_auth_credentials_store"] == "file"
    assert result.command == command
    assert result.environment["HOME"] == env["HOME"]
    assert result.environment["PROVIDER_API_KEY"] == env["PROVIDER_API_KEY"]
    assert env["CODEX_HOME"] == str(source)
    assert (isolated / "auth.json").is_symlink()
    assert (isolated / "auth.json").resolve() == auth
    assert not (isolated / ".credentials.json").exists()
    assert "official-course" in (source / "config.toml").read_text()


def test_agy_native_gemini_dir_excludes_registry_without_home_or_auth_copy(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    source = home / ".gemini"
    (source / "config").mkdir(parents=True)
    inherited = source / "config" / "mcp_config.json"
    inherited.write_text(
        '{"mcpServers":{"official-course":{"serverUrl":"https://official.example"}}}'
    )
    (source / "jetski-standalone-oauth-token").write_text("provider-sign-in")
    (source / "mcp-oauth-tokens-v2.json").write_text("must-not-inherit")
    environment = {"HOME": str(home), "OTHER_PROVIDER_AUTH": "unchanged"}
    command = ["agy", "--print=", "--model", "gemini-4-argon-low"]
    result = _prepare(tmp_path, "agy", environment=environment, command=command)
    gemini = Path(result.command[2])
    assert result.command == ["agy", "--gemini_dir", str(gemini), *command[1:]]
    assert result.environment == environment
    native = gemini / "config" / "mcp_config.json"
    payload = json.loads(native.read_text())
    assert set(payload["mcpServers"]) == {"private-mock"}
    assert payload["mcpServers"]["private-mock"] == {
        "serverUrl": "https://mock.example/mcp",
        "headers": {"Authorization": "Bearer " + _SECRET},
        "disabled": False,
    }
    assert not (gemini / "jetski-standalone-oauth-token").exists()
    assert not (gemini / "mcp-oauth-tokens-v2.json").exists()
    assert "official-course" in inherited.read_text()
    assert stat.S_IMODE(gemini.stat().st_mode) == 0o700
    assert stat.S_IMODE(native.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(native.stat().st_mode) == 0o600


@pytest.mark.parametrize("agent", ["claude", "codex", "agy"])
def test_secret_only_in_private_profile_not_argv_receipt_or_repr(
    tmp_path: Path, agent: str
) -> None:
    result = _prepare(tmp_path, agent)
    assert _SECRET not in json.dumps(result.command)
    assert _SECRET not in json.dumps(result.receipt)
    assert _SECRET not in repr(result)
    config = Path(result.receipt["config_path"])
    assert _SECRET in config.read_text()
    assert result.receipt["server_names"] == ["private-mock"]
    assert result.receipt["sha256"] == hashlib.sha256(config.read_bytes()).hexdigest()
    assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE((config.parent / "receipt.json").stat().st_mode) == 0o600
    if agent == "codex":
        assert (
            stat.S_IMODE(Path(result.environment["CODEX_HOME"]).stat().st_mode) == 0o700
        )
        assert (
            stat.S_IMODE(
                (Path(result.environment["CODEX_HOME"]) / "config.toml").stat().st_mode
            )
            == 0o600
        )


@pytest.mark.parametrize(
    "contents",
    [
        "not-json-private-worker-token-never-in-argv",
        "[]",
        "{}",
        '{"mcpServers":[]}',
        '{"mcpServers":{},"extra":"private-worker-token-never-in-argv"}',
        '{"mcpServers":{},"mcpServers":{}}',
        '{"mcpServers":{"bad.name":{"type":"http","url":"https://mock.example"}}}',
        '{"mcpServers":{"mock":{"type":"stdio","command":"echo"}}}',
        '{"mcpServers":{"mock":{"type":"http","url":"https://mock.example","oauth":{}}}}',
        '{"mcpServers":{"mock":{"type":"http","url":"https://mock.example","headers":{"Authorization":4}}}}',
        '{"mcpServers":{"mock":{"type":"http","url":"https://mock.example","headers":{"X-Test":"${SECRET}"}}}}',
        '{"mcpServers":{"mock":{"type":"http","url":"https://user:private-worker-token-never-in-argv@mock.example"}}}',
    ],
)
def test_malformed_profiles_fail_closed_before_private_files_created(
    tmp_path: Path, contents: str
) -> None:
    profile = tmp_path / "bad.json"
    profile.write_text(contents)
    run = tmp_path / "run"
    with pytest.raises(ValueError) as error:
        prepare_isolated_mcp(
            "codex",
            ["codex", "exec", "-"],
            profile,
            run_dir=run,
            environment={"HOME": str(tmp_path)},
        )
    assert _SECRET not in str(error.value)
    assert not run.exists()


def test_missing_and_unsupported_profiles_have_sanitized_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="readable"):
        validate_mcp_config(tmp_path / (_SECRET + ".json"), agent="claude")
    profile = _profile(tmp_path / "input.json")
    with pytest.raises(ValueError, match="unsupported"):
        validate_mcp_config(profile, agent="junie")


@pytest.mark.parametrize("agent", ["claude", "codex", "agy"])
def test_input_frozen_once_and_continuation_reuses_native_session_home(
    tmp_path: Path, agent: str
) -> None:
    source = _profile(tmp_path / "input.json")
    original = source.read_bytes()
    environment = {"HOME": str(tmp_path / "home")}
    first = prepare_isolated_mcp(
        agent,
        [agent, "exec"],
        source,
        run_dir=tmp_path / "run",
        environment=environment,
    )
    source.write_text("malformed after initial launch")
    again = prepare_isolated_mcp(
        agent,
        [agent, "exec"],
        source,
        run_dir=tmp_path / "run",
        environment=environment,
    )
    assert Path(again.receipt["config_path"]).read_bytes() == original
    assert first.receipt == again.receipt
    if agent == "codex":
        sessions = Path(first.environment["CODEX_HOME"]) / "sessions"
        sessions.mkdir()
        (sessions / "session.jsonl").write_text("native-session-record")
    continuation = prepare_isolated_mcp(
        agent,
        [agent, "resume", "session-id"],
        first.receipt["config_path"],
        run_dir=tmp_path / "continuation",
        environment=environment,
    )
    assert continuation.receipt == first.receipt
    if agent == "codex":
        assert continuation.environment["CODEX_HOME"] == first.environment["CODEX_HOME"]
        assert (
            Path(continuation.environment["CODEX_HOME"]) / "sessions" / "session.jsonl"
        ).read_text() == "native-session-record"


def test_changed_frozen_profile_refused_instead_of_falling_back(tmp_path: Path) -> None:
    first = _prepare(tmp_path, "claude")
    _profile(Path(first.receipt["config_path"]), {})
    with pytest.raises(ValueError, match="does not match"):
        prepare_isolated_mcp(
            "claude",
            ["claude", "-p"],
            first.receipt["config_path"],
            run_dir=tmp_path / "fork",
            environment={"HOME": str(tmp_path)},
        )


def test_native_command_mcp_override_is_not_merged(tmp_path: Path) -> None:
    profile = _profile(tmp_path / "input.json")
    with pytest.raises(ValueError, match="already contains"):
        prepare_isolated_mcp(
            "codex",
            [
                "codex",
                "-c",
                'mcp_servers.official.url="https://official.example"',
                "exec",
            ],
            profile,
            run_dir=tmp_path / "run",
            environment={},
        )
    assert not (tmp_path / "run").exists()


def test_malformed_native_routing_is_not_silently_discarded(tmp_path: Path) -> None:
    home = tmp_path / "home"
    source = home / ".codex"
    source.mkdir(parents=True)
    (source / "config.toml").write_text("bad syntax " + _SECRET)
    with pytest.raises(ValueError, match="routing") as error:
        _prepare(tmp_path, "codex", environment={"HOME": str(home)})
    assert _SECRET not in str(error.value)


def test_keyring_only_codex_auth_is_explicitly_refused_not_silently_lost(
    tmp_path: Path,
) -> None:
    source = tmp_path / "home" / ".codex"
    source.mkdir(parents=True)
    (source / "config.toml").write_text('cli_auth_credentials_store = "keyring"\n')
    with pytest.raises(ValueError, match="file-backed or environment"):
        _prepare(tmp_path, "codex", environment={"HOME": str(source.parent)})


@pytest.mark.parametrize("agent", ["claude", "codex", "agy"])
def test_empty_explicit_profile_means_no_mcp_servers(
    tmp_path: Path, agent: str
) -> None:
    profile = _profile(tmp_path / "empty.json", {})
    result = prepare_isolated_mcp(
        agent,
        [agent, "exec"],
        profile,
        run_dir=tmp_path / "run",
        environment={"HOME": str(tmp_path)},
    )
    assert result.receipt["server_names"] == []
    if agent == "codex":
        native = tomllib.loads(
            (Path(result.environment["CODEX_HOME"]) / "config.toml").read_text()
        )
        assert native["mcp_servers"] == {}
    elif agent == "agy":
        native = json.loads(
            (Path(result.command[2]) / "config" / "mcp_config.json").read_text()
        )
        assert native["mcpServers"] == {}


def test_malformed_frozen_receipt_refuses_continuation(tmp_path: Path) -> None:
    result = _prepare(tmp_path, "claude")
    (Path(result.receipt["profile_dir"]) / "receipt.json").write_text("[]")
    with pytest.raises(ValueError, match="does not match"):
        prepare_isolated_mcp(
            "claude",
            ["claude", "-p"],
            result.receipt["config_path"],
            run_dir=tmp_path / "resume",
            environment={},
        )


@pytest.mark.parametrize("agent", ["codex", "agy"])
@pytest.mark.parametrize(
    "change",
    ["additional-server", "endpoint", "headers", "malformed", "wrong-type", "missing"],
)
def test_continuation_refuses_changed_consumed_registry_without_resetting_session_state(
    tmp_path: Path,
    agent: str,
    change: str,
) -> None:
    result = _prepare(tmp_path, agent)
    profile_dir = Path(result.receipt["profile_dir"])
    home = (
        Path(result.environment["CODEX_HOME"])
        if agent == "codex"
        else Path(result.command[2])
    )
    native = home / ("config.toml" if agent == "codex" else "config/mcp_config.json")
    state = home / "sessions" / "preserved.jsonl"
    state.parent.mkdir()
    state.write_text("existing-native-session")
    provider_state = home / "provider-state.json"
    provider_state.write_text("existing-provider-auth-state")
    if change == "missing":
        native.unlink()
    elif change == "malformed":
        native.write_text("malformed " + _SECRET)
    elif agent == "codex":
        contents = native.read_text()
        if change == "additional-server":
            contents = contents.replace(
                '"mcp_servers" = { ',
                '"mcp_servers" = { official = {url = "https://official.example/mcp", enabled = true}, ',
                1,
            )
        elif change == "endpoint":
            contents = contents.replace(
                "https://mock.example/mcp", "https://changed.example/mcp"
            )
        elif change == "wrong-type":
            contents = contents.replace('"enabled" = true', '"enabled" = 1')
        else:
            contents = contents.replace(_SECRET, "unexpected-auth")
        native.write_text(contents)
    else:
        contents = json.loads(native.read_text())
        if change == "additional-server":
            contents["mcpServers"]["official"] = {
                "serverUrl": "https://official.example/mcp"
            }
        elif change == "endpoint":
            contents["mcpServers"]["private-mock"]["serverUrl"] = (
                "https://changed.example/mcp"
            )
        elif change == "wrong-type":
            contents["mcpServers"]["private-mock"]["disabled"] = 0
        else:
            contents["mcpServers"]["private-mock"]["headers"]["Authorization"] = (
                "unexpected-auth"
            )
        native.write_text(json.dumps(contents))
    frozen = (profile_dir / "config.json").read_bytes()
    with pytest.raises(ValueError, match="native MCP isolation") as error:
        prepare_isolated_mcp(
            agent,
            [agent, "resume", "session-id"],
            result.receipt["config_path"],
            run_dir=tmp_path / "continuation",
            environment={"HOME": str(tmp_path / "real-home")},
        )
    assert _SECRET not in str(error.value)
    assert state.read_text() == "existing-native-session"
    assert provider_state.read_text() == "existing-provider-auth-state"
    assert (profile_dir / "config.json").read_bytes() == frozen
    assert not (tmp_path / "continuation").exists()


@pytest.mark.parametrize(
    "field,location",
    [
        ("mcp_servers", "profile"),
        ("plugins", "top-level"),
        ("plugins", "profile"),
        ("hooks", "top-level"),
        ("hooks", "profile"),
    ],
)
def test_codex_reuse_rejects_new_schema_owned_mcp_scope(
    tmp_path: Path,
    field: str,
    location: str,
) -> None:
    result = _prepare(tmp_path, "codex")
    native = Path(result.environment["CODEX_HOME"]) / "config.toml"
    contents = native.read_text()
    if location == "profile":
        contents += "\n[profiles.route]\n"
    contents += f"{field} = {{ unwanted = {{ enabled = true }} }}\n"
    native.write_text(contents)
    with pytest.raises(ValueError, match="does not match"):
        prepare_isolated_mcp(
            "codex",
            ["codex", "resume", "session-id"],
            result.receipt["config_path"],
            run_dir=tmp_path / "continuation",
            environment={"HOME": str(tmp_path)},
        )


@pytest.mark.parametrize("agent", ["claude", "codex", "agy"])
@pytest.mark.parametrize("new_run", [False, True])
def test_missing_canonical_frozen_receipt_never_becomes_fresh_input(
    tmp_path: Path,
    agent: str,
    new_run: bool,
) -> None:
    result = _prepare(tmp_path, agent)
    profile_dir = Path(result.receipt["profile_dir"])
    state = profile_dir / "existing-session-state"
    state.write_text("preserve-me")
    (profile_dir / "receipt.json").unlink()
    run_dir = tmp_path / ("continuation" if new_run else "run")
    with pytest.raises(ValueError, match="receipt is missing"):
        prepare_isolated_mcp(
            agent,
            [agent, "resume", "session-id"],
            result.receipt["config_path"],
            run_dir=run_dir,
            environment={"HOME": str(tmp_path / "real-home")},
        )
    assert state.read_text() == "preserve-me"
    if new_run:
        assert not run_dir.exists()


def test_codex_preserves_mcp_named_routing_identifiers_and_provider_headers(
    tmp_path: Path,
) -> None:
    source = tmp_path / "home" / ".codex"
    source.mkdir(parents=True)
    (source / "config.toml").write_text("""model_provider = "mcp-gateway"
profile = "mcp-eval"
[model_providers.mcp-gateway]
name = "MCP gateway provider"
base_url = "https://provider.example/v1"
[model_providers.mcp-gateway.http_headers]
x-mcp-route = "preserve-header"
mcp_servers = "also-a-valid-provider-header"
plugins = "not-a-plugin-config-here"
[profiles.mcp-eval]
model_provider = "mcp-gateway"
model = "pinned-model"
model_reasoning_effort = "high"
plugins = { inherited = { enabled = true } }
hooks = { inherited = "do-not-copy" }
[profiles.mcp-eval.mcp_servers.inherited]
url = "https://official.example/mcp"
""")
    result = _prepare(tmp_path, "codex", environment={"HOME": str(source.parent)})
    native = tomllib.loads(
        (Path(result.environment["CODEX_HOME"]) / "config.toml").read_text()
    )
    assert native["model_provider"] == "mcp-gateway"
    assert native["profile"] == "mcp-eval"
    assert native["profiles"]["mcp-eval"] == {
        "model_provider": "mcp-gateway",
        "model": "pinned-model",
        "model_reasoning_effort": "high",
    }
    assert native["model_providers"]["mcp-gateway"]["http_headers"] == {
        "x-mcp-route": "preserve-header",
        "mcp_servers": "also-a-valid-provider-header",
        "plugins": "not-a-plugin-config-here",
    }
    continuation = prepare_isolated_mcp(
        "codex",
        ["codex", "resume", "session-id"],
        result.receipt["config_path"],
        run_dir=tmp_path / "continuation",
        environment={"HOME": str(source.parent)},
    )
    assert continuation.environment["CODEX_HOME"] == result.environment["CODEX_HOME"]
