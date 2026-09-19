# Product navigation helpers. Sourced by the vc-terminal profile only.
# `aliases` lists this file as the "navigation" group: `## ` lines are its
# sections, an alias shows its value, a public function its trailing comment.

## Listing
alias ll='ls -alF'
alias la='ls -A'
alias l='ls -CF'

## Moving up
alias ..='cd ..'
alias ...='cd ../..'
alias ....='cd ../../..'

## Repository
cdr() { # cd to the root of the current Git repository
  local root
  root="$(command git rev-parse --show-toplevel 2>/dev/null)" || return 1
  cd "$root"
}
