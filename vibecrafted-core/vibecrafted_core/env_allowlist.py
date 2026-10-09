"""Allowlist for the environment a headless worker may inherit.

Interactive sessions keep the dispatcher environment with visible color policy.
Headless workers keep their independent no-color intent and allowlist.
Copying ``os.environ`` wholesale leaked the parent Claude bus
(``CLAUDE_CODE_MESSAGING_SOCKET`` / ``CLAUDE_CODE_MESSAGING_TOKEN``) into a
Codex worker, along with unrelated API tokens. One allowlist is the gate.

``selected_runtime_environment`` is not this gate. Message delivery still uses
that helper to address a selected runtime generation.

What passes the gate keeps the runtime python pin: ``pin_runtime_python``
puts the selected generation's python door first on PATH and points ZDOTDIR
at its guest directory, so the worker's own shells -- a provider's login-shell
snapshot, ``zsh -lc``, a hook -- reach ``VIBECRAFTED_PYTHON`` as python3.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from .runtime_paths import pin_runtime_python

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
        "NODE_DISABLE_COLORS",
        "ANSI_COLORS_DISABLED",
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
        # Copilot CLI login also accepts an OS credential-store token. For
        # automation these are its documented environment token sources.
        "COPILOT_GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "COPILOT_HOME",
        "COPILOT_MODEL",
        "COPILOT_AUTO_UPDATE",
        # Copilot CLI BYOK provider (`copilot help providers`): routes the
        # worker through an operator-chosen OpenAI-compatible endpoint, e.g.
        # a local Ollama server serving kimi-k3:cloud. COPILOT_PROVIDER_BASE_URL
        # is the only required var; the rest are optional per-provider tuning.
        # Listed explicitly, not by prefix, same as every other provider above.
        "COPILOT_PROVIDER_BASE_URL",
        "COPILOT_PROVIDER_TYPE",
        "COPILOT_PROVIDER_API_KEY",
        "COPILOT_PROVIDER_API_KEY_COMMAND",
        "COPILOT_PROVIDER_BEARER_TOKEN",
        "COPILOT_PROVIDER_WIRE_API",
        "COPILOT_PROVIDER_TRANSPORT",
        "COPILOT_PROVIDER_HEADERS",
        "COPILOT_PROVIDER_MODEL_ID",
        "COPILOT_PROVIDER_WIRE_MODEL",
        "COPILOT_PROVIDER_MAX_PROMPT_TOKENS",
        "COPILOT_PROVIDER_MAX_OUTPUT_TOKENS",
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


# Visible product entries override inherited rendering opt-outs, including a
# long-lived Frame server's environment. Never apply this to headless workers
# or ordinary pipe output. TERM/COLORTERM remain the capability authority; only
# an absent/dumb TERM is repaired for the explicitly selected terminal.
_COLOR_DISABLE_KEYS = ("NO_COLOR", "NODE_DISABLE_COLORS", "ANSI_COLORS_DISABLED")


def visible_color_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Copy one explicitly visible child's environment, preserving capabilities."""
    child = dict(source)
    for key in _COLOR_DISABLE_KEYS:
        child.pop(key, None)
    if not child.get("TERM") or child["TERM"] == "dumb":
        child["TERM"] = "xterm-256color"
    level = "1"
    if child.get("COLORTERM") in {"truecolor", "24bit"}:
        level = "3"
    elif "256color" in child["TERM"]:
        level = "2"
    child.update(FORCE_COLOR=level, CLICOLOR="1", CLICOLOR_FORCE="1")
    return child


def visible_color_shell_prelude() -> str:
    """Normalize the pane's environment, not the dispatcher's stale snapshot.

    This is for explicitly visible provider/workflow output, including tee.
    Native terminals/profiles only remove opt-outs and enable TTY detection;
    they leave FORCE_COLOR/CLICOLOR_FORCE unset so ordinary pipes stay plain.
    """
    return (
        "unset " + " ".join(_COLOR_DISABLE_KEYS) + "\n"
        'case "${TERM:-dumb}" in dumb) export TERM=xterm-256color ;; esac\n'
        'case "${COLORTERM:-}" in\n'
        "  truecolor|24bit) export FORCE_COLOR=3 ;;\n"
        '  *) case "$TERM" in *256color*) export FORCE_COLOR=2 ;; *) export FORCE_COLOR=1 ;; esac ;;\n'
        "esac\n"
        "export CLICOLOR=1 CLICOLOR_FORCE=1\n"
    )


def env_key_allowed(name: str) -> bool:
    """Return whether one environment name may enter a headless worker."""
    if name in _PROCESS_ALLOW or name in _PROVIDER_ALLOW:
        return True
    return any(name.startswith(prefix) for prefix in _PREFIX_ALLOW)


def filter_headless_worker_env(source: Mapping[str, str]) -> dict[str, str]:
    """Copy only allowlisted string entries, then carry the runtime python pin.

    Non-string values are dropped. ``subprocess`` environments are strings,
    and a non-string would be a programming error rather than a secret to pass.
    The inherited ZDOTDIR does not pass; a selected generation that carries
    the python door replaces it with its guest directory (see
    ``runtime_paths.pin_runtime_python``).
    """
    return pin_runtime_python(
        {
            key: value
            for key, value in source.items()
            if isinstance(key, str) and isinstance(value, str) and env_key_allowed(key)
        }
    )


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
