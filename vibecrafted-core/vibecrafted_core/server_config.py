"""Operator-owned `~/.config/vibecrafted/config.toml`: the `[server]` table
(load, validate, seed-once) and the optional `[tools]` table naming
served tool consoles the native App may open in a tab."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import tomllib

DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 3024

#: Tool destinations the native App knows how to open. Each is an optional
#: ``[tools.<key>]`` table with one ``url`` — the *served* surface of a tool
#: owned outside this repository. No default URL is invented for any of them:
#: an absent table means "not configured", which the App reports as such.
#: - ``slack-console``: the vc-slack operator console (``/console`` route of
#:   the vc-slack-agent portal; ``make portal-preview`` serves it, or a deploy).
TOOL_DESTINATION_KEYS: tuple[str, ...] = ("slack-console",)


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


def origin_for(host: str, port: int) -> str:
    """Build an `http://host:port` origin, bracketing a bare IPv6 host."""

    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{rendered_host}:{port}"
