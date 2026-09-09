# Loaded only by the product terminal's private ZDOTDIR.
[[ -o interactive ]] || return 0
[[ -z ${_VC_TERMINAL_PROFILE_LOADED:-} ]] || return 0
typeset -g _VC_TERMINAL_PROFILE_LOADED=1

export VIBECRAFTED_TERMINAL_ENTRY=1
export VC_FRAME_CONFIG_DIR="$HOME/.config/vibecrafted/vc-frame"
export STARSHIP_CONFIG="$HOME/.config/vibecrafted/starship.toml"
export ATUIN_CONFIG_DIR="$HOME/.config/vibecrafted/atuin"
export ATUIN_DATA_DIR="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/atuin"
export ATUIN_DB_PATH="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/history.db"
export _ZO_DATA_DIR="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/zoxide"
export STARSHIP_CACHE="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/starship"
HISTFILE="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}/shell/zsh_history"
HISTSIZE=20000
SAVEHIST=20000
mkdir -p "${HISTFILE:h}" "$_ZO_DATA_DIR" "$STARSHIP_CACHE"
setopt appendhistory histignorespace
path=("$HOME/.local/bin" $path)
typeset -U path
bindkey -e
bindkey '^[b' backward-word
bindkey '^[f' forward-word

# One bounded snapshot, containing component names and statuses only. Never
# capture init output: a third-party tool can print private configuration.
typeset -ga _VC_TERMINAL_WARNINGS=()

# Public VC commands remain the installed PATH launchers. No automatic Frame
# attach/create, provider process, or private Python path export belongs here.
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
  compdef _gnu_generic vibecrafted vc-start vc-frame vc-workflow vc-dashboard
else
  _VC_TERMINAL_WARNINGS+=('shell completion unavailable')
fi
for vc_tool in zoxide atuin starship; do
  if (( ! $+commands[$vc_tool] )); then
    _VC_TERMINAL_WARNINGS+=("$vc_tool is not installed; install it with its upstream installer")
    continue
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
for vc_plugin in zsh-autosuggestions zsh-syntax-highlighting; do
  for vc_plugin_prefix in /opt/homebrew /usr/local; do
    vc_plugin_file="$vc_plugin_prefix/share/$vc_plugin/$vc_plugin.zsh"
    if [[ -r "$vc_plugin_file" ]]; then
      source "$vc_plugin_file"
      break
    fi
  done
  [[ -r "$vc_plugin_file" ]] || _VC_TERMINAL_WARNINGS+=("$vc_plugin is not installed")
done
unset vc_plugin_prefix vc_plugin vc_plugin_file

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
  (( ! $+commands[atuin] )) || print '  Ctrl+R history'
  (( ! $+commands[zoxide] )) || print '  z <directory> jump'
  print '  Tab completion'
fi
return 0
