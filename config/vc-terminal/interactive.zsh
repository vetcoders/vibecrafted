# Loaded only by the product terminal's private ZDOTDIR.
[[ -o interactive ]] || return 0

# Remember the file that defined this profile so reload can re-read it after
# an on-disk update. ${(%):-%x} is this sourced path; keep a fallback.
typeset -g _VC_TERMINAL_PROFILE_FILE="${${(%):-%x}:-$HOME/.config/vibecrafted/vc-terminal/interactive.zsh}"

_vc_terminal_owned_alias_names=(ll la l .. ... .... gs ga gc gp gl gd)
_vc_terminal_product_shell="$HOME/.config/vibecrafted/shell"

_vc_terminal_pin_product_env() {
  # Product tool paths must exist before zoxide/atuin/starship `init`.
  # Those commands read STARSHIP_CONFIG / ATUIN_* / _ZO_DATA_DIR at init time.
  export VIBECRAFTED_TERMINAL_ENTRY=1
  export VC_FRAME_CONFIG_DIR="$HOME/.config/vibecrafted/vc-frame"
  export STARSHIP_CONFIG="$HOME/.config/vibecrafted/starship.toml"
  export ATUIN_CONFIG_DIR="$HOME/.config/vibecrafted/atuin"
  export ATUIN_DATA_DIR="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/atuin"
  export ATUIN_DB_PATH="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/history.db"
  export _ZO_DATA_DIR="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/zoxide"
  export STARSHIP_CACHE="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/starship"
  mkdir -p \
    "${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/atuin" \
    "$_ZO_DATA_DIR" \
    "$STARSHIP_CACHE"
}

_vc_terminal_apply_fallback_prompt() {
  # Two-line offline prompt: path, then a simple ❯. Used only when Starship
  # did not install a precmd hook. Do not reset a live Starship prompt.
  if (( $+functions[starship_precmd] || $+functions[prompt_starship_precmd] )); then
    return 0
  fi
  PROMPT=$'%~
❯ '
  RPROMPT=''
}

_vc_terminal_python_door="$HOME/.config/vibecrafted/vc-terminal/bin"

_vc_terminal_write_python_door() {
  # Config-home wrappers only. Not generation bin. Not ~/.local/bin/python3.
  local dir="$_vc_terminal_python_door"
  mkdir -p "$dir"
  <<'EOF' >"$dir/python3"
#!/bin/sh
bin="${VIBECRAFTED_PYTHON:-}"
if [ -z "$bin" ] || [ "${bin#/}" = "$bin" ] || [ ! -x "$bin" ] || [ -d "$bin" ]; then
  printf '%s\n' 'Vibecrafted: python3 needs VIBECRAFTED_PYTHON as an absolute generation interpreter (>=3.11). Host python3 (macOS 3.9.6) is not a product interpreter.' >&2
  exit 127
fi
if ! "$bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
  printf '%s\n' 'Vibecrafted: VIBECRAFTED_PYTHON is not Python >=3.11. Host python3 (macOS 3.9.6) is not a product interpreter.' >&2
  exit 127
fi
exec "$bin" "$@"
EOF
  <<'EOF' >"$dir/python"
#!/bin/sh
bin="${VIBECRAFTED_PYTHON:-}"
if [ -z "$bin" ] || [ "${bin#/}" = "$bin" ] || [ ! -x "$bin" ] || [ -d "$bin" ]; then
  printf '%s\n' 'Vibecrafted: python3 needs VIBECRAFTED_PYTHON as an absolute generation interpreter (>=3.11). Host python3 (macOS 3.9.6) is not a product interpreter.' >&2
  exit 127
fi
if ! "$bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
  printf '%s\n' 'Vibecrafted: VIBECRAFTED_PYTHON is not Python >=3.11. Host python3 (macOS 3.9.6) is not a product interpreter.' >&2
  exit 127
fi
exec "$bin" "$@"
EOF
  chmod 755 "$dir/python3" "$dir/python"
}

_vc_terminal_bind_owned_python() {
  # Typed python / python3 exec VIBECRAFTED_PYTHON (generation CPython >=3.11).
  # env/command/shebang use ZDOTDIR/bin wrappers on PATH. Do not prepend
  # generation bin. Do not write python3 into ~/.local/bin.

  unalias python python3 2>/dev/null || true
  python3() {
    local bin="${VIBECRAFTED_PYTHON:-}"
    if [[ -z "$bin" || "$bin" != /* || ! -x "$bin" || -d "$bin" ]]; then
      print -u2 -r -- 'Vibecrafted: typed python3 needs VIBECRAFTED_PYTHON as an absolute generation interpreter (>=3.11). Host python3 (macOS 3.9.6) is not a product interpreter.'
      return 127
    fi
    if ! "$bin" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      print -u2 -r -- 'Vibecrafted: VIBECRAFTED_PYTHON is not Python >=3.11. Host python3 (macOS 3.9.6) is not a product interpreter.'
      return 127
    fi
    "$bin" "$@"
  }
  python() {
    python3 "$@"
  }
}

_vc_terminal_load_owned_layer() {
  local vc_alias_dir vc_alias_file vc_alias_name
  _vc_terminal_pin_product_env
  for vc_alias_name in "${_vc_terminal_owned_alias_names[@]}"; do
    unalias "$vc_alias_name" 2>/dev/null || true
  done
  # Installed product tree first; generation checkout is a source-only fallback.
  for vc_alias_dir in \
    "$_vc_terminal_product_shell/aliases" \
    "${VIBECRAFTED_ROOT:-}/vibecrafted-core/vibecrafted_core/runtime/shell/aliases"
  do
    [[ -d "$vc_alias_dir" ]] || continue
    for vc_alias_file in "$vc_alias_dir"/*.zsh(N); do
      source "$vc_alias_file"
    done
    break
  done
  unset vc_alias_dir vc_alias_file vc_alias_name
  aliases() {
    print -r -- 'navigation'
    print -r -- '  ll  la  l  ..  ...  ....  cdr'
    print -r -- 'git'
    print -r -- '  gs  ga  gc  gp  gl  gd'
    print -r -- 'frame'
    print -r -- '  vcf-lp  vcf-ls  vcf-da'
    print -r -- 'python'
    print -r -- '  python  python3  generation CPython (not host 3.9.6)'
  }
  _vc_terminal_write_python_door
  _vc_terminal_bind_owned_python
  _vc_terminal_apply_fallback_prompt
}

reload() {
  # Re-read the installed profile so a changed interactive.zsh becomes the
  # live definition. The load-once guard below skips tool/ZLE/banner work.
  # Then apply the (possibly new) owned layer. cwd and HISTFILE stay put.
  local vc_profile="${_VC_TERMINAL_PROFILE_FILE:-$HOME/.config/vibecrafted/vc-terminal/interactive.zsh}"
  if [[ -r "$vc_profile" ]]; then
    source "$vc_profile"
  fi
  _vc_terminal_load_owned_layer
}

[[ -z ${_VC_TERMINAL_PROFILE_LOADED:-} ]] || return 0
typeset -g _VC_TERMINAL_PROFILE_LOADED=1

HISTFILE="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/zsh_history"
HISTSIZE=20000
SAVEHIST=20000
mkdir -p "${HISTFILE:h}"
_vc_terminal_pin_product_env
setopt appendhistory histignorespace
_vc_terminal_write_python_door
path=("$_vc_terminal_python_door" "$HOME/.local/bin" $path)
typeset -U path
bindkey -e
bindkey '^[b' backward-word
bindkey '^[f' forward-word

# One bounded snapshot, containing component names and statuses only. Never
# capture init output: a third-party tool can print private configuration.
typeset -ga _VC_TERMINAL_WARNINGS=()

# Public VC commands remain the installed PATH launchers. No automatic Frame
# attach/create, provider process, or private generation-bin export belongs here.
# Door python names are ZDOTDIR/bin wrappers plus functions over VIBECRAFTED_PYTHON.
# Keep broken external completion installations out of this shell's scan.
# Do not repair or unlink files owned by another product. compinit still audits
# the remaining directories; -i excludes insecure entries instead of prompting.
typeset -a vc_completion_path
for vc_completion_dir in $fpath; do
  vc_completion_ok=1
  for vc_completion_file in "$vc_completion_dir"/_*(N); do
    if [[ ! -r "$vc_completion_file" ]]; then
      vc_completion_ok=0
      break
    fi
  done
  if (( vc_completion_ok )); then
    vc_completion_path+=("$vc_completion_dir")
  else
    _VC_TERMINAL_WARNINGS+=('skipped a completion directory containing unreadable entries')
  fi
done
fpath=("${vc_completion_path[@]}")
unset vc_completion_path vc_completion_dir vc_completion_file vc_completion_ok
autoload -Uz compinit
compinit -i -d "${HISTFILE:h}/zcompdump"
if (( $+functions[compdef] )); then
  # Ask each installed command for its advertised options on completion, not
  # on shell startup. This follows upgrades without executing a workspace.
  compdef _gnu_generic vibecrafted vc-frame vc-workflow vc-dashboard
  # vc-start's human-readable help embeds options in usage/examples rather
  # than a GNU option table, so _gnu_generic cannot discover its flags.
  _vc_terminal_start_completion() {
    _arguments \
      '--repo[Repository directory]:repository:_files -/' \
      '--root[Legacy spelling of --repo]:repository:_files -/' \
      '--help[Show workspace help]' \
      '1:workspace:(resume)'
  }
  compdef _vc_terminal_start_completion vc-start
else
  _VC_TERMINAL_WARNINGS+=('shell completion unavailable')
fi
# STARSHIP_CONFIG / ATUIN_* / _ZO_DATA_DIR were pinned above. Init reads them.
# Starship without the product toml, or with a leftover `$python` format,
# probes host python3 (macOS 3.9.6) and paints it as product chrome. Skip
# init; the offline two-line prompt stays. Do not change PATH here.
for vc_tool in zoxide atuin starship; do
  if (( ! $+commands[$vc_tool] )); then
    _VC_TERMINAL_WARNINGS+=("$vc_tool is not installed; install it with its upstream installer")
    continue
  fi
  if [[ $vc_tool == starship ]]; then
    if [[ ! -f ${STARSHIP_CONFIG:-} || -L ${STARSHIP_CONFIG:-} ]]; then
      _VC_TERMINAL_WARNINGS+=('product starship.toml is missing; using the offline prompt instead of host python chrome')
      continue
    fi
    if grep -q '$python' "$STARSHIP_CONFIG" 2>/dev/null; then
      _VC_TERMINAL_WARNINGS+=('product starship.toml still names $python; using the offline prompt instead of host python 3.9')
      continue
    fi
  fi
  vc_tool_args=(init zsh)
  [[ $vc_tool != atuin ]] || vc_tool_args+=(--disable-up-arrow)
  if vc_tool_init=$(command "$vc_tool" "${vc_tool_args[@]}" 2>/dev/null); then
    eval "$vc_tool_init" || _VC_TERMINAL_WARNINGS+=("$vc_tool initialization failed")
  else
    _VC_TERMINAL_WARNINGS+=("$vc_tool initialization failed")
  fi
done
unset vc_tool vc_tool_args vc_tool_init
if [[ -n ${VC_TERMINAL_PLUGIN_PREFIXES:-} ]]; then
  vc_plugin_prefixes=(${=VC_TERMINAL_PLUGIN_PREFIXES})
else
  vc_plugin_prefixes=(/opt/homebrew /usr/local "$_vc_terminal_product_shell/plugins")
fi
for vc_plugin in zsh-autosuggestions zsh-syntax-highlighting; do
  vc_plugin_file=""
  for vc_plugin_prefix in "${vc_plugin_prefixes[@]}"; do
    vc_plugin_file="$vc_plugin_prefix/share/$vc_plugin/$vc_plugin.zsh"
    if [[ "$vc_plugin_prefix" == */plugins ]]; then
      vc_plugin_file="$vc_plugin_prefix/$vc_plugin/$vc_plugin.zsh"
    fi
    if [[ -r "$vc_plugin_file" ]]; then
      source "$vc_plugin_file"
      break
    fi
  done
  [[ -r "$vc_plugin_file" ]] || _VC_TERMINAL_WARNINGS+=("$vc_plugin is not installed")
done
unset vc_plugin_prefix vc_plugin vc_plugin_file vc_plugin_prefixes

_vc_terminal_load_owned_layer

(
  umask 077
  print -r -- 'Vibecrafted terminal startup' > "${HISTFILE:h}/startup.log"
  for vc_warning in "${_VC_TERMINAL_WARNINGS[@]}"; do
    print -r -- "$vc_warning" >> "${HISTFILE:h}/startup.log"
  done
)
if (( ${#_VC_TERMINAL_WARNINGS} )); then
  print -u2 -r -- "Vibecrafted: ${#_VC_TERMINAL_WARNINGS} shell setup notices. See ${HISTFILE:h}/startup.log"
fi

if [[ -t 1 ]]; then
  print -P '%F{cyan}Vibecrafted%f · Your terminal is ready.'
  print '  vc-start --repo <path>    Create a workspace for your project'
  print '  vc-frame list-sessions   Find an existing workspace'
  print '  vc-frame attach <name>   Return to a workspace'
  print '  vibecrafted --help      Explore commands'
  print '  aliases                 List product shortcuts'
  print '  reload                  Re-read the installed product profile'
  (( ! $+commands[atuin] )) || print '  Ctrl+R history'
  (( ! $+commands[zoxide] )) || print '  z <directory> jump'
  print '  Tab completion'
fi
return 0
