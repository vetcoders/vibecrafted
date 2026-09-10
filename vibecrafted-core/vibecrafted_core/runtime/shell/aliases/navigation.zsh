# Product navigation helpers. Sourced by the vc-terminal profile only.
alias ll='ls -alF'
alias la='ls -A'
alias l='ls -CF'
alias ..='cd ..'
alias ...='cd ../..'
alias ....='cd ../../..'

cdr() {
  local root
  root="$(command git rev-parse --show-toplevel 2>/dev/null)" || return 1
  cd "$root"
}
