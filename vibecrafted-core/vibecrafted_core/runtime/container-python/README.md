# vibecrafted-core

Core Python library for Vibecrafted runtime surfaces.

This package exposes the stable runtime path helpers, control-plane state
normalization, workflow launch construction, repository status inspection,
and doctor summaries needed by sibling packages such as `vibecrafted-mcp`.

Install from the repository root:

```bash
pip install -e ./vibecrafted-core
```

Smoke the public API:

```bash
python -c "from vibecrafted_core import vibecrafted_home, repo_full, doctor_run; print(repo_full('.'))"
```

## iTerm2 plugin

The iTerm2 AutoLaunch plugin, OSC helpers, and Dynamic Profiles installer
live in the standalone package at `plugins/iterm2`.

```bash
pip install -e ./plugins/iterm2
python -m vibecrafted_iterm2.install_autolaunch --help
```

## Tests

```bash
uv run --project . --with pytest python -m pytest tests/ -q
```
