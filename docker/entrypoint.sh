#!/usr/bin/env bash
set -euo pipefail

export VIBECRAFTED_ROOT="${VIBECRAFTED_ROOT:-/workspace}"
export VIBECRAFTED_HOME="${VIBECRAFTED_HOME:-$VIBECRAFTED_ROOT/.vibecrafted}"
export VIBECRAFTED_SOURCE="${VIBECRAFTED_SOURCE:-/opt/vibecrafted}"

mkdir -p \
  "$VIBECRAFTED_ROOT" \
  "$VIBECRAFTED_HOME" \
  "$VIBECRAFTED_HOME/bin" \
  "$VIBECRAFTED_HOME/logs" \
  "$VIBECRAFTED_HOME/reports" \
  "$VIBECRAFTED_HOME/tmp"

if [[ "${VIBECRAFTED_DOCKER_SEED_SKILLS:-1}" == "1" ]]; then
  skills_dir="$VIBECRAFTED_HOME/skills"
  seed_file="$VIBECRAFTED_HOME/.docker-skills-version"
  source_version="$(cat "$VIBECRAFTED_SOURCE/VERSION" 2>/dev/null || printf 'unknown')"
  should_seed=0

  if [[ ! -d "$skills_dir" ]]; then
    should_seed=1
  elif [[ -f "$seed_file" && "$(cat "$seed_file")" != "$source_version" ]]; then
    should_seed=1
  elif [[ -z "$(find "$skills_dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    should_seed=1
  fi

  if [[ "$should_seed" == "1" ]]; then
    rm -rf "$skills_dir"
    mkdir -p "$skills_dir"
    cp -a \
      "$VIBECRAFTED_SOURCE/vibecrafted-core/vibecrafted_core/skills/." \
      "$skills_dir/"
    printf '%s\n' "$source_version" > "$seed_file"
  fi
fi

export PATH="$VIBECRAFTED_SOURCE/scripts:$VIBECRAFTED_HOME/bin:/root/.local/bin:$PATH"

cd "$VIBECRAFTED_ROOT"

if [[ $# -eq 0 ]]; then
  set -- help
fi

case "$1" in
  bash|sh|zsh|node|npm|npx|python|python3|uv|git|make|rg|jq|curl|tar|unzip)
    exec "$@"
    ;;
  loctree|loctree-mcp|aicx|aicx-mcp|prview|screenscribe|codex|claude|gemini|cursor-agent)
    exec "$@"
    ;;
  vibecrafted|vibecraft)
    exec "$@"
    ;;
  *)
    exec vibecrafted "$@"
    ;;
esac
