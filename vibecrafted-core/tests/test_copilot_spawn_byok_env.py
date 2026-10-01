"""copilot_spawn.sh's config.toml -> BYOK env translation.

Exercises the exact `eval "$(spawn_python_module vibecrafted_core.server_config
copilot-provider-env ...)"` line copilot_spawn.sh runs, under an isolated HOME,
without needing a real `copilot` binary or a full launcher lifecycle.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SCRIPTS = REPO_ROOT / "vibecrafted_core" / "runtime" / "scripts"
COMMON_SH = RUNTIME_SCRIPTS / "common.sh"


def _run_byok_eval(
    tmp_path: Path, config_body: str | None, *, extra_env: dict[str, str] | None = None
) -> dict[str, str]:
    """Run copilot_spawn.sh's config->env line under isolated HOME/config.toml
    and return the COPILOT_MODEL / COPILOT_PROVIDER_* vars it exported."""

    home = tmp_path / "home"
    config_dir = home / ".config" / "vibecrafted"
    config_dir.mkdir(parents=True)
    if config_body is not None:
        (config_dir / "config.toml").write_text(config_body, encoding="utf-8")

    script = (
        f'source "{COMMON_SH}"\n'
        'eval "$(spawn_python_module vibecrafted_core.server_config '
        'copilot-provider-env 2>/dev/null || true)"\n'
        'env | grep -E "^(COPILOT_MODEL|COPILOT_PROVIDER_[A-Z0-9_]*)=" || true\n'
    )
    env = {
        key: value
        for key, value in os.environ.items()
        if key != "COPILOT_MODEL" and not key.startswith("COPILOT_PROVIDER_")
    }
    env["HOME"] = str(home)
    env.pop("XDG_CONFIG_HOME", None)
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            rendered[key] = value
    return rendered


def test_no_config_means_no_byok_vars(tmp_path: Path) -> None:
    """Absent [agents.copilot.provider] => today's behavior, no new env vars."""
    assert _run_byok_eval(tmp_path, None) == {}


def test_configured_provider_sets_byok_env(tmp_path: Path) -> None:
    """The Ollama kimi-k3:cloud example from the docs, end to end."""
    rendered = _run_byok_eval(
        tmp_path,
        "[agents.copilot.provider]\n"
        'base_url = "http://localhost:11434/v1"\n'
        'model = "kimi-k3:cloud"\n',
    )
    assert rendered == {
        "COPILOT_PROVIDER_BASE_URL": "http://localhost:11434/v1",
        "COPILOT_MODEL": "kimi-k3:cloud",
    }


def test_explicit_env_export_wins_over_config(tmp_path: Path) -> None:
    """An operator export for this one invocation beats the config.toml pin."""
    rendered = _run_byok_eval(
        tmp_path,
        "[agents.copilot.provider]\n"
        'base_url = "http://localhost:11434/v1"\n'
        'model = "kimi-k3:cloud"\n',
        extra_env={"COPILOT_MODEL": "gpt-5.4"},
    )
    assert rendered["COPILOT_MODEL"] == "gpt-5.4"
    assert rendered["COPILOT_PROVIDER_BASE_URL"] == "http://localhost:11434/v1"


def test_copilot_spawn_sh_loads_byok_config_before_reading_copilot_model() -> None:
    """Static wiring check: the config->env eval runs before the script seeds
    its own `model` shell variable from `COPILOT_MODEL`."""
    body = (RUNTIME_SCRIPTS / "copilot_spawn.sh").read_text(encoding="utf-8")
    eval_index = body.index("vibecrafted_core.server_config copilot-provider-env")
    model_seed_index = body.index('model="${COPILOT_MODEL:-}"')
    assert eval_index < model_seed_index
