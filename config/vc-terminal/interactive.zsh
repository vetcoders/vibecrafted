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
  # Two-line offline prompt in the shape of the product starship.toml: bold
  # blue path, then ❯ (green after success, red after failure). Used only when
  # Starship did not install a precmd hook. Do not reset a live Starship prompt.
  if (( $+functions[starship_precmd] || $+functions[prompt_starship_precmd] )); then
    return 0
  fi
  PROMPT=$'%B%F{blue}%~%f%b\n%(?.%F{green}.%F{red})❯%f '
  RPROMPT=''
}

_vc_terminal_python_door="$HOME/.config/vibecrafted/vc-terminal/bin"

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
  local vc_alias_dir vc_alias_file vc_name
  local -a vc_before_aliases vc_before_functions vc_after
  _vc_terminal_pin_product_env
  # Forget what the previous load defined, so a shortcut removed from disk is
  # gone after reload instead of lingering under its old definition.
  for vc_name in "${_vc_terminal_owned_alias_names[@]}" "${_vc_terminal_loaded_aliases[@]}"; do
    unalias -- "$vc_name" 2>/dev/null || true
  done
  for vc_name in "${_vc_terminal_loaded_functions[@]}"; do
    unfunction -- "$vc_name" 2>/dev/null || true
  done
  vc_before_aliases=(${(k)aliases})
  vc_before_functions=(${(k)functions})
  typeset -g _vc_terminal_alias_dir=""
  # Installed product tree first; generation checkout is a source-only fallback.
  for vc_alias_dir in \
    "$_vc_terminal_product_shell/aliases" \
    "${VIBECRAFTED_ROOT:-}/vibecrafted-core/vibecrafted_core/runtime/shell/aliases"
  do
    [[ -d "$vc_alias_dir" ]] || continue
    _vc_terminal_alias_dir="$vc_alias_dir"
    for vc_alias_file in "$vc_alias_dir"/*.zsh(N); do
      source "$vc_alias_file"
    done
    break
  done
  # Unquoted on purpose: a quoted ${a:|b} compares one joined string instead
  # of removing elements.
  vc_after=(${(k)aliases})
  typeset -ga _vc_terminal_loaded_aliases=(${vc_after:|vc_before_aliases})
  vc_after=(${(k)functions})
  typeset -ga _vc_terminal_loaded_functions=(${vc_after:|vc_before_functions})
  _vc_terminal_bind_owned_python
  _vc_terminal_apply_fallback_prompt
}

_vc_terminal_catalog_emit() {
  # One catalogue row: $1 group, $2 section, $3 name, $4 value or description.
  # Group and section headers print once, and only above a row that matches.
  emulate -L zsh
  local vc_haystack="$1 $2 $3 $4"
  if [[ -n $_vc_terminal_catalog_filter && ${(L)vc_haystack} != *"$_vc_terminal_catalog_filter"* ]]; then
    return 0
  fi
  if [[ $1 != "$_vc_terminal_catalog_group" ]]; then
    _vc_terminal_catalog_group="$1"
    _vc_terminal_catalog_section=""
    print -r -- "${_vc_terminal_catalog_head}$1${_vc_terminal_catalog_reset}"
  fi
  if [[ -n $2 && $2 != "$_vc_terminal_catalog_section" ]]; then
    _vc_terminal_catalog_section="$2"
    print -r -- "  ${_vc_terminal_catalog_dim}── $2${_vc_terminal_catalog_reset}"
  fi
  printf '    %s%-8s%s %s\n' "$_vc_terminal_catalog_name" "$3" "$_vc_terminal_catalog_reset" "$4"
}

aliases() {
  # Grouped catalogue of what this profile loaded: one group per installed
  # alias file (`## ` lines are its sections), then the profile's own commands.
  # `aliases <text>` keeps rows whose group, section, name or value has <text>.
  emulate -L zsh
  local vc_file vc_group vc_section vc_line vc_value
  local vc_shell_group='shell'
  local vc_alias_re='^alias[[:space:]]+([^=[:space:]]+)=(.*)$'
  local vc_function_re='^([A-Za-z][-A-Za-z0-9_.]*)[(][)][[:space:]]*[{][[:space:]]*(#[[:space:]]*(.*))?$'
  local -a vc_files
  typeset -g _vc_terminal_catalog_filter="${(L)*}"
  typeset -g _vc_terminal_catalog_group="" _vc_terminal_catalog_section=""
  typeset -g _vc_terminal_catalog_head="" _vc_terminal_catalog_dim=""
  typeset -g _vc_terminal_catalog_name="" _vc_terminal_catalog_reset=""
  if [[ -t 1 ]]; then
    _vc_terminal_catalog_head=$'\e[1;34m'
    _vc_terminal_catalog_dim=$'\e[2;33m'
    _vc_terminal_catalog_name=$'\e[32m'
    _vc_terminal_catalog_reset=$'\e[0m'
  fi
  [[ -z ${_vc_terminal_alias_dir:-} ]] || vc_files=("$_vc_terminal_alias_dir"/*.zsh(N))
  for vc_file in "${vc_files[@]}"; do
    vc_group="${vc_file:t:r}"
    vc_section=""
    while IFS= read -r vc_line || [[ -n $vc_line ]]; do
      if [[ $vc_line == '## '* ]]; then
        vc_section="${vc_line[4,-1]}"
      elif [[ $vc_line =~ $vc_alias_re ]]; then
        vc_value="${match[2]}"
        if [[ $vc_value == \'*\' || $vc_value == \"*\" ]]; then
          vc_value="${vc_value[2,-2]}"
        fi
        _vc_terminal_catalog_emit "$vc_group" "$vc_section" "${match[1]}" "$vc_value"
      elif [[ $vc_line =~ $vc_function_re ]]; then
        _vc_terminal_catalog_emit "$vc_group" "$vc_section" "${match[1]}" "${match[3]}"
      fi
    done < "$vc_file"
  done
  _vc_terminal_catalog_emit "$vc_shell_group" 'Profile' reload 're-read the installed product profile'
  _vc_terminal_catalog_emit "$vc_shell_group" 'Profile' aliases '[text] list these shortcuts, optionally filtered'
  if (( $+functions[atuin-search] )); then
    _vc_terminal_catalog_emit "$vc_shell_group" 'History' 'Ctrl+R' 'search history with Atuin'
  else
    _vc_terminal_catalog_emit "$vc_shell_group" 'History' 'Ctrl+R' 'search history backwards'
  fi
  if (( $+functions[z] || $+aliases[z] )); then
    _vc_terminal_catalog_emit "$vc_shell_group" 'History' 'z' '<directory> jump with zoxide'
  fi
  _vc_terminal_catalog_emit "$vc_shell_group" 'Python' python3 'generation CPython, not host python3'
  _vc_terminal_catalog_emit "$vc_shell_group" 'Python' python 'same as python3'
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
HISTSIZE=100000
SAVEHIST=100000
mkdir -p "${HISTFILE:h}"
_vc_terminal_pin_product_env
# Keep the useful host-shell history semantics, but only inside the product
# state root above.  In particular, never borrow ~/.zsh_history or a personal
# ZDOTDIR to obtain this behaviour.
setopt extendedhistory incappendhistory sharehistory histignoredups \
  histignorealldups histverify appendhistory histignorespace
path=("$_vc_terminal_python_door" "$HOME/.local/bin" $path)
typeset -U path
bindkey -e
bindkey '^[b' backward-word
bindkey '^[f' forward-word

# Completion belongs to this profile even when the user has no personal zsh
# configuration.  `menu select` keeps Tab navigation deliberate; the matcher
# makes command and option completion case-insensitive without altering the
# command line itself.
zstyle ':completion:*' matcher-list 'm:{a-z}={A-Z}'
zstyle ':completion:*' menu select
[[ -z ${LS_COLORS:-} ]] || zstyle ':completion:*' list-colors "${(s.:.)LS_COLORS}"

# One bounded snapshot, containing component names and statuses only. Never
# capture init output: a third-party tool can print private configuration.
# Warnings are counted on stderr at startup; notes only go to startup.log,
# because the shell they describe is fully usable.
typeset -ga _VC_TERMINAL_WARNINGS=()
typeset -ga _VC_TERMINAL_NOTES=()

# Public VC commands remain the installed PATH launchers. No automatic Frame
# attach/create, provider process, or private generation-bin export belongs here.
# Door python names are ZDOTDIR/bin wrappers plus functions over VIBECRAFTED_PYTHON.
# A completion directory holding an unreadable entry (for example a Homebrew
# link into an app that is gone) would make compinit fail on that one file.
# Such a directory is replaced, at its fpath position, by a product-state
# mirror of its readable entries, so the rest of it still completes. Never
# repair or unlink files owned by another product. The mirror is rebuilt only
# when the directory or its set of unreadable entries changes. compinit still
# audits the remaining directories; -i excludes insecure entries instead of
# prompting.
typeset -a vc_completion_path vc_completion_readable vc_completion_unreadable
vc_completion_mirror_root="${HISTFILE:h}/completion-mirror"
for vc_completion_dir in $fpath; do
  vc_completion_readable=()
  vc_completion_unreadable=()
  for vc_completion_file in "$vc_completion_dir"/_*(N); do
    if [[ -r "$vc_completion_file" ]]; then
      vc_completion_readable+=("$vc_completion_file")
    else
      vc_completion_unreadable+=("${vc_completion_file:t}")
    fi
  done
  if (( ! ${#vc_completion_unreadable} )); then
    vc_completion_path+=("$vc_completion_dir")
    continue
  fi
  vc_completion_mirror="$vc_completion_mirror_root/${vc_completion_dir//\//%}"
  vc_completion_stamp="${(j: :)vc_completion_unreadable}"
  vc_completion_seen=""
  [[ ! -r "$vc_completion_mirror/.unreadable" ]] || vc_completion_seen="$(<"$vc_completion_mirror/.unreadable")"
  if [[ ! -d "$vc_completion_mirror" || "$vc_completion_dir" -nt "$vc_completion_mirror" \
    || "$vc_completion_seen" != "$vc_completion_stamp" ]]; then
    rm -rf -- "$vc_completion_mirror"
    mkdir -p -- "$vc_completion_mirror"
    (( ! ${#vc_completion_readable} )) \
      || ln -s -- "${vc_completion_readable[@]}" "$vc_completion_mirror/"
    print -r -- "$vc_completion_stamp" > "$vc_completion_mirror/.unreadable"
  fi
  vc_completion_path+=("$vc_completion_mirror")
  _VC_TERMINAL_NOTES+=("skipped unreadable completion entries: $vc_completion_stamp")
done
fpath=("${vc_completion_path[@]}")
unset vc_completion_path vc_completion_dir vc_completion_file vc_completion_readable \
  vc_completion_unreadable vc_completion_mirror_root vc_completion_mirror \
  vc_completion_stamp vc_completion_seen
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

# Atuin owns the interactive search widgets it exports from `init zsh`; keep
# the Up binding explicit because init ran with its stock Up binding disabled.
# A multiline buffer is editor text, not a search query: native Up then moves
# within it.  If Atuin is absent (or its init failed), all three keys retain
# useful built-in history behaviour.
if (( $+functions[_atuin_search] && $+functions[atuin-search] )); then
  _vc_terminal_atuin_up_or_history() {
    emulate -L zsh
    if [[ $BUFFER == *$'\n'* ]]; then
      zle up-line-or-history
    else
      _atuin_search --shell-up-key-binding "$@"
    fi
  }
  zle -N _vc_terminal_atuin_up_or_history
  bindkey '^[[A' _vc_terminal_atuin_up_or_history
  bindkey '^[OA' _vc_terminal_atuin_up_or_history
  bindkey '^[[B' down-line-or-history
  bindkey '^[OB' down-line-or-history
  bindkey '^R' atuin-search
  (( ! $+functions[atuin-search-viins] )) || bindkey -M viins '^R' atuin-search-viins
  (( ! $+functions[atuin-search-vicmd] )) || bindkey -M vicmd '/' atuin-search-vicmd
else
  # These built-in history widgets are autoloaded rather than registered in a
  # minimal `zsh -f` environment.  Register them before binding so the product
  # shell retains real Up/Down history recall when Atuin is unavailable.
  autoload -Uz up-line-or-beginning-search down-line-or-beginning-search
  zle -N up-line-or-beginning-search
  zle -N down-line-or-beginning-search
  bindkey '^[[A' up-line-or-beginning-search
  bindkey '^[OA' up-line-or-beginning-search
  bindkey '^[[B' down-line-or-beginning-search
  bindkey '^[OB' down-line-or-beginning-search
  bindkey '^R' history-incremental-search-backward
fi

# These plugins are already supplied by the product/host package paths.  Set
# preferences before sourcing so their initialisation sees the intended order.
# /usr covers the Debian/Ubuntu apt packages (/usr/share/<plugin>/<plugin>.zsh).
ZSH_AUTOSUGGEST_HIGHLIGHT_STYLE='fg=244'
ZSH_AUTOSUGGEST_STRATEGY=(history completion)
if [[ -n ${VC_TERMINAL_PLUGIN_PREFIXES:-} ]]; then
  vc_plugin_prefixes=(${=VC_TERMINAL_PLUGIN_PREFIXES})
else
  vc_plugin_prefixes=(/opt/homebrew /usr/local /usr "$_vc_terminal_product_shell/plugins")
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
  for vc_warning in "${_VC_TERMINAL_WARNINGS[@]}" "${_VC_TERMINAL_NOTES[@]}"; do
    print -r -- "$vc_warning" >> "${HISTFILE:h}/startup.log"
  done
)
if (( ${#_VC_TERMINAL_WARNINGS} )); then
  print -u2 -r -- "Vibecrafted: ${#_VC_TERMINAL_WARNINGS} shell setup notices. See ${HISTFILE:h}/startup.log"
fi

# The command deck is the door of a plain VC Terminal. A Frame pane already has
# its own chrome (Start here, the tab row, the Shell tab guide), so repeating
# the deck in every new pane is noise.
if [[ -t 1 && -z "${VIBECRAFTED_QUIET_START:-}" && -z "${VC_FRAME_PANE_ID:-}" ]]; then
  print -P '%F{cyan}Vibecrafted%f · Your terminal is ready.'
  print '  vc-start --repo <path>    Create a workspace for your project'
  print '  vc-frame list-sessions   Find an existing workspace'
  print '  vc-frame attach <name>   Return to a workspace'
  print '  vibecrafted --help      Explore commands'
  print '  aliases [text]          List product shortcuts (vcf-* for vc-frame)'
  print '  reload                  Re-read the installed product profile'
  (( ! $+commands[atuin] )) || print '  Ctrl+R history'
  (( ! $+commands[zoxide] )) || print '  z <directory> jump'
  print '  Tab completion'
fi
return 0
