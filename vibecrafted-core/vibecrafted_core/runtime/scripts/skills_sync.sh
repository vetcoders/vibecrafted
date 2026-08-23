#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF_USAGE'
Usage: skills_sync.sh <host> [--source <repo-root>] [--tool <codex|claude|agy>]... [--dry-run] [--mirror] [--with-shell] [--no-zshrc] [--no-bashrc] [--no-verify]

Sync canonical skill directories from this repo to the staged tools store:
  $HOME/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/skills

Then create symlink views inside the remote tool homes:
  $HOME/.codex/skills
  $HOME/.claude/skills
  $HOME/.agy/skills

Examples:
  bash runtime/scripts/skills_sync.sh host-b
  bash runtime/scripts/skills_sync.sh host-b --tool codex --tool claude
  bash runtime/scripts/skills_sync.sh host-b --dry-run
  bash runtime/scripts/skills_sync.sh host-b --mirror
  bash runtime/scripts/skills_sync.sh host-b --with-shell
EOF_USAGE
}

die() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

remote_foundation_check() {
  local host="$1"
  printf 'Foundation preflight on %s\n' "$host"
  ssh -n "$host" '
if command -v aicx >/dev/null 2>&1; then
  printf "  [ok] aicx -> %s\\n" "$(command -v aicx)"
else
  printf "  [missing] aicx\\n"
  printf "    fix: use canonical foundations installer from https://loct.io/install.sh\\n"
fi
if command -v loctree-mcp >/dev/null 2>&1; then
  printf "  [ok] loctree-mcp -> %s\\n" "$(command -v loctree-mcp)"
else
  printf "  [missing] loctree-mcp\\n"
  printf "    fix: use canonical foundations installer from https://loct.io/install.sh\\n"
fi
if command -v prview >/dev/null 2>&1; then
  printf "  [ok] prview -> %s\\n" "$(command -v prview)"
else
  printf "  [optional] prview not found\\n"
  printf "    fix: prefer GitHub Releases, fallback cargo install prview\\n"
fi
printf "\\n"
'
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
verify=1
dry_run=0
mirror=0
with_shell=0
shell_no_zshrc=0
shell_no_bashrc=0
host=""
declare -a tools=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --source"
      repo_root="$1"
      ;;
    --tool)
      shift
      [[ $# -gt 0 ]] || die "Missing value for --tool"
      case "$1" in
        codex|claude|agy) tools+=("$1") ;;
        *) die "Unknown tool: $1" ;;
      esac
      ;;
    --dry-run|-n)
      dry_run=1
      ;;
    --mirror)
      mirror=1
      ;;
    --with-shell)
      with_shell=1
      ;;
    --no-zshrc)
      shell_no_zshrc=1
      ;;
    --no-bashrc)
      shell_no_bashrc=1
      ;;
    --no-verify)
      verify=0
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    -*)
      die "Unknown option: $1"
      ;;
    *)
      [[ -z "$host" ]] || die "Host already set to $host"
      host="$1"
      ;;
  esac
  shift
done

[[ -n "$host" ]] || {
  usage
  exit 1
}
[[ -d "$repo_root" ]] || die "Repo root not found: $repo_root"

# shellcheck disable=SC2016
source_line='[[ -r "${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh" ]] && source "${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh"'

skills=()
skills_root="$repo_root/vibecrafted-core/vibecrafted_core/skills"
[[ -d "$skills_root" ]] || die "Canonical skills root not found: $skills_root"
while IFS= read -r skill; do
  [[ -n "$skill" ]] || continue
  skills+=("$skill")
done < <(
  find "$skills_root" -mindepth 1 -maxdepth 1 -type d \
    ! -name '.git' \
    ! -name '.loctree' \
    ! -name '_template' \
    ! -name 'docs' \
    ! -name '.github' \
    -exec test -f '{}/SKILL.md' ';' -print | sort
)

[[ ${#skills[@]} -gt 0 ]] || die "No skill directories found under $skills_root"

if [[ ${#tools[@]} -eq 0 ]]; then
  tools=(codex claude agy)
fi

rsync_args=(-az --exclude '.DS_Store' --exclude '.backup' --exclude '.loctree' -e ssh)
if (( mirror )); then
  rsync_args+=(--delete)
fi

printf 'Syncing skills from %s to %s\n' "$repo_root" "$host"
# shellcheck disable=SC2088,SC2016
remote_tools_target='$HOME/.local/share/vibecrafted/tools/vibecrafted-local'
# shellcheck disable=SC2088,SC2016
remote_current_link='$HOME/.local/share/vibecrafted/tools/vibecrafted-current'
# shellcheck disable=SC2088,SC2016
remote_package_target='$HOME/.local/share/vibecrafted/tools/vibecrafted-local/vibecrafted-core/vibecrafted_core/skills'
# shellcheck disable=SC2088,SC2016
remote_shared_target='$HOME/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/skills'
printf -- '-- canonical staged tools -> %s:%s\n' "$host" "$remote_shared_target"
if (( dry_run )); then
  printf '  ssh %s mkdir -p %s && ln -sfn %s %s\n' "$host" "$remote_package_target" "$remote_tools_target" "$remote_current_link"
else
  ssh -n "$host" "mkdir -p $remote_package_target && ln -sfn $remote_tools_target $remote_current_link" \
    || die "Could not prepare staged tools store on $host"
fi

rule_files=()
localized_rule_dirs=(pl)
for rule_name in VERIFICATION_RULE.md LIVING_TREE_RULE.md; do
  [[ -f "$skills_root/$rule_name" ]] && rule_files+=("$rule_name")
done
for localized_dir in "${localized_rule_dirs[@]}"; do
  for rule_name in VERIFICATION_RULE.md LIVING_TREE_RULE.md; do
    rule_path="$localized_dir/$rule_name"
    [[ -f "$skills_root/$rule_path" ]] && rule_files+=("$rule_path")
  done
done
for rule_path in "${rule_files[@]}"; do
  remote_rule_dir="$(dirname "$remote_package_target/$rule_path")"
  if (( dry_run )); then
    printf '  ssh %s mkdir -p %s\n' "$host" "$remote_rule_dir"
  else
    ssh -n "$host" "mkdir -p $remote_rule_dir" \
      || die "Could not create $remote_rule_dir on $host"
  fi
  if (( dry_run )); then
    printf '  rsync %s %s %s:%s\n' "${rsync_args[*]}" "$skills_root/$rule_path" "$host" "$remote_package_target/$rule_path"
  else
    rsync "${rsync_args[@]}" "$skills_root/$rule_path" "$host:$remote_package_target/$rule_path"
  fi
done
for skill in "${skills[@]}"; do
  name="$(basename "$skill")"
  if (( ! dry_run )); then
    ssh -n "$host" "mkdir -p $remote_package_target/$name" || die "Could not create $remote_package_target/$name on $host"
  else
    printf '  ssh %s mkdir -p %s/%s\n' "$host" "$remote_package_target" "$name"
  fi
  if (( dry_run )); then
    printf '  rsync %s %s %s:%s\n' "${rsync_args[*]}" "$skill/" "$host" "$remote_package_target/$name/"
  else
    rsync "${rsync_args[@]}" "$skill/" "$host:$remote_package_target/$name/"
  fi
done
printf '\n'

for tool in "${tools[@]}"; do
  remote_target="\$HOME/.${tool}/skills"
  printf -- '-- %s symlink view -> %s:%s\n' "$tool" "$host" "$remote_target"
  if (( dry_run )); then
    printf '  ssh %s mkdir -p %s\n' "$host" "$remote_target"
  else
    ssh -n "$host" "mkdir -p $remote_target" || die "Could not prepare $remote_target on $host"
  fi
  for rule_path in "${rule_files[@]}"; do
    remote_rule_dir="$(dirname "$remote_target/$rule_path")"
    if (( dry_run )); then
      printf '  ssh %s mkdir -p %s\n' "$host" "$remote_rule_dir"
    else
      ssh -n "$host" "mkdir -p $remote_rule_dir" \
        || die "Could not create $remote_rule_dir on $host"
    fi
    if (( dry_run )); then
      printf '  rsync %s %s %s:%s\n' "${rsync_args[*]}" "$skills_root/$rule_path" "$host" "$remote_target/$rule_path"
    else
      rsync "${rsync_args[@]}" "$skills_root/$rule_path" "$host:$remote_target/$rule_path"
    fi
  done
  for skill in "${skills[@]}"; do
    name="$(basename "$skill")"
    if (( dry_run )); then
      printf '  ssh %s rm -rf %s/%s && ln -s %s/%s %s/%s\n' "$host" "$remote_target" "$name" "$remote_shared_target" "$name" "$remote_target" "$name"
    else
      ssh -n "$host" "rm -rf $remote_target/$name && ln -s $remote_shared_target/$name $remote_target/$name" \
        || die "Could not link $remote_target/$name -> $remote_shared_target/$name on $host"
    fi
  done
  printf '\n'
done

if (( with_shell )); then
  shell_source="$repo_root/vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh"
  [[ -f "$shell_source" ]] || die "Shell helper file not found: $shell_source"

  # shellcheck disable=SC2016
  remote_config_root='${XDG_CONFIG_HOME:-$HOME/.config}'
  remote_helper_dir="$remote_config_root/vetcoders"
  remote_shell_target="$remote_helper_dir/vc-skills.sh"
  remote_legacy_dir="$remote_config_root/zsh"
  remote_legacy_target="$remote_legacy_dir/vc-skills.zsh"

  printf 'Syncing optional shell helper layer to %s\n' "$host"
  if (( dry_run )); then
    printf '  ssh %s mkdir -p %s %s\n' "$host" "$remote_helper_dir" "$remote_legacy_dir"
  else
    ssh -n "$host" "mkdir -p $remote_helper_dir $remote_legacy_dir" || die "Could not prepare helper dirs on $host"
  fi
  if (( dry_run )); then
    printf '  rsync %s %s %s:%s\n' "${rsync_args[*]}" "$shell_source" "$host" "$remote_shell_target"
  else
    rsync "${rsync_args[@]}" "$shell_source" "$host:$remote_shell_target"
  fi
  if (( dry_run )); then
    printf '  ssh %s ln -sfn %s %s\n' "$host" "$remote_shell_target" "$remote_legacy_target"
  else
    ssh -n "$host" "ln -sfn $remote_shell_target $remote_legacy_target" \
      || die "Could not link compat helper path $remote_legacy_target on $host"
  fi

  if (( shell_no_zshrc )); then
    # shellcheck disable=SC2016
    printf 'Skipping remote $HOME/.zshrc update (--no-zshrc).\n'
  else
    remote_zshrc="\$HOME/.zshrc"
    if (( dry_run )); then
      printf '  ssh %s touch %s && if ! grep -Fqx '"'"'%s'"'"' %s; then printf ... >> %s; fi\n' "$host" "$remote_zshrc" "$source_line" "$remote_zshrc" "$remote_zshrc"
    else
      ssh -n "$host" "touch ${remote_zshrc} && if ! grep -Fqx '$source_line' ${remote_zshrc}; then printf '\n# Vetcoders shell helpers\n%s\n' '$source_line' >> ${remote_zshrc}; fi" \
        || die "Could not update $HOME/.zshrc on $host"
    fi
  fi

  if (( shell_no_bashrc )); then
    # shellcheck disable=SC2016
    printf 'Skipping remote $HOME/.bashrc update (--no-bashrc).\n'
  else
    remote_bashrc="\$HOME/.bashrc"
    if (( dry_run )); then
      printf '  ssh %s touch %s && if ! grep -Fqx '"'"'%s'"'"' %s; then printf ... >> %s; fi\n' "$host" "$remote_bashrc" "$source_line" "$remote_bashrc" "$remote_bashrc"
    else
      ssh -n "$host" "touch ${remote_bashrc} && if ! grep -Fqx '$source_line' ${remote_bashrc}; then printf '\n# Vetcoders shell helpers\n%s\n' '$source_line' >> ${remote_bashrc}; fi" \
        || die "Could not update $HOME/.bashrc on $host"
    fi
  fi

  printf '\n'
fi

if (( ! verify )); then
  printf 'Sync complete. Verification skipped.\n'
  exit 0
fi

printf 'Verifying shared skill store on %s\n' "$host"
ssh -n "$host" 'for f in \
  $HOME/.local/share/vibecrafted/tools/vibecrafted-current/scripts/vibecrafted \
  $HOME/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/skills/vc-agents/SKILL.md \
  $HOME/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/shell/vetcoders.sh \
  $HOME/.local/share/vibecrafted/tools/vibecrafted-current/vibecrafted-core/vibecrafted_core/runtime/helpers/vetcoders-runtime-core.sh; do
  if [ -e "$f" ]; then
    echo "OK $f"
  else
    echo "MISSING $f"
  fi
done'

for tool in "${tools[@]}"; do
  printf 'Verifying %s symlink view on %s\n' "$tool" "$host"
  remote_tool_skills="\$HOME/.${tool}/skills/vc-agents"
  ssh -n "$host" "if [ -L ${remote_tool_skills} ]; then echo OK_LINK ${remote_tool_skills}; else echo MISSING_LINK ${remote_tool_skills}; fi"
done

if (( with_shell )); then
  ssh -n "$host" 'if [ -f "${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh" ]; then
  echo "OK ${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh"
else
  echo "MISSING ${XDG_CONFIG_HOME:-$HOME/.config}/vetcoders/vc-skills.sh"
fi'
fi

remote_foundation_check "$host"

printf 'Sync complete.\n'
