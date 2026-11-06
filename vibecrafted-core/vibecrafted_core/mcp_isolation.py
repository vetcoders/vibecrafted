"""Explicit, private MCP profiles for native workflow commands.

No caller without an explicit profile enters this module. Claude's native strict
switch owns registry isolation; Codex receives a private CODEX_HOME. HOME is
never changed: browser/tool children must retain the operator's real keychain.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_SCHEMA = "vibecrafted.mcp-isolation.v1"
_SUPPORTED = {"claude", "codex", "agy"}
_NAME = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_CODEX_ROUTING = {
    "model",
    "model_provider",
    "model_providers",
    "model_reasoning_effort",
    "model_reasoning_summary",
    "model_verbosity",
    "model_context_window",
    "model_auto_compact_token_limit",
    "profile",
    "profiles",
    "service_tier",
    "chatgpt_base_url",
    "preferred_auth_method",
    "forced_login_method",
    "forced_chatgpt_workspace_id",
    "cli_auth_credentials_store",
}


@dataclass(frozen=True)
class IsolatedMcp:
    command: list[str]
    environment: dict[str, str] = field(repr=False)
    receipt: dict[str, Any]


def _agent(agent: str) -> str:
    if agent and agent not in _SUPPORTED:
        raise ValueError("strict MCP isolation is unsupported for this provider")
    return agent


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate MCP configuration key")
        result[key] = value
    return result


def _read_profile(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw, object_pairs_hook=_object)
    except (OSError, ValueError, UnicodeError):
        raise ValueError(
            "MCP configuration must be a readable, valid JSON file"
        ) from None
    if not isinstance(payload, dict) or set(payload) != {"mcpServers"}:
        raise ValueError("MCP configuration must contain only an mcpServers object")
    servers = payload["mcpServers"]
    if not isinstance(servers, dict):
        raise TypeError("MCP configuration mcpServers must be an object")
    for name, server in servers.items():
        if not _NAME.fullmatch(name) or not isinstance(server, dict):
            raise ValueError("MCP configuration contains an invalid server entry")
        # Only losslessly portable HTTP profiles are admitted. Never discard
        # unsupported transports, OAuth, environment interpolation or options.
        if set(server) - {"type", "url", "headers"} or server.get("type") != "http":
            raise ValueError(
                "strict MCP profiles support only HTTP servers with type, url and optional headers"
            )
        url = server.get("url")
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            valid_url = (
                parsed is not None
                and parsed.scheme in {"https", "http"}
                and bool(parsed.hostname)
                and not parsed.username
                and not parsed.password
                and not parsed.fragment
            )
            if parsed is not None:
                _ = parsed.port
        except ValueError:
            valid_url = False
        if not valid_url or any(c.isspace() for c in url) or "${" in url:
            raise ValueError(
                "MCP server URL must be an absolute HTTP URL without user credentials or interpolation"
            )
        headers = server.get("headers", {})
        if not isinstance(headers, dict) or any(
            not isinstance(key, str)
            or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
            or not isinstance(value, str)
            or any(c in value for c in "\r\n\x00")
            or "${" in value
            for key, value in headers.items()
        ):
            raise ValueError(
                "MCP headers must contain literal HTTP header names and string values"
            )
    return raw, payload


def validate_mcp_config(path: str | Path, *, agent: str = "") -> Path:
    """Validate before launch side effects; errors never reproduce input data."""
    _agent(agent)
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except (OSError, ValueError, TypeError):
        raise ValueError("MCP configuration must be a readable JSON file") from None
    try:
        _read_profile(resolved)
    except TypeError:
        raise ValueError(
            "MCP configuration contains incorrectly typed server entries"
        ) from None
    return resolved


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise ValueError("MCP isolation directory must not be a symlink")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)


def _private_write(path: Path, content: bytes) -> None:
    # Exclusive creation prevents following a pre-existing credential symlink.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return (
            "{ "
            + ", ".join(
                json.dumps(key) + " = " + _toml_value(item)
                for key, item in value.items()
            )
            + " }"
        )
    raise ValueError("unsupported value in native provider routing configuration")


_CODEX_SCOPE_FIELDS = {
    "mcp_servers",
    "mcp_oauth_credentials_store",
    "mcp_oauth_callback_port",
    "mcp_oauth_callback_url",
    "experimental_use_rmcp_client",
    "plugins",
    "hooks",
}


def _codex_routing(native: dict[str, Any]) -> dict[str, Any]:
    routing = {key: value for key, value in native.items() if key in _CODEX_ROUTING}
    profiles = routing.get("profiles", {})
    if not isinstance(profiles, dict) or any(
        not isinstance(profile, dict) for profile in profiles.values()
    ):
        raise ValueError("native provider routing profiles must be objects")
    if "profiles" in routing:
        # Profile/provider identifiers and header names are user-defined, not
        # configuration fields. Only direct profile options own MCP scope.
        routing["profiles"] = {
            name: {
                key: value
                for key, value in profile.items()
                if key not in _CODEX_SCOPE_FIELDS
            }
            for name, profile in profiles.items()
        }
    return routing


def _codex_servers(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        name: {
            "url": server["url"],
            "http_headers": server.get("headers", {}),
            "enabled": True,
        }
        for name, server in payload["mcpServers"].items()
    }


def _codex_home(
    root: Path, environment: Mapping[str, str], payload: dict[str, Any]
) -> Path:
    home = root / "codex-home"
    if home.exists() or home.is_symlink():
        config = home / "config.toml"
        if home.is_symlink() or config.is_symlink() or not config.is_file():
            raise ValueError("native MCP isolation profile is incomplete")
        try:
            native = tomllib.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            raise ValueError(
                "native MCP isolation registry is unreadable or invalid"
            ) from None
        routing = _codex_routing(native)
        profiles = native.get("profiles", {})
        if (
            json.dumps(native.get("mcp_servers"), sort_keys=True)
            != json.dumps(_codex_servers(payload), sort_keys=True)
            or (set(native) & (_CODEX_SCOPE_FIELDS - {"mcp_servers"}))
            or profiles != routing.get("profiles", {})
        ):
            raise ValueError(
                "native MCP isolation registry does not match the frozen profile"
            )
        return home
    if (root / "receipt.json").exists():
        raise ValueError("native MCP isolation profile is incomplete")
    source = Path(
        environment.get("CODEX_HOME")
        or str(Path(environment.get("HOME") or str(Path.home())) / ".codex")
    ).expanduser()
    config = source / "config.toml"
    routing: dict[str, Any] = {}
    if config.exists():
        try:
            native = tomllib.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            raise ValueError(
                "native provider routing configuration is unreadable or invalid"
            ) from None
        routing = _codex_routing(native)
    auth = source / "auth.json"
    if auth.is_file():
        # A keyring entry is keyed by the original CODEX_HOME, not the isolated
        # home. Prefer the existing provider-only file when it is available.
        routing["cli_auth_credentials_store"] = "file"
    elif routing.get("cli_auth_credentials_store") == "keyring" and not environment.get(
        "OPENAI_API_KEY"
    ):
        raise ValueError(
            "strict Codex MCP isolation requires file-backed or environment provider authentication"
        )
    routing["mcp_servers"] = _codex_servers(payload)
    data = (
        "\n".join(
            json.dumps(key) + " = " + _toml_value(value)
            for key, value in routing.items()
        )
        + "\n"
    )
    _private_dir(home)
    _private_write(home / "config.toml", data.encode("utf-8"))
    # auth.json is Codex's provider login only. MCP OAuth lives in a separate
    # store, which is deliberately not copied. Sharing the provider file avoids
    # proactively cloning refresh-token state; native refresh behavior is owned
    # by Codex, not reimplemented here.
    if auth.is_file():
        (home / "auth.json").symlink_to(auth.resolve())
    return home


def _agy_dir(root: Path, payload: dict[str, Any]) -> Path:
    gemini = root / "agy-gemini"
    config_dir = gemini / "config"
    native = config_dir / "mcp_config.json"
    servers = {
        name: {
            "serverUrl": server["url"],
            "headers": server.get("headers", {}),
            "disabled": False,
        }
        for name, server in payload["mcpServers"].items()
    }
    expected = {"mcpServers": servers}
    if native.exists() or native.is_symlink():
        if gemini.is_symlink() or config_dir.is_symlink() or native.is_symlink():
            raise ValueError("native MCP isolation profile must not contain symlinks")
        try:
            actual = json.loads(
                native.read_text(encoding="utf-8"), object_pairs_hook=_object
            )
        except (OSError, ValueError, UnicodeError):
            raise ValueError(
                "native MCP isolation registry is unreadable or invalid"
            ) from None
        if json.dumps(actual, sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise ValueError(
                "native MCP isolation registry does not match the frozen profile"
            )
        return gemini
    if (root / "receipt.json").exists():
        raise ValueError("native MCP isolation profile is incomplete")
    _private_dir(gemini)
    _private_dir(config_dir)
    _private_write(native, (json.dumps(expected) + "\n").encode("utf-8"))
    return gemini


def _existing_profile(config: Path, agent: str) -> Path | None:
    root = config.parent
    if config.name != "config.json" or root.name != "mcp-isolation":
        return None
    marker = root / "receipt.json"
    if not marker.is_file():
        raise ValueError("frozen MCP isolation receipt is missing")
    try:
        receipt = json.loads(marker.read_text(encoding="utf-8"))
        digest = hashlib.sha256(config.read_bytes()).hexdigest()
    except (OSError, ValueError, UnicodeError):
        raise ValueError("frozen MCP isolation receipt is unreadable") from None
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != _SCHEMA
        or receipt.get("agent") != agent
        or receipt.get("sha256") != digest
    ):
        raise ValueError("frozen MCP isolation profile does not match its receipt")
    return root


def prepare_isolated_mcp(
    agent: str,
    command: Sequence[str],
    config_path: str | Path,
    *,
    run_dir: str | Path,
    environment: Mapping[str, str],
) -> IsolatedMcp:
    """Freeze one profile, then adapt an already-built native command.

    Continuations retain the frozen profile's provider home, including native
    session records. Changing the original source cannot change a running run.
    """
    _agent(agent)
    if not agent or not command:
        raise ValueError("strict MCP isolation requires a native provider command")
    # Reject conflicting explicit config flags rather than merge user registries.
    if any(
        arg.startswith(
            ("--mcp-config", "--strict-mcp-config", "--gemini_dir", "--gemini-dir")
        )
        or "mcp_servers" in arg
        for arg in command[1:]
    ):
        raise ValueError("native command already contains MCP configuration overrides")
    env = dict(environment)
    args = list(command)
    root = Path(run_dir).expanduser().resolve() / "mcp-isolation"
    local_config = root / "config.json"
    try:
        if local_config.exists():
            config = local_config
            prior = _existing_profile(config, agent)
            if prior is None:
                raise ValueError("frozen MCP isolation receipt is missing")
            root = prior
        else:
            config = validate_mcp_config(config_path, agent=agent)
            prior = _existing_profile(config, agent)
            if prior is not None:
                root = prior
            else:
                raw, _ = _read_profile(config)
                _private_dir(root)
                config = root / "config.json"
                _private_write(config, raw)
        raw, payload = _read_profile(config)
        if agent == "claude":
            # The strict switch terminates --mcp-config's variadic argument and
            # excludes project, user and managed registry entries natively.
            args[1:1] = ["--mcp-config", str(config), "--strict-mcp-config"]
        elif agent == "codex":
            env["CODEX_HOME"] = str(_codex_home(root, env, payload))
        elif agent == "agy":
            # Native config override only: sign-in and browser subprocesses
            # retain the real HOME. No provider/MCP OAuth files are copied.
            args[1:1] = ["--gemini_dir", str(_agy_dir(root, payload))]
        receipt = {
            "schema": _SCHEMA,
            "agent": agent,
            "strict": True,
            "config_path": str(config),
            "profile_dir": str(root),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "server_names": sorted(payload["mcpServers"]),
        }
        marker = root / "receipt.json"
        if not marker.exists():
            _private_write(
                marker, (json.dumps(receipt, sort_keys=True) + "\n").encode("utf-8")
            )
    except (OSError, TypeError):
        raise ValueError(
            "could not prepare private native MCP isolation profile"
        ) from None
    return IsolatedMcp(command=args, environment=env, receipt=receipt)
