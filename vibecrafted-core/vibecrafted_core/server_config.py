"""Operator-owned `~/.config/vibecrafted/config.toml`: the `[server]` table
(load, validate, seed-once), the optional `[tools]` table naming served tool
consoles the native App may open in a tab, and the optional
`[agents.copilot.provider]` table pinning a BYOK provider for the `copilot`
agent."""

from __future__ import annotations

import json
import os
import shlex
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import tomllib

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 3024

#: Disk-space housekeeping defaults: disabled until the Founder opts in, and
#: even once enabled, deletion (`auto_execute`) stays off until the Founder
#: opts into that *separately* -- enabling the schedule only ever plans by
#: default.
DEFAULT_HOUSEKEEPING_ENABLED = False
DEFAULT_HOUSEKEEPING_AUTO_EXECUTE = False
DEFAULT_HOUSEKEEPING_RETENTION_DAYS = 7
DEFAULT_HOUSEKEEPING_INTERVAL_HOURS = 24

#: Tool destinations the native App knows how to open. Each is an optional
#: ``[tools.<key>]`` table with one ``url`` — the *served* surface of a tool
#: owned outside this repository. No default URL is invented for any of them:
#: an absent table means "not configured", which the App reports as such.
#: - ``slack-console``: the vc-slack operator console (``/console`` route of
#:   the vc-slack-agent portal; ``make portal-preview`` serves it, or a deploy).
#: - ``vc-frame``: the product multiplexer web client (``vc-frame web`` / zellij
#:   web server). The App embeds that origin as a native tab. AppDelegate may
#:   start ``vc-frame web`` on the named loopback HTTP origin; it never guesses
#:   a port and never starts the service from a tab.
TOOL_DESTINATION_KEYS: tuple[str, ...] = ("slack-console", "vc-frame")


class ServerConfigError(ValueError):
    """Raised when the operator-owned server configuration is invalid."""


@dataclass(frozen=True)
class ServerConfig:
    """Validated bind host, port, and public URL for the vibecrafted server."""

    bind_host: str = DEFAULT_BIND_HOST
    port: int = DEFAULT_PORT
    public_url: str = ""

    def __post_init__(self) -> None:
        """Validate and normalize all fields in place; raises `ServerConfigError`
        on any invalid value, defaulting `public_url` from host/port when blank."""

        host = _validate_bind_host(self.bind_host)
        port = _validate_port(self.port)
        public_url = _validate_public_url(self.public_url or origin_for(host, port))
        object.__setattr__(self, "bind_host", host)
        object.__setattr__(self, "port", port)
        object.__setattr__(self, "public_url", public_url)

    @property
    def bind_addr(self) -> str:
        """`host:port` string suitable for logging/display."""

        return f"{self.bind_host}:{self.port}"

    @property
    def service_arguments(self) -> tuple[str, ...]:
        """CLI `--host`/`--port` argument pair for launching the server process."""

        return ("--host", self.bind_host, "--port", str(self.port))


def config_path(*, operator_home: Path | None = None) -> Path:
    """Resolve `~/.config/vibecrafted/config.toml`, honoring `XDG_CONFIG_HOME`
    and an explicit `operator_home` override."""

    if operator_home is None:
        configured = os.environ.get("XDG_CONFIG_HOME")
        if configured:
            return Path(configured).expanduser() / "vibecrafted" / "config.toml"
        operator_home = Path(os.environ.get("HOME", str(Path.home())))
    return operator_home.expanduser() / ".config" / "vibecrafted" / "config.toml"


def load_server_config(
    path: Path | None = None,
    *,
    operator_home: Path | None = None,
) -> ServerConfig:
    """Load and validate the `[server]` table from the TOML config file at
    `path` (or the resolved default); returns defaults when the file or table
    is absent. Raises `ServerConfigError` on unreadable/invalid TOML or an
    unsupported `[server]` key."""

    resolved = path or config_path(operator_home=operator_home)
    try:
        raw = resolved.read_bytes()
    except FileNotFoundError:
        return ServerConfig()
    except OSError as exc:
        raise ServerConfigError(
            f"cannot read server config at {resolved}: {exc}"
        ) from exc
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ServerConfigError(
            f"invalid TOML in server config at {resolved}: {exc}"
        ) from exc
    section = payload.get("server")
    if section is None:
        return ServerConfig()
    if not isinstance(section, dict):
        raise ServerConfigError("[server] must be a TOML table")
    unknown = sorted(set(section) - {"bind_host", "port", "public_url"})
    if unknown:
        raise ServerConfigError("unsupported [server] key(s): " + ", ".join(unknown))
    return ServerConfig(
        bind_host=section.get("bind_host", DEFAULT_BIND_HOST),
        port=section.get("port", DEFAULT_PORT),
        public_url=section.get("public_url", ""),
    )


@dataclass(frozen=True)
class HousekeepingConfig:
    """Validated `[housekeeping]` table: the Founder's own opt-in for
    automatic disk-space reclaim scheduling on `VIBECRAFTED_HOME`.

    `enabled` is the switch that lets `vibecrafted housekeeping schedule
    install` install anything at all. `auto_execute` is a second, separate
    switch: even an installed schedule only ever writes a read-only plan
    receipt unless the Founder also sets `auto_execute = true`, at which
    point the schedule deletes eligible stale transient/build-cache state
    the same way a manual `--execute --confirm ...` run would."""

    enabled: bool = DEFAULT_HOUSEKEEPING_ENABLED
    auto_execute: bool = DEFAULT_HOUSEKEEPING_AUTO_EXECUTE
    retention_days: int = DEFAULT_HOUSEKEEPING_RETENTION_DAYS
    interval_hours: int = DEFAULT_HOUSEKEEPING_INTERVAL_HOURS

    def __post_init__(self) -> None:
        """Validate and normalize all fields in place; raises
        `ServerConfigError` on any invalid value."""

        enabled = _validate_housekeeping_bool("housekeeping.enabled", self.enabled)
        auto_execute = _validate_housekeeping_bool(
            "housekeeping.auto_execute", self.auto_execute
        )
        retention_days = _validate_positive_int(
            "housekeeping.retention_days", self.retention_days
        )
        interval_hours = _validate_positive_int(
            "housekeeping.interval_hours", self.interval_hours
        )
        object.__setattr__(self, "enabled", enabled)
        object.__setattr__(self, "auto_execute", auto_execute)
        object.__setattr__(self, "retention_days", retention_days)
        object.__setattr__(self, "interval_hours", interval_hours)


def load_housekeeping_config(
    path: Path | None = None,
    *,
    operator_home: Path | None = None,
) -> HousekeepingConfig:
    """Load and validate the `[housekeeping]` table from the TOML config
    file at `path` (or the resolved default); returns defaults (disabled)
    when the file or table is absent. Raises `ServerConfigError` on
    unreadable/invalid TOML or an unsupported `[housekeeping]` key."""

    resolved = path or config_path(operator_home=operator_home)
    try:
        raw = resolved.read_bytes()
    except FileNotFoundError:
        return HousekeepingConfig()
    except OSError as exc:
        raise ServerConfigError(
            f"cannot read server config at {resolved}: {exc}"
        ) from exc
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ServerConfigError(
            f"invalid TOML in server config at {resolved}: {exc}"
        ) from exc
    section = payload.get("housekeeping")
    if section is None:
        return HousekeepingConfig()
    if not isinstance(section, dict):
        raise ServerConfigError("[housekeeping] must be a TOML table")
    unknown = sorted(
        set(section) - {"enabled", "auto_execute", "retention_days", "interval_hours"}
    )
    if unknown:
        raise ServerConfigError(
            "unsupported [housekeeping] key(s): " + ", ".join(unknown)
        )
    return HousekeepingConfig(
        enabled=section.get("enabled", DEFAULT_HOUSEKEEPING_ENABLED),
        auto_execute=section.get("auto_execute", DEFAULT_HOUSEKEEPING_AUTO_EXECUTE),
        retention_days=section.get(
            "retention_days", DEFAULT_HOUSEKEEPING_RETENTION_DAYS
        ),
        interval_hours=section.get(
            "interval_hours", DEFAULT_HOUSEKEEPING_INTERVAL_HOURS
        ),
    )


def load_tool_destinations(
    path: Path | None = None,
    *,
    operator_home: Path | None = None,
) -> dict[str, str]:
    """Load the optional ``[tools]`` table as ``{key: url}``.

    Returns ``{}`` when the file or table is absent. Raises `ServerConfigError`
    for unreadable/invalid TOML, an unknown tool key, a non-table entry, or a
    URL that is not an ``http(s)`` location without credentials, query, or
    fragment. The App and the Python owner apply the same contract, so a URL
    the App opens is one this owner would accept.
    """

    resolved = path or config_path(operator_home=operator_home)
    try:
        raw = resolved.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ServerConfigError(
            f"cannot read server config at {resolved}: {exc}"
        ) from exc
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ServerConfigError(
            f"invalid TOML in server config at {resolved}: {exc}"
        ) from exc
    section = payload.get("tools")
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ServerConfigError("[tools] must be a TOML table")
    unknown = sorted(set(section) - set(TOOL_DESTINATION_KEYS))
    if unknown:
        raise ServerConfigError("unsupported [tools] key(s): " + ", ".join(unknown))
    destinations: dict[str, str] = {}
    for key, entry in section.items():
        if not isinstance(entry, dict):
            raise ServerConfigError(f"[tools.{key}] must be a TOML table")
        extra = sorted(set(entry) - {"url"})
        if extra:
            raise ServerConfigError(
                f"unsupported [tools.{key}] key(s): " + ", ".join(extra)
            )
        url = entry.get("url", "")
        if url == "":
            continue
        destinations[key] = _validate_tool_url(key, url)
    return destinations


def _validate_tool_url(key: str, value: object) -> str:
    """Require an ``http(s)`` URL with a host and no credentials, query, or
    fragment; a path (such as ``/console``) is allowed."""

    if not isinstance(value, str):
        raise ServerConfigError(f"tools.{key}.url must be a string")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in value.strip())
    ):
        raise ServerConfigError(
            f"tools.{key}.url must be an HTTP(S) URL without credentials, query, or fragment"
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ServerConfigError(f"tools.{key}.url has an invalid port: {exc}") from exc
    return value.strip()


#: Env var name for each `[agents.copilot.provider]` TOML key, in the order
#: `copilot-provider-env` emits them. Names come from `copilot help
#: providers`; `base_url` is the only one BYOK requires (the Ollama case:
#: base_url + model).
COPILOT_PROVIDER_ENV_NAMES: dict[str, str] = {
    "base_url": "COPILOT_PROVIDER_BASE_URL",
    "model": "COPILOT_MODEL",
    "type": "COPILOT_PROVIDER_TYPE",
    "wire_api": "COPILOT_PROVIDER_WIRE_API",
    "transport": "COPILOT_PROVIDER_TRANSPORT",
    "api_key": "COPILOT_PROVIDER_API_KEY",
    "api_key_command": "COPILOT_PROVIDER_API_KEY_COMMAND",
    "bearer_token": "COPILOT_PROVIDER_BEARER_TOKEN",
    "headers": "COPILOT_PROVIDER_HEADERS",
    "model_id": "COPILOT_PROVIDER_MODEL_ID",
    "wire_model": "COPILOT_PROVIDER_WIRE_MODEL",
    "max_prompt_tokens": "COPILOT_PROVIDER_MAX_PROMPT_TOKENS",
    "max_output_tokens": "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS",
}

#: TOML keys that may be given as an integer (copilot still reads them as a
#: string env var; the loader stringifies before validation).
_COPILOT_PROVIDER_INT_KEYS: frozenset[str] = frozenset(
    {"max_prompt_tokens", "max_output_tokens"}
)


@dataclass(frozen=True)
class CopilotProviderConfig:
    """Validated `[agents.copilot.provider]` table: an operator pin of a BYOK
    provider (for example a local Ollama endpoint serving kimi-k3:cloud) for
    the `copilot` agent. An absent table yields every field blank and
    `configured=False` -- today's behavior, Copilot's own default model."""

    base_url: str = ""
    model: str = ""
    type: str = ""
    wire_api: str = ""
    transport: str = ""
    api_key: str = ""
    api_key_command: str = ""
    bearer_token: str = ""
    headers: str = ""
    model_id: str = ""
    wire_model: str = ""
    max_prompt_tokens: str = ""
    max_output_tokens: str = ""

    def __post_init__(self) -> None:
        """Require every field to be a string; raises `ServerConfigError`
        otherwise."""

        for field_name in COPILOT_PROVIDER_ENV_NAMES:
            value = getattr(self, field_name)
            if not isinstance(value, str):
                raise ServerConfigError(
                    f"agents.copilot.provider.{field_name} must be a string"
                )

    @property
    def configured(self) -> bool:
        """True once `base_url` is set -- the only field BYOK requires."""

        return bool(self.base_url.strip())

    def env(self) -> dict[str, str]:
        """BYOK env vars this config implies, in `COPILOT_PROVIDER_ENV_NAMES`
        order; `{}` when `base_url` is unset (unconfigured => no-op)."""

        if not self.configured:
            return {}
        rendered: dict[str, str] = {}
        for field_name, env_name in COPILOT_PROVIDER_ENV_NAMES.items():
            value = getattr(self, field_name)
            if value:
                rendered[env_name] = value
        return rendered


def load_copilot_provider_config(
    path: Path | None = None,
    *,
    operator_home: Path | None = None,
) -> CopilotProviderConfig:
    """Load and validate the `[agents.copilot.provider]` table: an operator
    pin of a BYOK provider for the `copilot` agent, translated to
    `COPILOT_PROVIDER_*` / `COPILOT_MODEL` env vars by `copilot_spawn.sh`.
    Returns an unconfigured default when the file, `[agents]`,
    `[agents.copilot]`, or `[agents.copilot.provider]` is absent -- today's
    behavior (Copilot's own default model). Raises `ServerConfigError` on
    unreadable/invalid TOML, a non-table section, an unsupported key, or a
    present table missing `base_url`.
    """

    resolved = path or config_path(operator_home=operator_home)
    try:
        raw = resolved.read_bytes()
    except FileNotFoundError:
        return CopilotProviderConfig()
    except OSError as exc:
        raise ServerConfigError(
            f"cannot read server config at {resolved}: {exc}"
        ) from exc
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ServerConfigError(
            f"invalid TOML in server config at {resolved}: {exc}"
        ) from exc
    agents = payload.get("agents")
    if agents is None:
        return CopilotProviderConfig()
    if not isinstance(agents, dict):
        raise ServerConfigError("[agents] must be a TOML table")
    copilot = agents.get("copilot")
    if copilot is None:
        return CopilotProviderConfig()
    if not isinstance(copilot, dict):
        raise ServerConfigError("[agents.copilot] must be a TOML table")
    section = copilot.get("provider")
    if section is None:
        return CopilotProviderConfig()
    if not isinstance(section, dict):
        raise ServerConfigError("[agents.copilot.provider] must be a TOML table")
    unknown = sorted(set(section) - set(COPILOT_PROVIDER_ENV_NAMES))
    if unknown:
        raise ServerConfigError(
            "unsupported [agents.copilot.provider] key(s): " + ", ".join(unknown)
        )
    values: dict[str, str] = {}
    for key in COPILOT_PROVIDER_ENV_NAMES:
        raw_value = section.get(key, "")
        if (
            key in _COPILOT_PROVIDER_INT_KEYS
            and isinstance(raw_value, int)
            and not isinstance(raw_value, bool)
        ):
            raw_value = str(raw_value)
        if not isinstance(raw_value, str):
            expected = (
                "a string or integer"
                if key in _COPILOT_PROVIDER_INT_KEYS
                else "a string"
            )
            raise ServerConfigError(f"agents.copilot.provider.{key} must be {expected}")
        values[key] = raw_value
    config = CopilotProviderConfig(**values)
    if not config.configured:
        raise ServerConfigError(
            "agents.copilot.provider.base_url is required when "
            "[agents.copilot.provider] is present"
        )
    return config


def copilot_provider_env_lines(config: CopilotProviderConfig) -> list[str]:
    """Render `export KEY=value` lines (shell-quoted) for vars `config`
    implies, skipping any already present in the live process environment --
    an explicit operator export always wins over the config.toml pin. Secrets
    (`api_key`, `api_key_command`, `bearer_token`, `headers`) only ever reach
    this list as `export` statements fed straight to `eval` by the caller;
    they are never logged, printed elsewhere, or written to meta/report."""

    return [
        f"export {key}={shlex.quote(value)}"
        for key, value in config.env().items()
        if key not in os.environ
    ]


def _copilot_provider_env_cli(argv: list[str]) -> int:
    """`python -m vibecrafted_core.server_config copilot-provider-env`: print
    shell `export` statements for the operator's BYOK pin, or nothing when
    unconfigured/absent. `copilot_spawn.sh` consumes this via
    `eval "$(...)"`."""

    if argv != ["copilot-provider-env"]:
        print(
            "usage: python -m vibecrafted_core.server_config copilot-provider-env",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_copilot_provider_config()
    except ServerConfigError as exc:
        print(f"vibecrafted: invalid copilot provider config: {exc}", file=sys.stderr)
        return 1
    for line in copilot_provider_env_lines(config):
        print(line)
    return 0


def has_server_config(
    path: Path | None = None, *, operator_home: Path | None = None
) -> bool:
    """Return whether the config contains an explicit [server] owner table."""
    resolved = path or config_path(operator_home=operator_home)
    try:
        payload = tomllib.loads(resolved.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ServerConfigError(
            f"cannot inspect server config at {resolved}: {exc}"
        ) from exc
    return isinstance(payload.get("server"), dict)


def seed_server_config(
    seed: ServerConfig,
    path: Path | None = None,
    *,
    operator_home: Path | None = None,
) -> tuple[ServerConfig, bool]:
    """Create [server] once; an existing table remains authoritative."""
    resolved = path or config_path(operator_home=operator_home)
    try:
        visible = resolved.lstat()
    except FileNotFoundError:
        existing = b""
        mode = 0o600
    except OSError as exc:
        raise ServerConfigError(
            f"cannot inspect server config at {resolved}: {exc}"
        ) from exc
    else:
        if resolved.is_symlink() or not stat.S_ISREG(visible.st_mode):
            raise ServerConfigError(
                f"server config is not a stable regular file: {resolved}"
            )
        try:
            existing = resolved.read_bytes()
        except OSError as exc:
            raise ServerConfigError(
                f"cannot read server config at {resolved}: {exc}"
            ) from exc
        mode = stat.S_IMODE(visible.st_mode)
        try:
            payload = tomllib.loads(existing.decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ServerConfigError(
                f"invalid TOML in server config at {resolved}: {exc}"
            ) from exc
        if "server" in payload:
            return load_server_config(resolved), False

    separator = b"" if not existing or existing.endswith(b"\n\n") else b"\n"
    rendered = (
        b"[server]\n"
        + f"bind_host = {json.dumps(seed.bind_host)}\n".encode()
        + f"port = {seed.port}\n".encode()
        + f"public_url = {json.dumps(seed.public_url)}\n".encode()
    )
    _atomic_write(resolved, existing + separator + rendered, mode=mode)
    return load_server_config(resolved), True


def _atomic_write(path: Path, contents: bytes, *, mode: int) -> None:
    """Write `contents` to `path` via a sibling tempfile, fsync, and atomic
    rename, cleaning up the tempfile if anything raises before the rename."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _validate_bind_host(value: object) -> str:
    """Reject non-string, empty, whitespace-containing, untrimmed, or
    URL-syntax-bearing bind hosts; return the value unchanged otherwise."""

    if not isinstance(value, str):
        raise ServerConfigError("server.bind_host must be a string")
    host = value.strip()
    if not host or host != value or any(char.isspace() for char in host):
        raise ServerConfigError("server.bind_host must be a non-empty host")
    if any(char in host for char in "/?#@"):
        raise ServerConfigError("server.bind_host must not contain URL syntax")
    return host


def _validate_port(value: object) -> int:
    """Require a real int (bool is rejected despite being an int subclass) in
    the 1-65535 range."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ServerConfigError("server.port must be an integer")
    if not 1 <= value <= 65535:
        raise ServerConfigError("server.port must be between 1 and 65535")
    return value


def _validate_public_url(value: object) -> str:
    """Require an http(s) origin with no credentials, path (beyond `/`), query,
    or fragment, and a parseable port; return it with any trailing slash
    stripped."""

    if not isinstance(value, str):
        raise ServerConfigError("server.public_url must be a string")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ServerConfigError(
            "server.public_url must be an HTTP(S) origin without credentials, path, query, or fragment"
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ServerConfigError(
            f"server.public_url has an invalid port: {exc}"
        ) from exc
    return value.rstrip("/")


def _validate_housekeeping_bool(key: str, value: object) -> bool:
    """Require a real bool for a `[housekeeping]` on/off switch."""

    if not isinstance(value, bool):
        raise ServerConfigError(f"{key} must be a boolean")
    return value


def _validate_positive_int(key: str, value: object) -> int:
    """Require a real int (bool is rejected) that is at least 1."""

    if isinstance(value, bool) or not isinstance(value, int):
        raise ServerConfigError(f"{key} must be an integer")
    if value < 1:
        raise ServerConfigError(f"{key} must be at least 1")
    return value


def origin_for(host: str, port: int) -> str:
    """Build an `http://host:port` origin, bracketing a bare IPv6 host."""

    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{rendered_host}:{port}"


if __name__ == "__main__":
    raise SystemExit(_copilot_provider_env_cli(sys.argv[1:]))
