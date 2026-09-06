# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

codex-decorate() { _vetcoders_skill codex decorate "$@"; }
claude-decorate() { _vetcoders_skill claude decorate "$@"; }
agy-decorate() { _vetcoders_skill agy decorate "$@"; }
junie-decorate() { _vetcoders_skill junie decorate "$@"; }
grok-decorate() { _vetcoders_skill grok decorate "$@"; }
cursor-decorate() { _vetcoders_skill cursor decorate "$@"; }

codex-followup() { _vetcoders_skill codex followup "$@"; }
claude-followup() { _vetcoders_skill claude followup "$@"; }
agy-followup() { _vetcoders_skill agy followup "$@"; }
junie-followup() { _vetcoders_skill junie followup "$@"; }
grok-followup() { _vetcoders_skill grok followup "$@"; }
cursor-followup() { _vetcoders_skill cursor followup "$@"; }

codex-prune() { _vetcoders_skill codex prune "$@"; }
claude-prune() { _vetcoders_skill claude prune "$@"; }
agy-prune() { _vetcoders_skill agy prune "$@"; }
junie-prune() { _vetcoders_skill junie prune "$@"; }
grok-prune() { _vetcoders_skill grok prune "$@"; }
cursor-prune() { _vetcoders_skill cursor prune "$@"; }

codex-scaffold() { _vetcoders_skill codex scaffold "$@"; }
claude-scaffold() { _vetcoders_skill claude scaffold "$@"; }
agy-scaffold() { _vetcoders_skill agy scaffold "$@"; }
junie-scaffold() { _vetcoders_skill junie scaffold "$@"; }
grok-scaffold() { _vetcoders_skill grok scaffold "$@"; }
cursor-scaffold() { _vetcoders_skill cursor scaffold "$@"; }

codex-release() { _vetcoders_skill codex release "$@"; }
claude-release() { _vetcoders_skill claude release "$@"; }
agy-release() { _vetcoders_skill agy release "$@"; }
junie-release() { _vetcoders_skill junie release "$@"; }
grok-release() { _vetcoders_skill grok release "$@"; }
cursor-release() { _vetcoders_skill cursor release "$@"; }

codex-justdo() { _vetcoders_skill codex justdo "$@"; }
claude-justdo() { _vetcoders_skill claude justdo "$@"; }
agy-justdo() { _vetcoders_skill agy justdo "$@"; }
junie-justdo() { _vetcoders_skill junie justdo "$@"; }
grok-justdo() { _vetcoders_skill grok justdo "$@"; }
cursor-justdo() { _vetcoders_skill cursor justdo "$@"; }

codex-partner() { _vetcoders_skill_partner codex "$@"; }
claude-partner() { _vetcoders_skill_partner claude "$@"; }
agy-partner() { _vetcoders_skill_partner agy "$@"; }
junie-partner() { _vetcoders_skill_partner junie "$@"; }
grok-partner() { _vetcoders_skill_partner grok "$@"; }
cursor-partner() { _vetcoders_skill_partner cursor "$@"; }

# Per-agent skill-* helpers. The deck (`vibecrafted <skill> <agent>` and
# `vibecrafted init <agent>`) resolves `${agent}-skill-${skill}` by name.
# All six fleet agents must be present — missing one is a hard fail at
# the launcher. cmd_init / cmd_skill also know the generic
# _vetcoders_skill_* entrypoints as a defense-in-depth fallback.
codex-skill-agents() { _vetcoders_skill_entry codex agents "$@"; }
claude-skill-agents() { _vetcoders_skill_entry claude agents "$@"; }
agy-skill-agents() { _vetcoders_skill_entry agy agents "$@"; }
junie-skill-agents() { _vetcoders_skill_entry junie agents "$@"; }
grok-skill-agents() { _vetcoders_skill_entry grok agents "$@"; }
cursor-skill-agents() { _vetcoders_skill_entry cursor agents "$@"; }

codex-skill-audit() { _vetcoders_skill_entry codex audit "$@"; }
claude-skill-audit() { _vetcoders_skill_entry claude audit "$@"; }
agy-skill-audit() { _vetcoders_skill_entry agy audit "$@"; }
junie-skill-audit() { _vetcoders_skill_entry junie audit "$@"; }
grok-skill-audit() { _vetcoders_skill_entry grok audit "$@"; }
cursor-skill-audit() { _vetcoders_skill_entry cursor audit "$@"; }

codex-skill-decorate() { _vetcoders_skill_entry codex decorate "$@"; }
claude-skill-decorate() { _vetcoders_skill_entry claude decorate "$@"; }
agy-skill-decorate() { _vetcoders_skill_entry agy decorate "$@"; }
junie-skill-decorate() { _vetcoders_skill_entry junie decorate "$@"; }
grok-skill-decorate() { _vetcoders_skill_entry grok decorate "$@"; }
cursor-skill-decorate() { _vetcoders_skill_entry cursor decorate "$@"; }

codex-skill-delegate() { _vetcoders_skill_entry codex delegate "$@"; }
claude-skill-delegate() { _vetcoders_skill_entry claude delegate "$@"; }
agy-skill-delegate() { _vetcoders_skill_entry agy delegate "$@"; }
junie-skill-delegate() { _vetcoders_skill_entry junie delegate "$@"; }
grok-skill-delegate() { _vetcoders_skill_entry grok delegate "$@"; }
cursor-skill-delegate() { _vetcoders_skill_entry cursor delegate "$@"; }

codex-skill-dou() { _vetcoders_skill_entry codex dou "$@"; }
claude-skill-dou() { _vetcoders_skill_entry claude dou "$@"; }
agy-skill-dou() { _vetcoders_skill_entry agy dou "$@"; }
junie-skill-dou() { _vetcoders_skill_entry junie dou "$@"; }
grok-skill-dou() { _vetcoders_skill_entry grok dou "$@"; }
cursor-skill-dou() { _vetcoders_skill_entry cursor dou "$@"; }

codex-skill-followup() { _vetcoders_skill_entry codex followup "$@"; }
claude-skill-followup() { _vetcoders_skill_entry claude followup "$@"; }
agy-skill-followup() { _vetcoders_skill_entry agy followup "$@"; }
junie-skill-followup() { _vetcoders_skill_entry junie followup "$@"; }
grok-skill-followup() { _vetcoders_skill_entry grok followup "$@"; }
cursor-skill-followup() { _vetcoders_skill_entry cursor followup "$@"; }

codex-skill-hydrate() { _vetcoders_skill_entry codex hydrate "$@"; }
claude-skill-hydrate() { _vetcoders_skill_entry claude hydrate "$@"; }
agy-skill-hydrate() { _vetcoders_skill_entry agy hydrate "$@"; }
junie-skill-hydrate() { _vetcoders_skill_entry junie hydrate "$@"; }
grok-skill-hydrate() { _vetcoders_skill_entry grok hydrate "$@"; }
cursor-skill-hydrate() { _vetcoders_skill_entry cursor hydrate "$@"; }

codex-skill-init() { _vetcoders_skill_init codex "$@"; }
claude-skill-init() { _vetcoders_skill_init claude "$@"; }
agy-skill-init() { _vetcoders_skill_init agy "$@"; }
junie-skill-init() { _vetcoders_skill_init junie "$@"; }
grok-skill-init() { _vetcoders_skill_init grok "$@"; }
cursor-skill-init() { _vetcoders_skill_init cursor "$@"; }

codex-skill-operator() { _vetcoders_skill_operator codex "$@"; }
claude-skill-operator() { _vetcoders_skill_operator claude "$@"; }
agy-skill-operator() { _vetcoders_skill_operator agy "$@"; }
junie-skill-operator() { _vetcoders_skill_operator junie "$@"; }
grok-skill-operator() { _vetcoders_skill_operator grok "$@"; }
cursor-skill-operator() { _vetcoders_skill_operator cursor "$@"; }

codex-skill-justdo() { _vetcoders_skill_entry codex justdo "$@"; }
claude-skill-justdo() { _vetcoders_skill_entry claude justdo "$@"; }
agy-skill-justdo() { _vetcoders_skill_entry agy justdo "$@"; }
junie-skill-justdo() { _vetcoders_skill_entry junie justdo "$@"; }
grok-skill-justdo() { _vetcoders_skill_entry grok justdo "$@"; }
cursor-skill-justdo() { _vetcoders_skill_entry cursor justdo "$@"; }

# vc-implement is the front-face brand for vc-justdo. Both helper families hit
# the same dispatcher (skill id stays "justdo" so run_id prefix, locks, and
# already-trained agents keep working unchanged).
codex-skill-implement() { _vetcoders_skill_entry codex justdo "$@"; }
claude-skill-implement() { _vetcoders_skill_entry claude justdo "$@"; }
agy-skill-implement() { _vetcoders_skill_entry agy justdo "$@"; }
junie-skill-implement() { _vetcoders_skill_entry junie justdo "$@"; }
grok-skill-implement() { _vetcoders_skill_entry grok justdo "$@"; }
cursor-skill-implement() { _vetcoders_skill_entry cursor justdo "$@"; }

codex-skill-marbles() { _vetcoders_marbles codex "$@"; }
claude-skill-marbles() { _vetcoders_marbles claude "$@"; }
agy-skill-marbles() { _vetcoders_marbles agy "$@"; }
junie-skill-marbles() { _vetcoders_marbles junie "$@"; }
grok-skill-marbles() { _vetcoders_marbles grok "$@"; }
cursor-skill-marbles() { _vetcoders_marbles cursor "$@"; }

codex-skill-partner() { _vetcoders_skill_partner codex "$@"; }
claude-skill-partner() { _vetcoders_skill_partner claude "$@"; }
agy-skill-partner() { _vetcoders_skill_partner agy "$@"; }
junie-skill-partner() { _vetcoders_skill_partner junie "$@"; }
grok-skill-partner() { _vetcoders_skill_partner grok "$@"; }
cursor-skill-partner() { _vetcoders_skill_partner cursor "$@"; }

codex-skill-polarize() { _vetcoders_skill_entry codex polarize "$@"; }
claude-skill-polarize() { _vetcoders_skill_entry claude polarize "$@"; }
agy-skill-polarize() { _vetcoders_skill_entry agy polarize "$@"; }
junie-skill-polarize() { _vetcoders_skill_entry junie polarize "$@"; }
grok-skill-polarize() { _vetcoders_skill_entry grok polarize "$@"; }
cursor-skill-polarize() { _vetcoders_skill_entry cursor polarize "$@"; }

codex-skill-prune() { _vetcoders_skill_entry codex prune "$@"; }
claude-skill-prune() { _vetcoders_skill_entry claude prune "$@"; }
agy-skill-prune() { _vetcoders_skill_entry agy prune "$@"; }
junie-skill-prune() { _vetcoders_skill_entry junie prune "$@"; }
grok-skill-prune() { _vetcoders_skill_entry grok prune "$@"; }
cursor-skill-prune() { _vetcoders_skill_entry cursor prune "$@"; }

codex-skill-release() { _vetcoders_skill_entry codex release "$@"; }
claude-skill-release() { _vetcoders_skill_entry claude release "$@"; }
agy-skill-release() { _vetcoders_skill_entry agy release "$@"; }
junie-skill-release() { _vetcoders_skill_entry junie release "$@"; }
grok-skill-release() { _vetcoders_skill_entry grok release "$@"; }
cursor-skill-release() { _vetcoders_skill_entry cursor release "$@"; }

codex-skill-research() { _vetcoders_skill_entry codex research "$@"; }
claude-skill-research() { _vetcoders_skill_entry claude research "$@"; }
agy-skill-research() { _vetcoders_skill_entry agy research "$@"; }
junie-skill-research() { _vetcoders_skill_entry junie research "$@"; }
grok-skill-research() { _vetcoders_skill_entry grok research "$@"; }
cursor-skill-research() { _vetcoders_skill_entry cursor research "$@"; }
# Public shortcuts are exact deck pass-throughs (see _vetcoders_vc_passthrough).
# Do not route to legacy _vetcoders_research / shell help — that diverged from
# vibecrafted research flags (--json, --model, --prompt-stdin, …).
vc-research() { _vetcoders_vc_passthrough research "$@"; }
# Standalone binary (no vibecrafted <verb> twin) — skip function, hit PATH.
vc-research-await() { command vc-research-await "$@"; }

codex-skill-review() { _vetcoders_skill_entry codex review "$@"; }
claude-skill-review() { _vetcoders_skill_entry claude review "$@"; }
agy-skill-review() { _vetcoders_skill_entry agy review "$@"; }
junie-skill-review() { _vetcoders_skill_entry junie review "$@"; }
grok-skill-review() { _vetcoders_skill_entry grok review "$@"; }
cursor-skill-review() { _vetcoders_skill_entry cursor review "$@"; }

codex-skill-scaffold() { _vetcoders_skill_entry codex scaffold "$@"; }
claude-skill-scaffold() { _vetcoders_skill_entry claude scaffold "$@"; }
agy-skill-scaffold() { _vetcoders_skill_entry agy scaffold "$@"; }
junie-skill-scaffold() { _vetcoders_skill_entry junie scaffold "$@"; }
grok-skill-scaffold() { _vetcoders_skill_entry grok scaffold "$@"; }
cursor-skill-scaffold() { _vetcoders_skill_entry cursor scaffold "$@"; }

codex-skill-workflow() { _vetcoders_skill_entry codex workflow "$@"; }
claude-skill-workflow() { _vetcoders_skill_entry claude workflow "$@"; }
agy-skill-workflow() { _vetcoders_skill_entry agy workflow "$@"; }
junie-skill-workflow() { _vetcoders_skill_entry junie workflow "$@"; }
grok-skill-workflow() { _vetcoders_skill_entry grok workflow "$@"; }
cursor-skill-workflow() { _vetcoders_skill_entry cursor workflow "$@"; }

_vetcoders_skill_wrapper_usage() {
  local skill="$1"
  case "$skill" in
    init)
      printf 'Usage: vc-init <claude|codex|agy|junie|grok|cursor> [--prompt <text>] [--file <path>]\n' >&2
      ;;
    marbles)
      printf 'Usage: vc-marbles <claude|codex|agy|junie|grok|cursor> [--prompt <text>|--file <path>|--depth <n>] [--count <n>]\n' >&2
      printf '       vc-marbles <pause|stop|resume|session|inspect|delete|gc> [args]\n' >&2
      ;;
    polarize)
      printf 'Usage: vc-polarize <claude|codex|agy|junie|grok|cursor> --task <text> [--prompt <text>] [--file <path>] [--no-aicx] [--no-context-corpus]\n' >&2
      printf '       vc-polarize <claude|codex|agy|junie|grok|cursor> [--count <n>] [--prompt <text>] [--file <path>]\n' >&2
      ;;
    *)
      printf 'Usage: vc-%s <claude|codex|agy|junie|grok|cursor> [--prompt <text>] [--file <path>]\n' "$skill" >&2
      ;;
  esac
}

_vetcoders_has_agent() {
  local candidate="${1:-}"
  case "$candidate" in
    claude|codex|agy|junie|grok|cursor) return 0 ;;
    gemini) return 1 ;;  # deprecated - gemini CLI is dead upstream, use agy (Google Antigravity CLI)
    *) return 1 ;;
  esac
}

_vetcoders_is_help_flag() {
  local candidate="${1:-}"
  [[ "$candidate" == "help" || "$candidate" == "-h" || "$candidate" == "--help" ]]
}

# Single deck-resolution gate. Every path that reaches for the installed
# `vibecrafted` deck MUST go through this: an explicitly set
# VIBECRAFTED_DECK_BIN (even empty) wins verbatim, VIBECRAFTED_TEST_MODE=1
# refuses PATH discovery outright (a test stub losing to the operator's live
# launcher has already dispatched real workers), otherwise PATH decides.
# Prints the deck path (empty = no deck available).
_vetcoders_resolve_deck_bin() {
  if [ -n "${VIBECRAFTED_DECK_BIN+x}" ]; then
    printf '%s\n' "${VIBECRAFTED_DECK_BIN}"
    return 0
  fi
  if [ "${VIBECRAFTED_TEST_MODE:-0}" = "1" ]; then
    return 0
  fi
  command -v vibecrafted 2>/dev/null || true
}

_vetcoders_no_deck_report() {
  printf 'vc-%s: vibecrafted helper layer is not loaded and no "vibecrafted" deck is on PATH.\n' "$1" >&2
  printf 'Run scripts/install-foundations.sh or add the repo bin/ to PATH.\n' >&2
  return 127
}

# Thin public `vc-*` contract: never reimplement help/flags; never shadow the
# installed deck with a second parser. Deck resolution goes through
# _vetcoders_resolve_deck_bin so test mode and DECK_BIN overrides hold.
# Prefer this for every mappable `vc-foo` ↔ `vibecrafted foo` pair.
_vetcoders_vc_passthrough() {
  local verb="$1"
  shift || true
  local deck_bin
  deck_bin="$(_vetcoders_resolve_deck_bin)"
  if [ -n "$deck_bin" ] && [ -x "$deck_bin" ]; then
    "$deck_bin" "$verb" "$@"
    return
  fi
  # No deck (bare host or test mode): agent-first skills still have a full
  # in-shell path through the wrapper layer — spawn launchers, marbles
  # control routing, usage help. Only verbs the wrapper actually understands
  # may fall back; deck-owned verbs (research, ship, loop, ...) keep the
  # clean 127 so a partial load never resurrects a diverged legacy surface.
  case "$verb" in
    audit|decorate|delegate|dou|followup|hydrate|init|justdo|implement|marbles|ownership|partner|polarize|prune|release|review|scaffold|workflow)
      if typeset -f _vetcoders_skill_wrapper >/dev/null 2>&1; then
        _vetcoders_skill_wrapper "$verb" "$@"
        return
      fi
      ;;
  esac
  _vetcoders_no_deck_report "$verb"
}

# Deck-owned skill help with local usage fallback; never launches anything.
_vetcoders_deck_help() {
  local skill="$1"
  local deck_bin
  deck_bin="$(_vetcoders_resolve_deck_bin)"
  if [ -n "$deck_bin" ] && [ -x "$deck_bin" ] \
    && "$deck_bin" "$skill" --help >/dev/null 2>&1; then
    "$deck_bin" "$skill" --help
    return 0
  fi
  _vetcoders_skill_wrapper_usage "$skill"
  return 1
}

_vetcoders_skill_wrapper() {
  local skill="$1"
  shift || true

  # Leading help must not enter agent-first parsing (that treated --help as an
  # agent name and either failed closed or, worse, side-effected resume paths).
  if _vetcoders_is_help_flag "${1:-}"; then
    # Prefer deck skill help only when the verb is a real deck/LAUNCHERS topic.
    # Redirect both streams so "not in the command deck" never leaks into help.
    _vetcoders_deck_help "$skill"
    return 0
  fi

  local tool="${1:-}"
  if [[ "$skill" == "marbles" ]]; then
    case "$tool" in
      pause|stop|resume|session|inspect|delete|gc)
        shift || true
        "marbles-$tool" "$@"
        return
        ;;
    esac
  fi

  [[ -n "$tool" ]] || {
    if _vetcoders_deck_help "$skill"; then
      return 0
    fi
    return 1
  }
  _vetcoders_has_agent "$tool" || {
    printf 'vc-%s expects claude|codex|agy|junie|grok|cursor as the first argument (not a placeholder with angle brackets).\n' "$skill" >&2
    _vetcoders_deck_help "$skill"
    return 1
  }
  shift || true

  if _vetcoders_is_help_flag "${1:-}"; then
    _vetcoders_deck_help "$skill"
    return 0
  fi

  case "$skill" in
    init) _vetcoders_skill_init "$tool" "$@" ;;
    partner) _vetcoders_skill_partner "$tool" "$@" ;;
    operator) _vetcoders_skill_operator "$tool" "$@" ;;
    marbles) _vetcoders_marbles "$tool" "$@" ;;
    *) _vetcoders_skill_entry "$tool" "$skill" "$@" ;;
  esac
}

_vetcoders_skill_dispatch() {
  # Graceful degradation for headless / partially-loaded shells: when the
  # helper layer is incomplete (version skew, stale snapshot, interrupted
  # source), fall back to the standalone command deck instead of dying with
  # "command not found: _vetcoders_skill_wrapper" (exit 127, zero run, zero
  # failure-card). Headless is a first-class environment.
  local skill="$1"
  shift || true
  if typeset -f _vetcoders_skill_wrapper >/dev/null 2>&1; then
    _vetcoders_skill_wrapper "$skill" "$@"
    return
  fi
  local deck_bin
  deck_bin="$(_vetcoders_resolve_deck_bin)"
  if [ -n "$deck_bin" ] && [ -x "$deck_bin" ]; then
    "$deck_bin" "$skill" "$@"
    return
  fi
  _vetcoders_no_deck_report "$skill"
}

_vetcoders_command_dispatch() {
  local command_name="$1"
  local deck_command="$2"
  shift 2 || true
  local deck_bin
  deck_bin="$(_vetcoders_resolve_deck_bin)"
  if [ -n "$deck_bin" ] && [ -x "$deck_bin" ]; then
    "$deck_bin" "$deck_command" "$@"
    return
  fi
  _vetcoders_no_deck_report "$command_name"
}

# Shell dotfiles commonly alias vc/vc-* (old container templates did); zsh
# refuses to define a function whose name is an active alias. Drop any such
# alias before defining the canonical functions.
if [ -n "${ZSH_VERSION:-}" ]; then
  unalias -m 'vc' 'vc-*' 2>/dev/null || true
else
  for _vc_alias in $(alias 2>/dev/null | sed -n 's/^alias \(vc[a-z-]*\)=.*/\1/p'); do
    unalias "$_vc_alias" 2>/dev/null || true
  done
  unset _vc_alias 2>/dev/null || true
fi

# Mappable public surface: exact `command vibecrafted <verb>` pass-through.
# Skill-dispatch wrappers required agent-first argv and reimplemented help —
# that is the interactive-zsh split-brain (2026-07-28 audit).
# Deck/LAUNCHERS skills → exact pass-through (help/flags owned by Python CLI).
vc-audit() { _vetcoders_vc_passthrough audit "$@"; }
vc-decorate() { _vetcoders_vc_passthrough decorate "$@"; }
vc-delegate() { _vetcoders_vc_passthrough delegate "$@"; }
vc-dou() { _vetcoders_vc_passthrough dou "$@"; }
vc-hydrate() { _vetcoders_vc_passthrough hydrate "$@"; }
vc-init() { _vetcoders_vc_passthrough init "$@"; }
vc-intents() { _vetcoders_vc_passthrough intents "$@"; }
# justdo is its own skill id (ADR-0001) — never alias help to implement.
vc-justdo() { _vetcoders_vc_passthrough justdo "$@"; }
vc-implement() { _vetcoders_vc_passthrough implement "$@"; }
vc-loop() { _vetcoders_vc_passthrough loop "$@"; }
vc-cron() { _vetcoders_vc_passthrough cron "$@"; }
vc-ship() { _vetcoders_vc_passthrough ship "$@"; }
vc-marbles() { _vetcoders_vc_passthrough marbles "$@"; }
vc-ownership() { _vetcoders_vc_passthrough ownership "$@"; }
vc-partner() { _vetcoders_vc_passthrough partner "$@"; }
# --task owns the prism band gate (abort/memo/pass/doctrine) — shell-only
# logic not yet ported to core (docs/RC_RUNTIME_POLARIZE.md port-debt); the
# python deck rejects --task, so that flag must route through the wrapper.
vc-polarize() {
  case " $* " in
    *" --task "*|*" --task="*) _vetcoders_skill_dispatch polarize "$@" ;;
    *) _vetcoders_vc_passthrough polarize "$@" ;;
  esac
}
vc-prune() { _vetcoders_vc_passthrough prune "$@"; }
vc-release() { _vetcoders_vc_passthrough release "$@"; }
vc-review() { _vetcoders_vc_passthrough review "$@"; }
vc-followup() { _vetcoders_vc_passthrough followup "$@"; }
vc-scaffold() { _vetcoders_vc_passthrough scaffold "$@"; }
vc-trust() { _vetcoders_vc_passthrough trust "$@"; }
vc-guard() { _vetcoders_vc_passthrough guard "$@"; }
vc-workflow() { _vetcoders_vc_passthrough workflow "$@"; }
vc-dispatch() { _vetcoders_vc_passthrough dispatch "$@"; }
# Agent-first skills without a deck LAUNCHERS verb — safe help via skill_wrapper.
vc-agents() { _vetcoders_skill_dispatch agents "$@"; }
# Deck now owns `vibecrafted operator` (interactive face). Passthrough matches vc-init.
vc-operator() { _vetcoders_vc_passthrough operator "$@"; }

vc-help() {
  _vetcoders_vc_passthrough help "$@"
  return $?
}

# Legacy multi-page help body retained only for offline/docs greps — not used
# by the public vc-help entrypoint (pass-through above).
_vetcoders_legacy_vc_help_body() {
  local crafted_home="${VIBECRAFTED_HOME:-$HOME/.vibecrafted}"
  cat <<'HELP'
𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. Framework — Skills & Helpers

Pipeline:  scaffold → init → workflow → implement → followup → marbles → audit → dou → decorate → hydrate → release
Modes:     partner (shared steering) | ownership (take the wheel)
Research:  research (triple-agent) | delegate (in-session)
Quality:   audit (plan falsification) | review (bounded diff/PR/commit) | followup (post-implementation direction) | prune
Video:     screenscribe (foundation)

Spawn helpers (per agent):
  <agent>-implement <plan.md>    Full implementation from plan
  <agent>-review <plan.md>       Bounded PR, branch, commit-range, or artifact review
  <agent>-plan <plan.md>         Planning only
  <agent>-prompt "text"          Quick one-shot prompt
  <agent>-scaffold                Architecture planning
  <agent>-followup               Post-implementation direction audit
  <agent>-skill-audit            Plan-vs-code falsification
  <agent>-dou                    Definition of Undone audit
  <agent>-hydrate                Market packaging
  <agent>-marbles                Convergence loop
  <agent>-decorate               Visual polish
  <agent>-release                Ship to market
  <agent>-prune                  Repo pruning
  <agent>-skill-implement        Autonomous e2e implementation (vc-implement)
  <agent>-justdo                 Alias for autonomous e2e implementation
  <agent>-partner                Collaborative partner mode with the user in the loop
  <agent>-observe --last         Check last report
  <agent>-await --last           Wait for metadata completion + summary

Swarm launchers:
  vc-research --prompt "text"    Triple-agent research swarm
  vc-research-await --last       Wait for the latest research swarm

Command deck:
  vibecrafted help               Main command surface
  vibecrafted <skill> <agent>    Run a repo skill via the launcher
  vibecrafted resume <agent>     Resume a previous session
  vibecrafted loop start --file plan.md --completion-promise READY
  vibecrafted cron line --root "$(pwd)" --every-minutes 10
  vibecrafted workflow claude -p "Plan and implement auth"
  vibecrafted marbles codex --count 3 --depth 3
  vibecrafted init claude        First-context entrypoint

Uniform skill flags:
  -p, --prompt <text>            Inline prompt; captures the rest of the command line
  -f, --file <path.md>           Input file as prompt context
  --count <n>                    Marbles / Polarize loop count (default: 3)
  --depth <n>                    Marbles plan crawl depth (default: 3)
  --session <id>                 Resume session id

Utilities:
  vc-git                         Git truth + visible worktree inventory
  repo-full                      Legacy full git context helper
  skills-sync                    Sync skills to agents
  vc-frontier-paths              Show frontier config paths
  vc-frontier-install            Install frontier presets (starship/atuin/vc_frame)
  vc-help                        This help

Frontier docs:  docs/FRONTIER.md (starship, atuin, optional vc_frame)
HELP
  printf '\nInbox:     %s/inbox/\n' "$crafted_home"
  printf 'Artifacts: %s/artifacts/<org>/<repo>/<YYYY_MMDD>/\n' "$crafted_home"
  printf 'Skills:    %s/skills/ (16 installed)\n' "$crafted_home"
}

skills-sync() {
  local script
  script="$(_vetcoders_spawn_script codex skills_sync.sh)" || return 1
  bash "$script" "$@"
}

_repo_full_rescue_emit_txt() {
  local file="$1"
  awk '
    /^REPO:/ || /^REMOTE:/ || /^BRANCH:/ || /^HEAD:/ || /^UPSTREAM:/ { print }
    /^===== STATUS =====/ { p=1; print; next }
    /^===== LOCAL DIFF FILES =====/ { p=1; print; next }
    /^===== LOCAL DIFF MATCHES ONLY =====/ { p=1; print; next }
    /^===== RELEASE\/WATCH\/LSP\/MCP COMMIT MSG MATCHES SINCE MAY 1 =====/ { p=1; print; next }
    /^===== STASHES =====/ { p=1; print; next }
    /^===== STASH MATCHES =====/ { p=1; print; next }
    /^===== CURRENT TREE MATCHES/ { p=0; next }
    /^===== RECENT COMMITS SINCE MAY 1/ { p=0; next }
    /^===== / && p { p=0 }
    p { print }
  ' "$file" | sed '/^$/N;/^\n$/D'
}

_repo_full_rescue_emit_patch() {
  local file="$1"
  local max_matches="${REPO_RESCUE_MAX_MATCHES:-120}"
  local pattern='release|publish|homebrew|npm|watch|lsp|mcp|install|aicx|fallback|sign|notary|formula|loctree-mcp|loctree-lsp|artifact|prebuilt|postinstall'

  echo "----- PATCH STAT -----"
  git apply --stat "$file" 2>/dev/null || awk '
    /^diff --git / {
      old=$3; new=$4;
      sub(/^a\//, "", old);
      sub(/^b\//, "", new);
      print old " -> " new;
    }
  ' "$file" | awk 'NF && !seen[$0]++'

  echo
  echo "----- MATCHED SIGNALS (bounded) -----"
  if command -v rg >/dev/null 2>&1; then
    rg -n -i "$pattern" "$file" | head -n "$max_matches"
  else
    awk -v pat="$pattern" -v max="$max_matches" '
      BEGIN { IGNORECASE=1 }
      $0 ~ pat {
        print FNR ":" $0;
        count++;
        if (count >= max) exit;
      }
    ' "$file"
  fi
}

_repo_full_rescue_emit_plain() {
  local file="$1"
  local max_lines="${REPO_RESCUE_MAX_LINES:-120}"
  local pattern='repo|remote|branch|head|upstream|status|stash|diff|release|publish|homebrew|npm|watch|lsp|mcp|install|aicx|fallback'
  if command -v rg >/dev/null 2>&1; then
    rg -n -i "$pattern" "$file" | head -n "$max_lines"
  else
    awk -v pat="$pattern" -v max="$max_lines" '
      BEGIN { IGNORECASE=1 }
      $0 ~ pat {
        print FNR ":" $0;
        count++;
        if (count >= max) exit;
      }
    ' "$file"
  fi
}

_repo_full_rescue_emit_file() {
  local file="$1"
  local bytes lines
  bytes="$(wc -c < "$file" | tr -d ' ')"
  lines="$(wc -l < "$file" | tr -d ' ')"

  echo
  echo "================================================================================"
  echo "### $(basename "$file")"
  echo "Path:  $file"
  echo "Size:  $bytes bytes"
  echo "Lines: $lines"
  echo

  case "$file" in
    *.patch|*.diff) _repo_full_rescue_emit_patch "$file" ;;
    *.txt) _repo_full_rescue_emit_txt "$file" ;;
    *.md|*.markdown) _repo_full_rescue_emit_plain "$file" ;;
    *) echo "Skipped: unsupported rescue evidence type." ;;
  esac
}

_repo_full_rescue() {
  local rescue_dir="${1:-${REPO_RESCUE_DIR:-$HOME/Desktop/loctree-release-rescue}}"
  local pattern="${2:-*}"
  local root branch head files_found=0 file

  if [[ ! -d "$rescue_dir" ]]; then
    echo "Rescue directory not found: $rescue_dir"
    echo "Usage: repo-full --rescue [evidence-dir] [glob]"
    return 1
  fi

  root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  branch="$(git symbolic-ref --short -q HEAD 2>/dev/null || echo "DETACHED_OR_NO_GIT")"
  head="$(git rev-parse --short HEAD 2>/dev/null || echo "no-git")"

  echo "==================== REPO RESCUE ===================="
  echo "Working dir:       $(pwd)"
  echo "Root:              $root"
  echo "Branch:            $branch"
  echo "HEAD short:        $head"
  echo "Evidence dir:      $rescue_dir"
  echo "Evidence glob:     $pattern"
  echo "Generated at:      $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  echo

  while IFS= read -r file; do
    files_found=1
    _repo_full_rescue_emit_file "$file"
  done < <(find "$rescue_dir" -maxdepth 1 -type f -name "$pattern" -print 2>/dev/null | sort)

  [[ "$files_found" != "0" ]] || echo "No rescue evidence files matched."
  echo
  echo "==================== RESCUE DONE ===================="
}

repo-full() {
  if [[ "${1:-}" == "--rescue" ]]; then
    shift
    _repo_full_rescue "$@"
    return
  fi

  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
    echo "Not a git repository."
    return 1
  }

  local cwd root repo branch head_short head_full upstream origin_url default_remote default_branch
  local last_tag stash_count staged_count unstaged_count untracked_count worktree_count
  local upstream_ahead upstream_behind

  cwd="$(pwd)"
  root="$(git rev-parse --show-toplevel 2>/dev/null)"
  repo="$(basename "$root")"
  branch="$(git symbolic-ref --short -q HEAD 2>/dev/null || echo "DETACHED_HEAD")"
  head_short="$(git rev-parse --short HEAD 2>/dev/null)"
  head_full="$(git rev-parse HEAD 2>/dev/null)"
  upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || echo "no upstream")"
  origin_url="$(git remote get-url origin 2>/dev/null || echo "no origin")"
  last_tag="$(git describe --tags --abbrev=0 2>/dev/null || echo "no tags")"
  stash_count="$(git stash list 2>/dev/null | wc -l | tr -d ' ')"
  staged_count="$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')"
  unstaged_count="$(git diff --name-only 2>/dev/null | wc -l | tr -d ' ')"
  untracked_count="$(git ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ')"
  worktree_count="$(git worktree list 2>/dev/null | wc -l | tr -d ' ')"

  default_remote="$(git remote | awk 'NR==1{print; exit}')"
  [[ -z "$default_remote" ]] && default_remote="origin"

  default_branch="$(git symbolic-ref --quiet --short "refs/remotes/${default_remote}/HEAD" 2>/dev/null | sed "s#^${default_remote}/##")"
  [[ -z "$default_branch" ]] && default_branch="$(git remote show "$default_remote" 2>/dev/null | sed -n '/HEAD branch/s/.*: //p' | head -n 1)"
  [[ -z "$default_branch" ]] && default_branch="unknown"

  # shellcheck disable=SC1083 # @{u} is git upstream ref syntax, not shell braces
  if git rev-parse '@{u}' >/dev/null 2>&1; then
    read -r upstream_ahead upstream_behind <<< "$(git rev-list --left-right --count HEAD...'@{u}' 2>/dev/null)"
  else
    upstream_ahead="-"
    upstream_behind="-"
  fi

  _repo_full_compare_ref() {
    local ref="$1"
    git rev-parse --verify "$ref" >/dev/null 2>&1 || return 0
    local ahead behind sha
    read -r ahead behind <<< "$(git rev-list --left-right --count HEAD..."$ref" 2>/dev/null)"
    sha="$(git rev-parse --short "$ref" 2>/dev/null)"
    printf "%-24s ahead:%-4s behind:%-4s sha:%s\n" "$ref" "$ahead" "$behind" "$sha"
  }

  # shellcheck disable=SC2016 # expressions in awk are intentional
  _repo_full_human_awk='
    function human(x) {
      split("B KB MB GB TB", u, " ");
      i=1;
      while (x >= 1024 && i < 5) { x /= 1024; i++ }
      return sprintf("%.1f %s", x, u[i]);
    }
    {
      size=$1;
      $1="";
      sub(/^\t/, "", $0);
      printf "%10s  %s\n", human(size), $0;
    }
  '

  echo "==================== REPO FULL ===================="
  echo "Repo:              $repo"
  echo "Working dir:       $cwd"
  echo "Root:              $root"
  echo "Branch:            $branch"
  echo "Default remote:    $default_remote"
  echo "Default branch:    $default_branch"
  echo "Upstream:          $upstream"
  echo "Ahead / Behind:    $upstream_ahead / $upstream_behind"
  echo "Origin:            $origin_url"
  echo "HEAD short:        $head_short"
  echo "HEAD full:         $head_full"
  echo "Last tag:          $last_tag"
  echo "Stashes:           $stash_count"
  echo "Worktrees:         $worktree_count"
  echo "Staged changes:    $staged_count"
  echo "Unstaged changes:  $unstaged_count"
  echo "Untracked files:   $untracked_count"
  echo

  echo "==================== HEAD COMMIT ===================="
  git show -s --format="Commit: %H%nAuthor: %an <%ae>%nDate:   %ad%nTitle:  %s" --date=iso HEAD
  echo

  echo "==================== STATUS ===================="
  git status -sb
  echo

  echo "==================== WORKTREE ===================="
  git status --short
  echo

  echo "==================== COMPARE TO IMPORTANT REFS ===================="
  {
    [[ "$upstream" != "no upstream" ]] && echo "$upstream"
    [[ "$default_branch" != "unknown" ]] && echo "${default_remote}/${default_branch}"
    echo "origin/develop"
    echo "origin/main"
  } | awk 'NF && !seen[$0]++' | while IFS= read -r ref; do
    _repo_full_compare_ref "$ref"
  done
  echo

  echo "==================== REMOTES ===================="
  git remote -v
  echo

  echo "==================== LOCAL BRANCHES (RECENT FIRST) ===================="
  git for-each-ref \
    --sort=-committerdate \
    refs/heads \
    --format='%(HEAD) %(refname:short) | upstream=%(upstream:short) | %(committerdate:short) | %(objectname:short) | %(subject)'
  echo

  echo "==================== LAST 20 COMMITS ===================="
  git log --oneline --decorate --graph -n 20
  echo

  echo "==================== STAGED DIFF STAT ===================="
  git diff --cached --stat
  echo

  echo "==================== UNSTAGED DIFF STAT ===================="
  git diff --stat
  echo

  echo "==================== STASH LIST ===================="
  git stash list 2>/dev/null
  echo

  echo "==================== WORKTREES ===================="
  git worktree list 2>/dev/null
  echo

  echo "==================== SUBMODULES ===================="
  if [[ -f "$root/.gitmodules" ]]; then
    git submodule status
  else
    echo "No submodules."
  fi
  echo

  echo "==================== TOP 10 LARGEST TRACKED FILES ===================="
  if git ls-files -z | grep -q . 2>/dev/null; then
    { git ls-files -z | xargs -0 stat -f "%z\t%N" 2>/dev/null ||
      git ls-files -z | xargs -0 stat -c "%s\t%n" 2>/dev/null; } \
      | sort -nr \
      | head -n 10 \
      | awk "$_repo_full_human_awk"
  else
    echo "No tracked files."
  fi
  echo

  echo "==================== GIT CONFIG ===================="
  echo "user.name:         $(git config --get user.name 2>/dev/null || echo "not set")"
  echo "user.email:        $(git config --get user.email 2>/dev/null || echo "not set")"
  echo "pull.rebase:       $(git config --get pull.rebase 2>/dev/null || echo "not set")"
  echo "init.defaultBranch:$(git config --get init.defaultBranch 2>/dev/null || echo "not set")"
  echo

  echo "==================== DONE ===================="
}

# start/dashboard MUST NOT passthrough to `vibecrafted start|dashboard`.
# Deck cmd_start/cmd_dashboard call these helpers after sourcing this file;
# a thin alias re-enters Python → deck → helper forever (fork bomb, 2026-07-28).
# --help only may touch the deck (help exits before _run_helper).
#
# Product entry choke (goal: vc-start owns lifecycle): shell vc-start is the
# live operator path — it never enters deck cmd_start. Prepare lives here so
# helpers/config projection/server eye run on the real backyard ride.
vc-start() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    _vetcoders_vc_passthrough start --help
    return $?
  fi
  # A public entry without a controlling terminal owes the operator a visible
  # terminal, not a refusal: vc-frame cannot create a session over a pipe, so
  # route through the product terminal host (the same owner Vibecrafted.app
  # uses) and let the child run this very entry with a real PTY. Probe mode is
  # a deliberate non-interactive contract and is never rerouted.
  if [[ "${VIBECRAFTED_PRODUCT_ENTRY_PROBE:-0}" != "1" ]] &&
    command -v _vetcoders_needs_vc_terminal_entry >/dev/null 2>&1 &&
    _vetcoders_needs_vc_terminal_entry; then
    local _vc_start_front_door="" _vc_start_project_root=""
    _vc_start_front_door="$(_vetcoders_product_front_door vc-start 2>/dev/null || true)"
    if [[ -z "$_vc_start_front_door" ]]; then
      # No front door means no supported way to obtain a PTY; continuing would
      # walk straight into vc-frame's strict TTY guard and die there anyway.
      printf 'vc-start: no TTY and no installed vc-start front door to open a terminal with.\n' >&2
      printf 'Run vc-start from a terminal, or install the runtime so bin/vc-terminal and bin/vc-start exist.\n' >&2
      return 1
    fi
    # The project is the caller's repository, resolved through the one owner —
    # NOT $VIBECRAFTED_ROOT, which every front door pins to the generation.
    _vc_start_project_root="$(_vetcoders_effective_project_root)"
    _vetcoders_open_entry_in_vc_terminal \
      "$_vc_start_front_door" "$_vc_start_project_root" "$@" || return 1
    return 0
  fi
  # Required lifecycle helpers belong to the admitted facade.
  if ! declare -F _vetcoders_product_entry_prepare >/dev/null 2>&1; then
    printf 'vc-start: required product preparation helper missing\n' >&2
    return 1
  fi
  _vetcoders_product_entry_prepare || return $?
  # Tests/doctor: print env effects without attach (no TUI, no session create).
  if [[ "${VIBECRAFTED_PRODUCT_ENTRY_PROBE:-0}" == "1" ]]; then
    if ! declare -F _vetcoders_product_entry_probe_print >/dev/null 2>&1; then
      printf 'vc-start: required product probe helper missing\n' >&2
      return 1
    fi
    _vetcoders_product_entry_probe_print
    return $?
  fi
  if [[ "${1:-}" == "resume" ]]; then
    shift || true
    _vetcoders_resume_operator_session "$@"
    return
  fi
  if [[ "${1:-}" == "operator" || "${1:-}" == "vibecrafted" ]]; then
    shift || true
  fi
  _vetcoders_launch_dashboard operator "$@"
}

vc-dashboard() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    _vetcoders_vc_passthrough dashboard --help
    return $?
  fi
  _vetcoders_launch_dashboard "$@"
}

vc-frontier-paths() {
  local starship_config atuin_config vc_frame_config
  starship_config="$(_vetcoders_frontier_file "starship.toml")" || return 1
  atuin_config="$(_vetcoders_frontier_file "atuin/config.toml" 2>/dev/null || true)"
  vc_frame_config="$(_vetcoders_frontier_file "vc-frame/config.kdl" 2>/dev/null || true)"

  printf 'STARSHIP_CONFIG=%s\n' "$starship_config"
  [[ -n "$atuin_config" ]] && printf 'ATUIN_CONFIG=%s\n' "$atuin_config"
  [[ -n "$vc_frame_config" ]] && printf 'VC_FRAME_CONFIG_DIR=%s\n' "$(dirname "$vc_frame_config")"
  return 0
}

vc-frontier-install() {
  local repo_root script base
  repo_root="$(_vetcoders_frontier_source_root)" || {
    echo "Repo-owned frontier source not found." >&2
    return 1
  }
  base="$(_vetcoders_spawn_home "vc-agents")"
  script="$base/scripts/install-frontier-config.sh"
  
  [[ -f "$script" ]] || {
    echo "Frontier installer not found: $script" >&2
    return 1
  }
  bash "$script" --source "$repo_root" "$@"
}
