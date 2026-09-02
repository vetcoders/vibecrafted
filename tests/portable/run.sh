#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# Portable simulates a stranger's clean machine. A live Vibecrafted worker
# shell exports runtime identity plus PYTHONPATH pinned at the HOST install;
# leaked into the sandboxed bootstrap they make uv-tool import the host's
# vibecrafted_core and trip the install-tools drift FATAL. Strip them all
# up front instead of per-invocation.
unset PYTHONPATH
for _ambient_var in $(compgen -e | grep -E '^(VIBECRAFTED_|VC_FRAME|ZELLIJ)'); do
  unset "$_ambient_var"
done
unset _ambient_var

log() {
  printf '[portable] %s\n' "$*"
}

die() {
  printf '[portable] FAIL: %s\n' "$*" >&2
  exit 1
}

require_file() {
  [[ -f "$1" ]] || die "Missing file: $1"
}

require_symlink() {
  [[ -L "$1" ]] || die "Missing symlink: $1"
}

wait_for_meta() {
  local meta_path="$1"
  local attempts="${2:-80}"
  local delay="${3:-0.25}"
  local status=""
  local i
  for ((i=0; i<attempts; i++)); do
    if [[ -f "$meta_path" ]]; then
      status="$(python3 - "$meta_path" <<'PY'
import json, sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    payload = json.load(fh)
print(payload.get('status') or '')
PY
)"
      case "$status" in
        completed|failed)
          printf '%s\n' "$status"
          return 0
          ;;
      esac
    fi
    sleep "$delay"
  done
  die "Timed out waiting for $meta_path"
}

assert_contains() {
  local file="$1"
  local pattern="$2"
  grep -Fq "$pattern" "$file" || die "Expected '$pattern' in $file"
}

assert_matches() {
  local file="$1"
  local pattern="$2"
  grep -Eq "$pattern" "$file" || die "Expected regex '$pattern' in $file"
}

assert_not_contains() {
  local file="$1"
  local pattern="$2"
  [[ -f "$file" ]] || die "assert_not_contains: file not found: $file"
  if grep -Fq "$pattern" "$file"; then
    die "Did not expect '$pattern' in $file"
  fi
}

assert_no_perception_watcher() {
  local root="$1"
  python3 - "$root" <<'PY' || die "Detached perception watcher escaped the portable sandbox: $root"
import subprocess
import sys

root = sys.argv[1]
needle = f"loct watch --dev {root}"
result = subprocess.run(
    ["ps", "-axo", "command="],
    check=True,
    capture_output=True,
    text=True,
)
if any(line.strip().endswith(needle) for line in result.stdout.splitlines()):
    raise SystemExit(1)
PY
}

print_installer_logs() {
  local home="$1"
  local log_dir="$home/.vibecrafted/logs/installer"
  local log_file

  if [[ ! -d "$log_dir" ]]; then
    printf '[portable] no installer logs found under %s\n' "$log_dir" >&2
    return 0
  fi

  while IFS= read -r log_file; do
    [[ -f "$log_file" ]] || continue
    printf '\n[portable] installer log: %s\n' "$log_file" >&2
    sed -n '1,220p' "$log_file" >&2 || true
  done < <(find "$log_dir" -type f -name '*.log' -print | sort)
}

log "syntax checks"
bash -n \
  "$repo_root/install.sh" \
  "$repo_root/vibecrafted-core/vibecrafted_core/runtime/scripts/install.sh" \
  "$repo_root/vibecrafted-core/vibecrafted_core/runtime/scripts/install-shell.sh" \
  "$repo_root/vibecrafted-core/vibecrafted_core/runtime/scripts/skills_sync.sh" \
  "$repo_root/vibecrafted-core/vibecrafted_core/runtime/scripts/observe.sh" \
  "$repo_root/vibecrafted-core/vibecrafted_core/runtime/scripts/common.sh" \
  "$repo_root/vibecrafted-core/vibecrafted_core/runtime/scripts/codex_spawn.sh" \
  "$repo_root/vibecrafted-core/vibecrafted_core/runtime/scripts/claude_spawn.sh" \
  "$repo_root/vibecrafted-core/vibecrafted_core/runtime/scripts/agy_spawn.sh"
# Shell helpers are bash-compatible; verify with bash -n
bash -n "$repo_root/vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
# If zsh is available, also verify zsh syntax
if command -v zsh >/dev/null 2>&1; then
  zsh -n "$repo_root/vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
fi

workspace="$(mktemp -d)"
# macOS reports TMPDIR through the compatibility /var symlink.  Canonicalize
# the sandbox root before it becomes HOME so the installer's no-follow payload
# transaction proves a physical path instead of correctly rejecting /var.
workspace="$(cd "$workspace" && pwd -P)"
cleanup_workspace() {
  local status=$?
  rm -rf "$workspace" 2>/dev/null || {
    sleep 0.5
    rm -rf "$workspace" 2>/dev/null || printf '[portable] warn: could not remove temp workspace: %s\n' "$workspace" >&2
  }
  exit "$status"
}
trap cleanup_workspace EXIT
bootstrap_home="$workspace/bootstrap-home"
bootstrap_config_dir="$bootstrap_home/.config"
home_dir="$workspace/home"
config_dir="$home_dir/.config"
work_repo="$workspace/workrepo"
fake_bin="$home_dir/.local/bin"
bootstrap_archive="$workspace/vibecrafted-bootstrap.tar.gz"
mkdir -p "$bootstrap_home" "$bootstrap_config_dir" "$home_dir" "$config_dir" "$work_repo" "$fake_bin"

# Product spawns intentionally detach one perception watcher per durable repo.
# This test repo is ephemeral and deleted by the EXIT trap, so keep that
# orthogonal daemon disabled here; perception lifecycle has its own core suite.
export VIBECRAFTED_PERCEPTION_WATCH=0

log "bootstrap smoke via root install.sh"
read -r bootstrap_source_owner bootstrap_source_revision < <(
  python3 - "$repo_root" <<'PY'
from pathlib import Path
import sys

repo_root = Path(sys.argv[1])
sys.path.insert(0, str(repo_root))
from scripts.distribution_manifest import resolve_source_provenance

provenance = resolve_source_provenance(
    repo_root,
    owner_repo=None,
    source_revision=None,
)
print(provenance["owner_repo"], provenance["source_revision"])
PY
)
python3 "$repo_root/scripts/distribution_manifest.py" archive \
  --source "$repo_root" \
  --output "$bootstrap_archive" \
  --root-name vibecrafted-bootstrap \
  --owner-repo "$bootstrap_source_owner" \
  --source-revision "$bootstrap_source_revision"
# The portable sandbox shares the operator's launchd user domain. Install the
# complete payload without claiming or mutating the host's fixed service label.
if ! HOME="$bootstrap_home" XDG_CONFIG_HOME="$bootstrap_config_dir" VIBECRAFTED_HOME="$bootstrap_home/.vibecrafted" INSTALL_SERVER_SERVICE_POLICY=isolated \
  bash "$repo_root/install.sh" --archive-file "$bootstrap_archive" install-source; then
  print_installer_logs "$bootstrap_home"
  die "root install.sh bootstrap failed"
fi

require_symlink "$bootstrap_home/.local/share/vibecrafted/tools/vibecrafted-current"
require_file "$bootstrap_home/.local/share/vibecrafted/tools/vibecrafted-current/Makefile"
require_file "$bootstrap_home/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/scripts/codex_spawn.sh"
# Source-lane contract (test_install_all_paths_do_not_install_shell_helpers_by_default):
# the explicit portable maintainer lane (install.sh -> make install-source) installs
# tools and views but does NOT wire the legacy shell helpers or touch shell rc files.
# Shell-helper generation is an explicit opt-in, exercised by the `--with-shell`
# install smoke below, so the bootstrap does not assert vc-skills.sh here.

log "install smoke into clean HOME"
HOME="$home_dir" XDG_CONFIG_HOME="$config_dir" \
  bash "$repo_root/vibecrafted-core/vibecrafted_core/runtime/scripts/install.sh" \
  --source "$repo_root" \
  --tool codex --tool claude --tool agy \
  --with-shell --write-shell-rc

# Stage the uv-tool launcher shim. The granular installer wires
# ~/.local/bin/vibecrafted as a symlink onto the uv-tool shim (the live launcher
# contract — see test_keys), but only `make install-python-tools` actually
# materializes that shim via `uv tool install`. The bootstrap above runs the
# full `make install` (which includes it); this clean-HOME smoke uses the
# granular installer, so create the shim here too — otherwise the launcher
# symlink dangles and the resume smoke below cannot exec it.
log "stage python launcher tools (uv-tool shim)"
HOME="$home_dir" XDG_CONFIG_HOME="$config_dir" INSTALL_TOOLS_SERVICE_POLICY=isolated \
  make --no-print-directory -C "$repo_root" install-python-tools

require_file "$home_dir/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/scripts/codex_spawn.sh"
require_file "$home_dir/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/scripts/claude_spawn.sh"
require_file "$home_dir/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/scripts/agy_spawn.sh"
require_file "$home_dir/.local/bin/vibecrafted"
require_symlink "$home_dir/.local/bin/vc-help"
require_symlink "$home_dir/.local/bin/vc-marbles"
# Explicit --tool selections keep their requested compatibility views.
require_symlink "$home_dir/.agents/skills/vc-agents"
require_symlink "$home_dir/.codex/skills/vc-agents"
require_symlink "$home_dir/.claude/skills/vc-agents"
require_file "$home_dir/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/scripts/codex_spawn.sh"
require_file "$home_dir/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/scripts/claude_spawn.sh"
require_file "$home_dir/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/scripts/agy_spawn.sh"
# Canonical + compat helper locations
require_file "$config_dir/vetcoders/vc-skills.sh"
require_file "$config_dir/zsh/vc-skills.zsh"
assert_contains "$config_dir/vetcoders/vc-skills.sh" '𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. helper shim'
bad_helper_candidate="\${VIBECRAFTED_ROOT:-}/runtime/shell/vetcoders.sh"
assert_not_contains "$config_dir/vetcoders/vc-skills.sh" "$bad_helper_candidate"
assert_not_contains "$config_dir/vetcoders/vc-skills.sh" "vibecrafted-current/runtime/shell/vetcoders.sh"
# Host-shell helper sourcing is intentionally retired (install-shell.sh:
# the helper is loaded by vc-start, never by the ordinary host shell).
# --write-shell-rc now means: PATH-only launcher guard in an rcfile, and any
# legacy vc-skills sourcing REMOVED. Assert the new contract both ways.
rc_found=0
for rcfile in "$home_dir/.zshrc" "$home_dir/.bashrc"; do
  [[ -f "$rcfile" ]] || continue
  if grep -Fq 'vc-skills' "$rcfile"; then
    die "rcfile $rcfile still sources vc-skills (retired host-shell contract)"
  fi
  # shellcheck disable=SC2016  # literal $HOME is the rc line's actual text
  grep -Fq '$HOME/.local/bin' "$rcfile" && rc_found=1
done
(( rc_found )) || die "No rcfile carries the PATH-only launcher guard"

log "prepare fake repo and fake agent CLIs"
git -C "$work_repo" init -q
mkdir -p "$work_repo/.vibecrafted/plans"
cat > "$work_repo/.vibecrafted/plans/test.md" <<'PLAN'
# Test plan
- Prove the portable spawn runtime can create artifacts.
PLAN

cat > "$fake_bin/codex" <<'EOF_CODEX'
#!/usr/bin/env bash
set -euo pipefail
case "${1:-}" in
  --version)
    echo "codex-cli 0.999.0-portable-fixture"
    exit 0
    ;;
  --help)
    echo "Usage: codex [exec|resume]"
    exit 0
    ;;
esac
report=""
json_mode=0
if [[ -n "${FAKE_CODEX_CAPTURE:-}" ]]; then
  printf "%s\n" "$@" > "$FAKE_CODEX_CAPTURE"
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-last-message)
      shift
      report="$1"
      ;;
    --json)
      json_mode=1
      ;;
  esac
  shift || true
done
if [[ -n "${FAKE_CODEX_STDIN_CAPTURE:-}" ]]; then
  cat > "$FAKE_CODEX_STDIN_CAPTURE"
else
  cat >/dev/null || true
fi
if (( json_mode )); then
  printf '{"type":"thread.started","thread_id":"fake-session-001"}\n'
  printf '{"type":"item.started","item":{"type":"command_execution","command":"ls"}}\n'
  printf '{"type":"item.completed","item":{"type":"command_execution","output":"alpha\\nbeta\\n"}}\n'
  printf '{"type":"turn.started"}\n'
  printf '{"type":"item.completed","item":{"type":"agent_message","text":"Fake Codex Report: spawn ok"}}\n'
  printf '{"type":"turn.completed","usage":{"input_tokens":100,"output_tokens":10}}\n'
else
  echo 'fake codex stdout'
fi
if [[ -n "$report" ]]; then
  cat > "$report" <<EOF_REPORT
---
agent: codex
run_id: ${SPAWN_RUN_ID:-missing-run-id}
prompt_id: ${SPAWN_PROMPT_ID:-missing-prompt-id}
started_at: 2026-03-27T17:47:00Z
model: fake-codex
---

# Fake Codex Report

spawn ok
EOF_REPORT
fi
EOF_CODEX

cat > "$fake_bin/claude" <<'EOF_CLAUDE'
#!/usr/bin/env bash
set -euo pipefail
echo '{"type":"system","subtype":"init","session_id":"fake-claude-001"}'
echo '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Read"},{"type":"text","text":"fake claude stream"}]}}'
echo '{"type":"result","result":"Fake Claude final handoff"}'
EOF_CLAUDE

cat > "$fake_bin/agy" <<'EOF_AGY'
#!/usr/bin/env bash
set -euo pipefail
echo 'fake agy stdout'
# minimal model line for telemetry smoke if transcript present
printf 'model: agy-test-model\n' >> "${SPAWN_TRANSCRIPT:-/dev/null}" 2>/dev/null || true
EOF_AGY

chmod +x "$fake_bin/codex" "$fake_bin/claude" "$fake_bin/agy"

common_env=(
  HOME="$home_dir"
  XDG_CONFIG_HOME="$config_dir"
  PATH="$fake_bin:$PATH"
  VIBECRAFTED_RUN_ID=""
  VIBECRAFTED_PROMPT_ID=""
)

log "headless spawn smoke"
env "${common_env[@]}" bash "$home_dir/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/scripts/codex_spawn.sh" --mode plan --runtime headless --root "$work_repo" "$work_repo/.vibecrafted/plans/test.md"
env "${common_env[@]}" bash "$home_dir/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/scripts/claude_spawn.sh" --mode review --runtime headless --root "$work_repo" "$work_repo/.vibecrafted/plans/test.md"
env "${common_env[@]}" bash "$home_dir/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/scripts/agy_spawn.sh" --mode implement --runtime headless --root "$work_repo" "$work_repo/.vibecrafted/plans/test.md"

codex_meta="$(find "$work_repo/.vibecrafted/reports" -maxdepth 1 -type f -name '*_codex.meta.json' | sort | tail -n 1)"
claude_meta="$(find "$work_repo/.vibecrafted/reports" -maxdepth 1 -type f -name '*_claude.meta.json' | sort | tail -n 1)"
agy_meta="$(find "$work_repo/.vibecrafted/reports" -maxdepth 1 -type f -name '*_agy.meta.json' | sort | tail -n 1)"

require_file "$codex_meta"
require_file "$claude_meta"
require_file "$agy_meta"

[[ "$(wait_for_meta "$codex_meta")" == "completed" ]] || die "codex spawn did not complete"
[[ "$(wait_for_meta "$claude_meta")" == "completed" ]] || die "claude spawn did not complete"
[[ "$(wait_for_meta "$agy_meta")" == "completed" ]] || die "agy spawn did not complete"

codex_report="$(python3 - "$codex_meta" <<'PY'
import json, sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    print(json.load(fh)['report'])
PY
)"
claude_report="$(python3 - "$claude_meta" <<'PY'
import json, sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    print(json.load(fh)['report'])
PY
)"
agy_report="$(python3 - "$agy_meta" <<'PY'
import json, sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    print(json.load(fh)['report'])
PY
)"
codex_transcript="$(python3 - "$codex_meta" <<'PY'
import json, sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    print(json.load(fh)['transcript'])
PY
)"
claude_transcript="$(python3 - "$claude_meta" <<'PY'
import json, sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    print(json.load(fh)['transcript'])
PY
)"
agy_transcript="$(python3 - "$agy_meta" <<'PY'
import json, sys
with open(sys.argv[1], 'r', encoding='utf-8') as fh:
    print(json.load(fh)['transcript'])
PY
)"

require_file "$codex_report"
require_file "$claude_report"
require_file "$agy_report"
require_file "$codex_transcript"
require_file "$claude_transcript"
require_file "$agy_transcript"
assert_contains "$codex_report" 'Fake Codex Report'
assert_matches "$codex_report" 'run_id: plan-[0-9]{6}'
assert_contains "$codex_report" 'prompt_id: test_'
# claude_spawn.sh now salvages the captured final message into the report when
# the agent writes no standalone report file (salvage_success_report), instead
# of the old "completed without writing a standalone report file" placeholder.
# The fake claude emits that final handoff above, so assert it was salvaged.
assert_contains "$claude_report" 'Fake Claude final handoff'
assert_contains "$agy_report" 'fake agy'
assert_matches "$codex_transcript" '\[[0-9]{2}:[0-9]{2}:[0-9]{2} \$ ls\]'
assert_matches "$codex_transcript" '\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] tokens: 100 in / 10 out'
assert_matches "$claude_transcript" '\[[0-9]{2}:[0-9]{2}:[0-9]{2}\] session: fake-claude-001'
assert_matches "$claude_transcript" '\[[0-9]{2}:[0-9]{2}:[0-9]{2} Read\]'
assert_matches "$agy_transcript" 'agy-test-model'

jq -e '.prompt_id != null and (.prompt_id | startswith("test_"))' "$codex_meta" >/dev/null || die "codex meta missing prompt_id"
jq -e '.run_id | test("^plan-[0-9]{6}-[0-9]{6}-[0-9]{5}$")' "$codex_meta" >/dev/null || die "codex meta has non-canonical plan run_id"
jq -e '.run_id | test("^rvew-[0-9]{6}-[0-9]{6}-[0-9]{5}$")' "$claude_meta" >/dev/null || die "claude meta has non-canonical review run_id"
jq -e '.run_id | test("^impl-[0-9]{6}-[0-9]{6}-[0-9]{5}$")' "$agy_meta" >/dev/null || die "agy meta has non-canonical implement run_id"
jq -e '.loop_nr == 0' "$codex_meta" >/dev/null || die "codex meta missing loop_nr"
jq -e '.framework_version != null and .framework_version != ""' "$codex_meta" >/dev/null || die "codex meta missing framework_version"
jq -e '.completed_at != null and .duration_s != null' "$codex_meta" >/dev/null || die "codex meta missing completion telemetry"
jq -e '.liveness == "terminal"' "$codex_meta" >/dev/null || die "codex meta missing terminal liveness"

log "launcher resume smoke"
resume_capture="$workspace/resume-codex.txt"
resume_prompt_capture="$workspace/resume-codex-prompt.txt"
resume_output="$(
  env -u VIBECRAFTED_RUN_ID -u VIBECRAFTED_OPERATOR_SESSION \
    -u VC_FRAME -u VC_FRAME_PANE_ID -u VC_FRAME_SESSION_NAME \
    -u ZELLIJ -u ZELLIJ_PANE_ID -u ZELLIJ_SESSION_NAME \
    HOME="$home_dir" XDG_CONFIG_HOME="$config_dir" PATH="$fake_bin:$PATH" \
    FAKE_CODEX_CAPTURE="$resume_capture" \
    FAKE_CODEX_STDIN_CAPTURE="$resume_prompt_capture" \
    "$home_dir/.local/bin/vibecrafted" resume codex \
      --session fake-session-001 --prompt "resume smoke"
)"
printf '%s\n' "$resume_output"
resume_run_id="$(
  printf '%s\n' "$resume_output" |
    sed -n 's/^run_id:[[:space:]]*//p' |
    tail -n 1
)"
[[ -n "$resume_run_id" ]] || die "resume receipt did not expose a run_id"
env -u VIBECRAFTED_RUN_ID -u VIBECRAFTED_OPERATOR_SESSION \
  -u VC_FRAME -u VC_FRAME_PANE_ID -u VC_FRAME_SESSION_NAME \
  -u ZELLIJ -u ZELLIJ_PANE_ID -u ZELLIJ_SESSION_NAME \
  HOME="$home_dir" XDG_CONFIG_HOME="$config_dir" PATH="$fake_bin:$PATH" \
  "$home_dir/.local/bin/vibecrafted" await codex \
    --run-id "$resume_run_id" --timeout 20 --interval 0.1 --status-interval 20
require_file "$resume_capture"
require_file "$resume_prompt_capture"
assert_contains "$resume_capture" 'resume'
assert_contains "$resume_capture" 'fake-session-001'
assert_contains "$resume_prompt_capture" 'resume smoke'

log "helper bash smoke"
# shellcheck disable=SC2016
env HOME="$home_dir" XDG_CONFIG_HOME="$config_dir" PATH="$home_dir/.local/bin:$fake_bin:$PATH" \
  bash -c 'source "${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh"; command -v codex-implement >/dev/null && command -v claude-implement >/dev/null && command -v agy-implement >/dev/null && command -v vc-marbles >/dev/null && command -v skills-sync >/dev/null && echo helper-ok' \
  | grep -Fq 'helper-ok' || die 'bash helper layer not loaded'
log "skill helper telemetry smoke"
# shellcheck disable=SC2016
skill_output="$(
  env HOME="$home_dir" XDG_CONFIG_HOME="$config_dir" PATH="$fake_bin:$PATH" VETCODERS_SPAWN_RUNTIME=headless \
    bash -c 'cd "$1"; source "${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh"; codex-marbles --count 1 --prompt "telemetry smoke"' _ "$work_repo"
)"
skill_report="$(printf '%s\n' "$skill_output" | sed -n 's/^Agent launched\. Report will land at: //p' | tail -n 1)"
[[ -n "$skill_report" ]] || die "skill helper did not report output path"
# The "Report will land at:" line is a pre-run preview path; the materialized
# report/meta carry the marbles run-id infix (..._marb-<id>-001_...), so derive
# the meta by locating the actual file (same glob pattern the spawn smokes above
# use) rather than from the announced path.
skill_meta="$(find "$work_repo/.vibecrafted/marbles/reports" -maxdepth 1 -type f -name '*_marbles-ancestor_L1_codex.meta.json' | sort | tail -n 1)"
[[ -n "$skill_meta" ]] || die "skill helper marbles meta not found"
require_file "$skill_meta"
[[ "$(wait_for_meta "$skill_meta")" == "completed" ]] || die "skill helper spawn did not complete"
jq -e '.skill_code == "marb"' "$skill_meta" >/dev/null || die "skill helper did not wire skill_code"
jq -e '.run_id | startswith("marb-")' "$skill_meta" >/dev/null || die "skill helper did not wire run_id"
jq -e '.liveness == "terminal"' "$skill_meta" >/dev/null || die "skill helper did not finish with terminal liveness"
assert_no_perception_watcher "$work_repo"

# If zsh is available, also smoke test zsh loading via compat symlink
if command -v zsh >/dev/null 2>&1; then
  log "helper zsh smoke (bonus)"
  # shellcheck disable=SC2016
  env HOME="$home_dir" XDG_CONFIG_HOME="$config_dir" PATH="$home_dir/.local/bin:$fake_bin:$PATH" \
    zsh -c 'source "${XDG_CONFIG_HOME:-$HOME/.config}/zsh/vc-skills.zsh"; command -v codex-implement >/dev/null && command -v claude-implement >/dev/null && command -v agy-implement >/dev/null && command -v vc-marbles >/dev/null && command -v skills-sync >/dev/null && echo helper-ok' \
    | grep -Fq 'helper-ok' || die 'zsh helper layer not loaded'
fi

log "sync dry-run smoke"
cat > "$fake_bin/ssh" <<'EOF_SSH'
#!/usr/bin/env bash
# mock ssh just echoes the command
shift
echo "$@"
EOF_SSH
chmod +x "$fake_bin/ssh"

cat > "$fake_bin/rsync" <<'EOF_RSYNC'
#!/usr/bin/env bash
# mock rsync just echoes args
echo rsync "$@"
EOF_RSYNC
chmod +x "$fake_bin/rsync"

sync_output="$(env HOME="$home_dir" XDG_CONFIG_HOME="$config_dir" PATH="$fake_bin:$PATH" bash "$repo_root/vibecrafted-core/vibecrafted_core/runtime/scripts/skills_sync.sh" fakehost --source "$repo_root" --dry-run)"
grep -q "Syncing skills from" <<<"$sync_output" || die "Sync dry-run failed to start"
grep -q '^  rsync ' <<<"$sync_output" || die "Sync dry-run didn't print planned rsync commands"
! grep -q '^rsync ' <<<"$sync_output" || die "Sync dry-run executed rsync instead of printing it"
# shellcheck disable=SC2016 # matching literal $HOME in sync output, not expanding
grep -q '\$HOME/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/skills' <<<"$sync_output" || die "Sync dry-run didn't target the package-owned canonical skill store"
# shellcheck disable=SC2016 # matching literal $HOME in sync output, not expanding
! grep -q '\$HOME/.vibecrafted/skills' <<<"$sync_output" || die "Sync dry-run still targets the legacy state-home skill store"

log "docs truth checks"
# shellcheck disable=SC2016 # backticks are literal content we're matching, not command substitution
assert_not_contains "$repo_root/vibecrafted-core/vibecrafted_core/skills/vc-followup/SKILL.md" 'Use canonical Terminal spawn (`osascript`)'
assert_not_contains "$repo_root/vibecrafted-core/vibecrafted_core/skills/vc-workflow/SKILL.md" 'osascript preferred'
assert_not_contains "$repo_root/docs/FRONTIER.md" 'vetcoders.zsh'
assert_not_contains "$repo_root/docs/FAQ-ANSWERED.md" 'truth as of March 2026'
[[ ! -e "$repo_root/vibecrafted-core/vibecrafted_core/skills/vc-subagents/SKILL.md" ]] || die 'vc-subagents should not exist'
if [[ -e "$repo_root/docs/index.html" ]]; then
  assert_not_contains "$repo_root/docs/index.html" 'Canonical osascript Terminal spawn'
  assert_contains "$repo_root/docs/index.html" 'https://vibecrafted.io/'
  assert_contains "$repo_root/docs/index.html" 'window.location.replace("https://vibecrafted.io/")'
  assert_not_contains "$repo_root/docs/index.html" "The Founders' Framework"
fi
assert_contains "$repo_root/docs/QUICK_START.md" 'vibecrafted init claude'
# Canonical command shape only — no hardcoded prompt text (brittle), no
# `justdo` in public quickstart copy (backward-compatible CLI but not an advertised surface per the
# canonical rename to `vc-implement`).
assert_contains "$repo_root/docs/QUICK_START.md" 'vibecrafted implement codex'
assert_contains "$repo_root/docs/presence/quickstart.html" 'https://vibecrafted.io/en/quickstart/'
assert_contains "$repo_root/docs/presence/quickstart.html" 'window.location.replace("https://vibecrafted.io/en/quickstart/")'
assert_not_contains "$repo_root/docs/presence/quickstart.html" 'vibecrafted workflow claude --prompt "Plan and implement auth module"'
[[ -e "$repo_root/vibecrafted-core/vibecrafted_core/skills/vc-suite-showcase.html" ]] && die 'vc-suite-showcase.html should not exist (was mv to docs/index.html)'

log "portable checks passed"
log "portable checks passed"
