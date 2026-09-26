"""Allowlist for the environment a headless worker may inherit.

Interactive sessions keep the dispatcher environment. Headless workers do not.
Copying ``os.environ`` wholesale leaked the parent Claude bus
(``CLAUDE_CODE_MESSAGING_SOCKET`` / ``CLAUDE_CODE_MESSAGING_TOKEN``) into a
Codex worker, along with unrelated API tokens. One allowlist is the gate.

``selected_runtime_environment`` is not this gate. Message delivery still uses
that helper to address a selected runtime generation.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# Process basics a provider CLI needs before it can read HOME or speak HTTPS.
_PROCESS_ALLOW: frozenset[str] = frozenset(
    {
        # Locate the CLI, git, and the interpreter. Value passes unchanged.
        "PATH",
        # Provider auth and config live under the user home (~/.codex, ~/.claude,
        # ~/.grok, ~/.kimi-code). Without HOME the CLI cannot find them.
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        # CLIs probe a terminal even when headless. TERM missing changes or
        # refuses startup; the COLOR/NO_COLOR family only affects rendering.
        "TERM",
        "COLORTERM",
        "NO_COLOR",
        "FORCE_COLOR",
        "CLICOLOR",
        "CLICOLOR_FORCE",
        # Temp files for the interpreter and the CLI.
        "TMPDIR",
        "TMP",
        "TEMP",
        "PWD",
        # Locale. LC_* facets ride the prefix below; these two are the roots.
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        # The dispatcher child is often ``python -m vibecrafted_core``.
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONUNBUFFERED",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONWARNINGS",
        "VIRTUAL_ENV",
        "VIRTUAL_ENV_PROMPT",
        # TLS material. Dropping these makes provider HTTPS fail closed on
        # machines that do not use the default CA bundle.
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "NIX_SSL_CERT_FILE",
        "GIT_SSL_CAINFO",
        # Git identity and editor for the worker's own commits. GIT_DIR and
        # GIT_WORK_TREE are intentionally absent: they would pin the child to
        # the dispatcher's repository.
        "GIT_EXEC_PATH",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_COUNT",
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
        "GIT_EDITOR",
        "GIT_SEQUENCE_EDITOR",
        "GIT_PAGER",
        "GIT_TERMINAL_PROMPT",
        "GPG_TTY",
        "GNUPGHOME",
        # ssh-agent so git over ssh can authenticate without a new key file.
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        # Proxy the provider CLI already honors. Dropping it strands off-LAN
        # API calls that the dispatcher itself could make.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        # macOS Homebrew locates keg-only tools the CLI shells out to.
        "HOMEBREW_PREFIX",
        "HOMEBREW_CELLAR",
        "HOMEBREW_REPOSITORY",
        # Windows process basics. USERPROFILE is that platform's home.
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        # wrappers._env_for_run pins the spawn substrate. Not a secret.
        "VETCODERS_SPAWN_RUNTIME",
    }
)

# Provider CLI requirements. Names mirror command_bridge_onboarding
# PROVIDER_ENV_KEYS plus the config homes and API bases those CLIs read.
# CLAUDE_CODE_MESSAGING_SOCKET and CLAUDE_CODE_MESSAGING_TOKEN are not here:
# they are the parent Claude bus, not worker auth.
_PROVIDER_ALLOW: frozenset[str] = frozenset(
    {
        # claude CLI auth and optional gateway (PROVIDER_ENV_KEYS["claude"]).
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        # claude CLI config directory override. Not CLAUDE_CODE_*.
        "CLAUDE_CONFIG_DIR",
        # codex CLI auth (PROVIDER_ENV_KEYS["codex"]) and config home
        # (agent_stream.py / compact_hooks.py read CODEX_HOME).
        "OPENAI_API_KEY",
        "OPENAI_API_KEY_CODEX",
        "OPENAI_BASE_URL",
        "CODEX_HOME",
        # grok CLI auth (PROVIDER_ENV_KEYS["grok"]).
        "XAI_API_KEY",
        "GROK_API_KEY",
        # kimi CLI auth (PROVIDER_ENV_KEYS["kimi"]).
        "KIMI_API_KEY",
        "MOONSHOT_API_KEY",
        "MOONSHOT_BASE_URL",
    }
)

# Families, not wildcards over the whole environment.
# VIBECRAFTED_: the run contract (RUN_ID, paths, session, agent, skill).
# XDG_: config/data/runtime homes the CLIs follow.
# LC_: locale facets other than LC_ALL.
# SPAWN_: shell launcher contract (launcher.sh, session.sh, run_reaper.py).
# GIT_CONFIG_KEY_ / GIT_CONFIG_VALUE_: git's numbered config injection,
# paired with GIT_CONFIG_COUNT above.
_PREFIX_ALLOW: tuple[str, ...] = (
    "VIBECRAFTED_",
    "XDG_",
    "LC_",
    "SPAWN_",
    "GIT_CONFIG_KEY_",
    "GIT_CONFIG_VALUE_",
)


def env_key_allowed(name: str) -> bool:
    """Return whether one environment name may enter a headless worker."""
    if name in _PROCESS_ALLOW or name in _PROVIDER_ALLOW:
        return True
    return any(name.startswith(prefix) for prefix in _PREFIX_ALLOW)


def filter_headless_worker_env(source: Mapping[str, str]) -> dict[str, str]:
    """Copy only allowlisted string entries, preserving order and values.

    Non-string values are dropped. ``subprocess`` environments are strings,
    and a non-string would be a programming error rather than a secret to pass.
    """
    return {
        key: value
        for key, value in source.items()
        if isinstance(key, str) and isinstance(value, str) and env_key_allowed(key)
    }


def dispatcher_identity(
    environment: Mapping[str, str] | None = None,
    *,
    pid: int | None = None,
) -> dict[str, object]:
    """Identity of the process that launched a run.

    ``agent`` and ``session_id`` come from the dispatcher environment before
    the child overlay replaces ``VIBECRAFTED_AGENT``. ``pid`` is the process
    that records the launch (this process, unless the caller names one).
    Empty agent or session is honest when a human shell had neither set.
    """
    env = os.environ if environment is None else environment
    return {
        "agent": str(env.get("VIBECRAFTED_AGENT") or "").strip(),
        "session_id": str(env.get("VIBECRAFTED_SESSION_ID") or "").strip(),
        "pid": os.getpid() if pid is None else int(pid),
    }
