"""copilot_spawn.sh's config.toml -> BYOK env translation.

Exercises the exact BYOK validation block copilot_spawn.sh runs (extracted
verbatim from the shipped script, so this suite can never drift from it),
under an isolated HOME and a minimal subprocess environment, without needing
a real `copilot` binary or a full launcher lifecycle.

The subprocess environment is built from scratch (PATH + an isolated HOME
only) rather than copied from os.environ: copying would leak ambient
VIBECRAFTED_RUN_ID / VIBECRAFTED_META_PATH / VIBECRAFTED_HOME vars from a
live vibecrafted session into the child, and the invalid-config case below
calls spawn_die -- which, under those ambient vars, tries to settle the
*live* run's control-plane meta.json instead of staying a harmless no-op.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SCRIPTS = REPO_ROOT / "vibecrafted_core" / "runtime" / "scripts"
COMMON_SH = RUNTIME_SCRIPTS / "common.sh"
COPILOT_SPAWN_SH = RUNTIME_SCRIPTS / "copilot_spawn.sh"

_BLOCK_START = "_copilot_provider_env_rc=0"
_BLOCK_END = "unset _copilot_provider_env_rc _copilot_provider_env_out"


def _byok_validation_block() -> str:
    """The BYOK config->env validation block, extracted verbatim from
    copilot_spawn.sh between its start/end sentinel lines."""

    body = COPILOT_SPAWN_SH.read_text(encoding="utf-8")
    start = body.index(_BLOCK_START)
    end = body.index(_BLOCK_END, start) + len(_BLOCK_END)
    return body[start:end]


def _run_byok_eval(
    tmp_path: Path, config_body: str | None, *, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Run copilot_spawn.sh's BYOK validation block under an isolated
    HOME/config.toml and a minimal (non-inherited) environment."""

    home = tmp_path / "home"
    config_dir = home / ".config" / "vibecrafted"
    config_dir.mkdir(parents=True)
    if config_body is not None:
        (config_dir / "config.toml").write_text(config_body, encoding="utf-8")

    script = (
        f'source "{COMMON_SH}"\n'
        f"{_byok_validation_block()}\n"
        'env | grep -E "^(COPILOT_MODEL|COPILOT_PROVIDER_[A-Z0-9_]*)=" || true\n'
    )
    # Deliberately minimal: PATH (to find bash/python3) + an isolated HOME.
    # No VIBECRAFTED_*, no XDG_CONFIG_HOME, no inherited COPILOT_* -- see
    # module docstring for why copying os.environ here is unsafe.
    env: dict[str, str] = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    env["HOME"] = str(home)
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _rendered_vars(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            rendered[key] = value
    return rendered


def test_no_config_means_no_byok_vars(tmp_path: Path) -> None:
    """Absent [agents.copilot.provider] => today's behavior, no new env vars,
    no error."""
    result = _run_byok_eval(tmp_path, None)
    assert result.returncode == 0, result.stderr
    assert _rendered_vars(result) == {}


def test_configured_provider_sets_byok_env(tmp_path: Path) -> None:
    """The Ollama kimi-k3:cloud example from the docs, end to end."""
    result = _run_byok_eval(
        tmp_path,
        "[agents.copilot.provider]\n"
        'base_url = "http://localhost:11434/v1"\n'
        'model = "kimi-k3:cloud"\n',
    )
    assert result.returncode == 0, result.stderr
    assert _rendered_vars(result) == {
        "COPILOT_PROVIDER_BASE_URL": "http://localhost:11434/v1",
        "COPILOT_MODEL": "kimi-k3:cloud",
    }


def test_explicit_env_export_wins_over_config(tmp_path: Path) -> None:
    """An operator export for this one invocation beats the config.toml pin."""
    result = _run_byok_eval(
        tmp_path,
        "[agents.copilot.provider]\n"
        'base_url = "http://localhost:11434/v1"\n'
        'model = "kimi-k3:cloud"\n',
        extra_env={"COPILOT_MODEL": "gpt-5.4"},
    )
    assert result.returncode == 0, result.stderr
    rendered = _rendered_vars(result)
    assert rendered["COPILOT_MODEL"] == "gpt-5.4"
    assert rendered["COPILOT_PROVIDER_BASE_URL"] == "http://localhost:11434/v1"


@pytest.mark.parametrize(
    ("label", "config_body", "expected_stderr_fragment"),
    [
        (
            "missing_base_url",
            '[agents.copilot.provider]\nmodel = "kimi-k3:cloud"\n',
            "base_url is required",
        ),
        (
            "typo_key",
            ('[agents.copilot.provider]\nbas_url = "http://localhost:11434/v1"\n'),
            "unsupported [agents.copilot.provider] key",
        ),
        (
            "malformed_toml",
            ('[agents.copilot.provider\nbase_url = "http://localhost:11434/v1"\n'),
            "invalid TOML",
        ),
    ],
)
def test_invalid_config_fails_closed_without_silent_default(
    tmp_path: Path, label: str, config_body: str, expected_stderr_fragment: str
) -> None:
    """A *present but invalid* [agents.copilot.provider] table must never
    fall back to Copilot's silent default model (the 2026-09-30 research.yaml
    class of bug): the spawn must hard-fail with a readable error, and must
    not export any COPILOT_* vars."""

    result = _run_byok_eval(tmp_path, config_body)
    assert result.returncode != 0, (label, result.stdout, result.stderr)
    assert "Invalid [agents.copilot.provider] config" in result.stderr, (
        label,
        result.stderr,
    )
    assert expected_stderr_fragment in result.stderr, (label, result.stderr)
    assert _rendered_vars(result) == {}, (label, result.stdout)


def test_copilot_spawn_sh_loads_byok_config_before_reading_copilot_model() -> None:
    """Static wiring check: the config->env validation runs before the script
    seeds its own `model` shell variable from `COPILOT_MODEL`."""
    body = COPILOT_SPAWN_SH.read_text(encoding="utf-8")
    eval_index = body.index("vibecrafted_core.server_config copilot-provider-env")
    model_seed_index = body.index('model="${COPILOT_MODEL:-}"')
    assert eval_index < model_seed_index


def test_copilot_spawn_sh_dies_on_invalid_config_without_evaluating_output() -> None:
    """Static wiring check: the validated block's `eval` line only runs after
    the exit-code gate, so an invalid config's captured output (an error
    message, not export statements) is never blindly eval'd."""
    body = COPILOT_SPAWN_SH.read_text(encoding="utf-8")
    block = _byok_validation_block()
    die_index = block.index("spawn_die")
    eval_index = block.index('eval "$_copilot_provider_env_out"')
    assert die_index < eval_index
    assert block in body
