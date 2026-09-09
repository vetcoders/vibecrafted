# shellcheck shell=bash
# Extracted from vetcoders.sh; sourced only by the compatibility facade.

_vetcoders_vc_frame_owner_root() {
  # The loaded shell belongs to the selected payload. Ambient roots and cwd
  # cannot select a different generation after the entrypoint has been chosen.
  if [[ -n "${_vetcoders_vc_frame_loaded_root:-}" ]]; then
    printf '%s\n' "$_vetcoders_vc_frame_loaded_root"
    return 0
  fi
  local source_file="${BASH_SOURCE[0]:-}"
  if [[ -z "$source_file" && -n "${ZSH_VERSION:-}" ]]; then
    source_file="$(eval 'printf "%s\n" "${(%):-%x}"')"
  fi
  [[ -n "$source_file" ]] || return 1
  (cd -P "$(dirname "$source_file")/../../../../.." && pwd -P)
}

# Capture at source time, before cwd or a tools-current symlink can move.
unset _vetcoders_vc_frame_loaded_root
_vetcoders_vc_frame_loaded_root="$(_vetcoders_vc_frame_owner_root)" || return 1

_vetcoders_vc_frame_developer_mode() {
  local owner_root
  [[ "${VIBECRAFTED_PREFER_REPO_VC_FRAME:-0}" == "1" ]] || return 1
  owner_root="$(_vetcoders_vc_frame_owner_root)" || return 1
  # Retain the existing opt-in only for a directly sourced Git checkout.
  # A leaked development preference never changes installed product startup.
  [[ -e "$owner_root/.git" && ! -f "$owner_root/runtime-manifest.json" ]]
}

_vetcoders_vc_frame_missing_message() {
  local owner_root
  owner_root="$(_vetcoders_vc_frame_owner_root)" || return 1
  printf 'vc-frame: installed product entry/engine missing under: %s/{bin,libexec}/vc-frame\n' "$owner_root" >&2
  printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
}

_vetcoders_vc_frame_bin() {
  local owner_root bin
  owner_root="$(_vetcoders_vc_frame_owner_root)" || return 1
  if _vetcoders_vc_frame_developer_mode; then
    bin="${VIBECRAFTED_VC_FRAME_BIN:-}"
    [[ -n "$bin" ]] || bin="$(command -v vc-frame 2>/dev/null || true)"
  else
    bin="$owner_root/bin/vc-frame"
    if [[ -L "$bin" || -L "$owner_root/libexec/vc-frame" || ! -f "$owner_root/libexec/vc-frame" || ! -x "$owner_root/libexec/vc-frame" ]]; then
      _vetcoders_vc_frame_missing_message
      return 1
    fi
  fi
  if [[ "$bin" == /* && -f "$bin" && -x "$bin" ]]; then
    printf '%s\n' "$bin"
    return 0
  fi
  _vetcoders_vc_frame_missing_message
  return 1
}

_vetcoders_require_vc_frame() {
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  _vetcoders_vc_frame_bin >/dev/null 2>&1 || {
    _vetcoders_vc_frame_missing_message
    return 1
  }
}

# ---------------------------------------------------------------------------
# Product terminal host (VC Terminal) — the PTY supplier for public VC entries.
#
# vc-frame keeps its strict TTY guard: it is an internal surface and still
# refuses a pipe. The PUBLIC entries (vc-start / vc-resume) must not inherit
# that refusal, because the operator's caller (agent shell, script, app hook)
# legitimately has no controlling terminal. The supported answer is the same
# one Vibecrafted.app already uses:
#
#   vc-terminal -e <launch-primary-shell.zsh> <product front door> [argv...]
#
# (AppDelegate.openWorkspaceTerminal, config/vc-terminal/vibecrafted.toml).
# We reuse that owner instead of inventing nohup/setsid/osascript launchers.
# ---------------------------------------------------------------------------

_vetcoders_vc_terminal_missing_message() {
  local owner_root
  owner_root="$(_vetcoders_vc_frame_owner_root)" || return 1
  printf 'vc-terminal: installed product entry/engine missing under: %s/{bin,libexec}/vc-terminal\n' \
    "$owner_root" >&2
  printf 'A non-interactive caller has no PTY, so Vibecrafted must open its own terminal host.\n' >&2
  printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
}

# Strict twin of _vetcoders_vc_frame_bin: entry AND engine must be real,
# executable, non-symlink files inside the generation that loaded this shell.
_vetcoders_vc_terminal_bin() {
  local owner_root bin engine
  owner_root="$(_vetcoders_vc_frame_owner_root)" || return 1
  bin="$owner_root/bin/vc-terminal"
  engine="$owner_root/libexec/vc-terminal"
  if [[ -L "$bin" || -L "$engine" || ! -f "$engine" || ! -x "$engine" ]]; then
    return 1
  fi
  if [[ "$bin" == /* && -f "$bin" && -x "$bin" ]]; then
    printf '%s\n' "$bin"
    return 0
  fi
  return 1
}

# Installer-owned product launcher. ONE physical owner, the same boundary
# scripts/vc-terminal-product-entry.sh already enforces for the product config:
# $HOME/.config/vibecrafted/vc-terminal/, with no symlinked ancestor.
#
# XDG_CONFIG_HOME is deliberately NOT consulted. The terminal wrapper resets
# XDG_CONFIG_HOME to $HOME/.config on the way in, so honouring it here would
# let a foreign launcher be passed verbatim after -e while the wrapper insists
# the canonical one is in force — two truths for one file. The generation's
# config/alacritty copy is installer INPUT, never a startup fallback: silently
# substituting it hides a broken install behind a working-looking window.
_vetcoders_vc_terminal_primary_shell_path() {
  printf '%s/.config/vibecrafted/vc-terminal/launch-primary-shell.zsh\n' "$HOME"
}

_vetcoders_vc_terminal_primary_shell() {
  local config_home="$HOME/.config"
  local candidate=""
  candidate="$(_vetcoders_vc_terminal_primary_shell_path)"
  if [[ -L "$config_home" || -L "$config_home/vibecrafted" \
    || -L "$config_home/vibecrafted/vc-terminal" || -L "$candidate" ]]; then
    return 1
  fi
  [[ -f "$candidate" && -x "$candidate" ]] || return 1
  printf '%s\n' "$candidate"
}

# True when this process is a public VC entry that owes the operator a visible
# terminal. The signals are the real controlling terminal, one explicit
# re-entry boundary exported into the child, and -- for an inherited frame
# marker or operator-session name -- the ENGINE's word that the named session
# is a surface someone is attached to. The environment alone is never that
# proof (2026-09-09, Founder repros for init/operator/resume/fork: every one
# ran from an agent shell carrying VC_FRAME_SESSION_NAME=vibecrafted while the
# only live sessions were other projects', and the direct path either panicked
# on the missing host or parked the provider tab where nobody was looking).
_vetcoders_needs_vc_terminal_entry() {
  # The child we spawn re-enters the same entry; the boundary stops the loop
  # even if the host somehow fails to hand us a PTY.
  [[ -z "${VIBECRAFTED_TERMINAL_ENTRY:-}" ]] || return 1
  # A real terminal means the direct path is already correct — never reroute it.
  # (Deliberately no test flag here: a test that models the terminal child
  # sets the re-entry boundary above, the way the real child receives it.)
  [[ ! -t 0 || ! -t 1 ]] || return 1
  # Inside a frame the caller owns a visible surface only when the marker's
  # session is live AND has an attached client (or the engine cannot be asked,
  # which keeps a plain sourced checkout on the old path). A marker whose
  # session is dead, missing or unattended is ambient context, not a surface.
  if _vetcoders_in_vc_frame; then
    ! _vetcoders_has_usable_vc_frame_surface || return 1
  fi
  # An explicitly named operator session is honoured on the direct path when
  # it is a surface by the same test; a name the engine reports dead, missing
  # or unattended is inherited env, and the direct path would only hang the
  # provider tab on a host nobody sees (or create one nobody asked for).
  if [[ -n "${VIBECRAFTED_OPERATOR_SESSION:-}" ]]; then
    case "$(_vetcoders_vc_frame_surface_state "$VIBECRAFTED_OPERATOR_SESSION")" in
      usable | unknown) return 1 ;;
    esac
  fi
  return 0
}

# Open the product terminal host on THIS project and run the prepared entry
# inside it. The project root is passed in by the public entry (explicit --root
# wins there), never re-derived from cwd here: `vibecrafted resume codex --root
# /project/B` run from /project/A must open B, not A.
#
# Returns 0 only when the host ADMITTED the launch; 1 when it did not (host
# unavailable, canonical launcher missing, immediate rejection).
_vetcoders_open_entry_in_vc_terminal() {
  local front_door="$1"
  local project_root="$2"
  shift 2
  local terminal_bin primary_shell
  terminal_bin="$(_vetcoders_vc_terminal_bin)" || {
    _vetcoders_vc_terminal_missing_message
    return 1
  }
  primary_shell="$(_vetcoders_vc_terminal_primary_shell)" || {
    printf 'vc-terminal: canonical product shell launcher missing or not a real file: %s\n' \
      "$(_vetcoders_vc_terminal_primary_shell_path)" >&2
    printf 'Vibecrafted reads only that physical path (no XDG override, no release-default fallback).\n' >&2
    printf 'Install explicitly: python3 <checkout>/scripts/vetcoders_install.py runtime-install --payload-root <Runtime-Pack>\n' >&2
    return 1
  }
  [[ -n "$front_door" && -x "$front_door" ]] || {
    printf 'vc-terminal: product front door is not executable: %s\n' "$front_door" >&2
    return 1
  }
  [[ -n "$project_root" && -d "$project_root" ]] || {
    printf 'vc-terminal: project root is not a directory: %s\n' "$project_root" >&2
    return 1
  }

  # Bounded admission receipt. A canonical wrapper that rejects a missing
  # product config (exit 2) or an invalid native host (exit 127) dies at once;
  # reporting "opened" for that leaves the operator waiting for a window that
  # never appears. A host that really opened writes nothing in this window.
  #
  # The receipt lives in a private DIRECTORY, not a bare temp file, because the
  # writer outlives this window by design: a host that really opened exits only
  # when the operator closes the window, minutes from now. Unlinking a bare
  # file would let that late write recreate it — a stale artifact left in
  # TMPDIR, at a name any other process could have taken over in the meantime.
  # Removing the directory makes the late write simply fail: a file cannot be
  # created without its parent.
  local receipt_dir="" receipt=""
  receipt_dir="$(mktemp -d "${TMPDIR:-/tmp}/vc-terminal-admit.XXXXXX")" || return 1
  receipt="$receipt_dir/status"

  # Detach the host into its OWN process session, not merely a backgrounded
  # + disowned job. `disown` only drops the job from THIS shell's job table;
  # under a non-interactive/non-monitor shell (the normal case for a public
  # entry invoked by a script or agent tool) the child stays in the SAME OS
  # process group as the caller. When the caller is itself a transient tool
  # process torn down as a group, the "detached" terminal host is reaped
  # with it — no signal was ever sent to it directly, its group just died.
  # This is the same class of bug already solved for headless dispatch in
  # runtime/scripts/lib/launcher.sh (spawn_launch_headless): macOS has no
  # setsid(1), so Python's start_new_session=True (posix setsid) is the
  # portable true-detach. It must run in a freshly forked child, never on
  # the caller's own process (self-setsid fails once a process is already
  # its group leader, and whether bash/zsh hands `&` its own pgid here is
  # shell/mode-dependent) — so a short-lived foreground driver spawns one
  # detached writer that outlives it.
  local python_bin=""
  python_bin="$(_vetcoders_internal_python)"
  command -v "$python_bin" >/dev/null 2>&1 || {
    printf 'vc-terminal: a supported internal Python interpreter is required to open an independent terminal session.\n' >&2
    rm -rf "$receipt_dir"
    return 1
  }

  local argv_file="$receipt_dir/argv"
  {
    printf '%s\0' "$terminal_bin" "--working-directory" "$project_root" \
      "-e" "$primary_shell" "$front_door"
    if (($#)); then
      printf '%s\0' "$@"
    fi
  } >"$argv_file"

  # The writer's own stdio is fully closed off the caller's (possibly
  # pipe-backed) fds before it ever execs the host, so a closed pipe on the
  # caller's side can never reach into a still-running terminal.
  #
  # This FOREGROUND driver's own exit status is load-bearing: an interpreter
  # that fails to start at all (a broken/shimmed internal interpreter), or whose own
  # subprocess.Popen call below (the one spawning the detached writer -- NOT
  # the writer's own later exec of the host, already handled by the receipt)
  # raises, must never fall through to the receipt-polling loop and be
  # reported as "accepted". No writer ever ran in that case, so no receipt
  # will ever appear -- and that ABSENCE is indistinguishable from a real
  # host still opening unless this driver's own exit status is checked
  # first, before the poll below ever starts.
  local driver_rc=0
  VC_TERMINAL_ARGV_FILE="$argv_file" VC_TERMINAL_RECEIPT="$receipt" \
    VIBECRAFTED_TERMINAL_ENTRY=1 "$python_bin" - <<'PY'
import os
import subprocess
import sys

argv_file = os.environ["VC_TERMINAL_ARGV_FILE"]
receipt = os.environ["VC_TERMINAL_RECEIPT"]
with open(argv_file, "rb") as fh:
    raw = fh.read()
argv = [part.decode("utf-8", "surrogateescape") for part in raw.split(b"\0")[:-1]]

# Baked in as a literal (not re-read from disk): the writer must not depend
# on argv_file surviving the bounded admission window that follows.
writer_src = (
    "import subprocess\n"
    "argv = " + repr(argv) + "\n"
    "try:\n"
    "    rc = subprocess.call(argv)\n"
    "except OSError:\n"
    "    rc = 127\n"
    "try:\n"
    "    with open(" + repr(receipt) + ", 'w') as fh:\n"
    "        fh.write(str(rc))\n"
    "except OSError:\n"
    "    pass\n"
)

# The window we open is, by construction, NOT inside the frame this process
# inherited its markers from: an attached-context marker that reaches the
# child would make it adopt a session that is dead, missing or unwatched (the
# very reason a window is being opened), and a pending attach/switch spec is
# this process's own state. The child resolves its own target from scratch.
child_env = dict(os.environ)
for key in (
    "VC_FRAME",
    "VC_FRAME_PANE_ID",
    "VC_FRAME_SESSION_NAME",
    "ZELLIJ",
    "ZELLIJ_PANE_ID",
    "ZELLIJ_SESSION_NAME",
    "VIBECRAFTED_OPERATOR_SESSION",
    "VIBECRAFTED_PENDING_VC_FRAME_ATTACH",
    "VIBECRAFTED_PENDING_VC_FRAME_SWITCH",
    "VIBECRAFTED_PREPARED_VC_FRAME_SESSION",
):
    child_env.pop(key, None)

try:
    subprocess.Popen(
        [sys.executable, "-c", writer_src],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=child_env,
    )
except OSError as exc:
    # A real failure to fork/exec the detached writer -- e.g. a resource
    # limit -- not a rejection from the terminal host (no writer exists yet
    # to reject anything). Report it plainly and exit non-zero so the shell
    # side never mistakes this for "starting".
    print(
        f"vc-terminal: could not start the detached terminal writer: {exc}",
        file=sys.stderr,
    )
    sys.exit(1)
PY
  driver_rc=$?

  if ((driver_rc != 0)); then
    rm -rf "$receipt_dir"
    printf 'vc-terminal: failed to start an independent terminal session (spawner exited %s); no window was opened.\n' \
      "$driver_rc" >&2
    printf '  host:    %s\n' "$terminal_bin" >&2
    printf '  project: %s\n' "$project_root" >&2
    return 1
  fi

  # `status` itself is a zsh readonly special parameter (an alias for `$?`):
  # `local status=""` errors "read-only variable: status" under zsh (function
  # still exits 0 — the error is non-fatal but the local never actually holds
  # our receipt content). Use a task-specific name instead of a reserved word.
  local waited=0 admit_status=""
  while ((waited < 30)); do
    admit_status="$(cat "$receipt" 2>/dev/null || true)"
    [[ -z "$admit_status" ]] || break
    sleep 0.05
    ((waited += 1))
  done
  rm -rf "$receipt_dir"
  if [[ -n "$admit_status" && "$admit_status" != "0" ]]; then
    printf 'vc-terminal: the terminal host rejected this launch (exit %s); no window was opened.\n' \
      "$admit_status" >&2
    printf '  host:    %s\n' "$terminal_bin" >&2
    printf '  project: %s\n' "$project_root" >&2
    return 1
  fi

  # Two different truths, said in two different sentences. An exit 0 inside the
  # window is a host that spawned the window and returned. No exit at all is
  # only the ABSENCE of a rejection — the shape a real window has, but not
  # proof of one, and it must not be reported as if we had seen it open.
  if [[ -n "$admit_status" ]]; then
    printf 'No TTY here — opened the Vibecrafted terminal for this project.\n' >&2
  else
    printf 'No TTY here — the Vibecrafted terminal host accepted this launch (starting; it had not exited after 1.5s).\n' >&2
  fi
  printf '  project: %s\n' "$project_root" >&2
  printf '  entry:   %s' "${front_door##*/}" >&2
  if (($#)); then
    printf ' %s' "$@" >&2
  fi
  printf '\n' >&2
  return 0
}

# Canonical product front door for a public verb, inside the generation that
# loaded this shell. Never PATH-resolved: a stale ~/.local/bin shim from
# another generation must not capture the escalation.
_vetcoders_product_front_door() {
  local verb="$1" owner_root candidate
  owner_root="$(_vetcoders_vc_frame_owner_root)" || return 1
  candidate="$owner_root/bin/$verb"
  [[ -f "$candidate" && -x "$candidate" ]] || return 1
  printf '%s\n' "$candidate"
}

# Open the product terminal for a public declaration that has no visible
# surface here, and re-enter it there through the `vibecrafted` front door.
# One owner for resume/init/operator/partner (shell) and fork (deck): the
# hosted argv is `<verb> <agent> [args…]`, exactly what the child re-parses on
# the project root the window is opened on. 0 = the host admitted the launch
# (the caller returns 0 and does nothing else here); 1 = it did not.
_vetcoders_open_public_entry_in_vc_terminal() {
  local project_root="$1"
  shift
  local verb="${1:-entry}"
  local front_door=""
  front_door="$(_vetcoders_product_front_door vibecrafted 2>/dev/null || true)"
  if [[ -z "$front_door" ]]; then
    # No front door means no supported way to obtain a PTY. Falling through
    # would compose a continuity pack / provider command for a launch that
    # cannot happen.
    printf 'vc-%s: no TTY and no installed vibecrafted front door to open a terminal with.\n' "$verb" >&2
    printf 'Run the %s from a terminal, or install the runtime so bin/vc-terminal and bin/vibecrafted exist.\n' "$verb" >&2
    return 1
  fi
  _vetcoders_open_entry_in_vc_terminal "$front_door" "$project_root" "$@"
}

# The shared contract-parsed half of that escalation, for the shell entries
# that went through _vetcoders_parse_contract (resume, init, operator, partner).
# Placement is the caller's contract: BEFORE any continuity pack, prompt
# composition or provider command, so the escalated child does each of them
# exactly once. Returns 0 = escalated (caller returns 0), 1 = a terminal was
# needed but could not be opened (caller returns 1), 2 = direct path here.
_vetcoders_declaration_escalate_if_needed() {
  local verb="$1" tool="$2"
  shift 2
  command -v _vetcoders_needs_vc_terminal_entry >/dev/null || return 2
  _vetcoders_needs_vc_terminal_entry || return 2
  local project_root=""
  project_root="$(_vetcoders_effective_project_root)"
  # The child re-parses this very vector, but from the terminal's working
  # directory — which IS the normalized root. Forwarding a raw relative token
  # would resolve it a second time, one level deeper; hand over the absolute
  # value so the child, the session name, AICX and the provider read the same
  # project.
  _vetcoders_rewrite_contract_root_argv "${_vetcoders_contract_root:-}" "$@"
  # shellcheck disable=SC2154  # the rewritten vector is the parser's global (prompts.sh)
  _vetcoders_open_public_entry_in_vc_terminal "$project_root" "$verb" "$tool" \
    "${_vetcoders_contract_argv[@]}" || return 1
  return 0
}

# Normalize an explicit --root/--repo ONCE, before anything changes cwd:
# `--root ../B` must mean ../B relative to where the operator typed it, not
# relative to wherever a later step happens to stand. Every downstream reader
# (terminal cwd, workspace/session naming, AICX, provider cwd) then sees one
# absolute value. 1 when the declared path is not a directory.
_vetcoders_normalize_declared_contract_root() {
  local verb="${1:-resume}"
  [[ -n "${_vetcoders_contract_root:-}" ]] || return 0
  local normalized=""
  normalized="$(_vetcoders_absolute_physical_path "$_vetcoders_contract_root")"
  if [[ -z "$normalized" || ! -d "$normalized" ]]; then
    printf '%s: --root is not an existing directory: %s\n' "$verb" "$_vetcoders_contract_root" >&2
    return 1
  fi
  _vetcoders_contract_root="$normalized"
}

# One launch owner for the interactive declarations: the init / operator /
# partner faces, and fork's declared path. Prepare the workspace session
# WITHOUT handing the terminal over, hang the provider tab on it, print the
# receipt, and only then -- last act -- enter it. Before this owner the init
# family prepared in the FOREGROUND: with no live session the operator layout
# blocked until the Founder detached, and the provider tab appeared only after
# the window they were waiting in had been closed (the ordering defect resume
# fixed with defer-attach on 2026-09-06). Same terminal-side arguments as
# resume: the declared root routes through the declared-workspace owner.
_vetcoders_launch_interactive_declaration() {
  local verb="$1" runtime="$2" tab_name="$3" command_text="$4"
  local declared_root="${5:-}" receipt="${6:-}"
  _vetcoders_prepare_operator_runtime "$runtime" defer-attach "$declared_root" "$verb" || return 1
  if [[ -z "${VIBECRAFTED_OPERATOR_SESSION:-}" ]]; then
    printf '%s: no visible operator target could be prepared here; refusing to start %s in a session nobody can see.\n' \
      "$verb" "$tab_name" >&2
    printf '  run the %s from a terminal, or let the public entry open one.\n' "$verb" >&2
    return 1
  fi
  _vetcoders_spawn_into_operator_session "$tab_name" "$command_text" || return 1
  printf '%s launched in workspace session: %s\n' "$verb" "$VIBECRAFTED_OPERATOR_SESSION"
  [[ -z "$declared_root" ]] || printf '  root:    %s\n' "$declared_root"
  [[ -z "$receipt" ]] || printf '%s\n' "$receipt"
  # The tab exists, so the terminal may now be handed over. This blocks until
  # the Founder detaches, which is exactly what they asked for.
  _vetcoders_attach_prepared_vc_frame_session
}

# vc-frame needs a real PTY to enable raw mode. When stdin/stdout are pipes
# (curl|bash, ssh without -t, agent subprocess), vc-frame panics with an
# unhelpful Rust traceback. Catch the missing-TTY case early and return a
# user-actionable message instead.
_vetcoders_require_tty() {
  if [[ -t 0 && -t 1 ]]; then
    return 0
  fi
  cat >&2 <<'EOF'

vc-init requires an interactive terminal (TTY) to spawn a vc-frame session.

Detected: stdin or stdout is not a TTY (pipe, redirect, or non-interactive
SSH/agent context). vc-frame needs a real PTY to switch into raw mode.

To proceed:
  - Local terminal:        run `vibecrafted init <agent>` directly
  - SSH:                   add `-t`, e.g. `ssh -t user@host vibecrafted init claude`
  - Inside another agent:  vc-frame cannot start from a piped subprocess.
                           Use `vibecrafted <action> <agent>` (no vc-frame wrapper)
                           or run vc-init in a separate user-attached shell.

EOF
  return 1
}

_vetcoders_in_vc_frame() {
  # VC_FRAME_* is the trusted attached-context signal. Legacy ZELLIJ_* values
  # can leak from a parent shell and must not hijack visible launch targeting.
  [[ -n "${VC_FRAME_PANE_ID:-}" ]] && [[ -n "${VC_FRAME_SESSION_NAME:-}" ]]
}

# Attached-client evidence for a live session, from the engine itself. Prints:
#   clients  -- at least one client is attached (the session is on a screen);
#   none     -- the server is up but nobody is attached (a background session);
#   unknown  -- the engine could not be asked, or answered in an unknown shape.
# `action list-clients` prints a CLIENT_ID header and one row per attached
# client (vc-frame 0.47.3, verified in an isolated sandbox: zero rows for a
# session created with --create-background, one row once a pty client attaches,
# zero again after it leaves). The query is addressed explicitly and runs with
# THIS process's attachment context cleared, so the engine can never mistake it
# for a nested client of the session it is asked about.
_vetcoders_vc_frame_session_client_state() {
  local session_name="${1:-}"
  [[ -n "$session_name" ]] || {
    printf 'unknown\n'
    return 0
  }
  local vc_frame_bin=""
  vc_frame_bin="$(_vetcoders_vc_frame_bin 2>/dev/null)" || {
    printf 'unknown\n'
    return 0
  }
  local listing="" query_status=0
  listing="$(env -u VC_FRAME -u VC_FRAME_PANE_ID -u VC_FRAME_SESSION_NAME \
    -u ZELLIJ -u ZELLIJ_PANE_ID -u ZELLIJ_SESSION_NAME \
    "$vc_frame_bin" --session "$session_name" action list-clients 2>/dev/null)" || query_status=$?
  if ((query_status != 0)); then
    printf 'unknown\n'
    return 0
  fi
  local header_seen=0 rows=0 line=""
  while IFS= read -r line; do
    line="$(printf '%s' "$line" | _vetcoders_strip_ansi)"
    [[ -n "${line// /}" ]] || continue
    if ((header_seen == 0)); then
      [[ "$line" == CLIENT_ID* ]] || continue
      header_seen=1
      continue
    fi
    rows=$((rows + 1))
  done <<<"$listing"
  if ((header_seen == 0)); then
    printf 'unknown\n'
  elif ((rows > 0)); then
    printf 'clients\n'
  else
    printf 'none\n'
  fi
}

# Whether a named session is a surface an operator can SEE right now. Prints:
#   usable     -- live, with an attached client (or a live session on an
#                 engine that cannot report clients);
#   unattended -- live, but no client is attached;
#   dead       -- an EXITED incarnation (recovery evidence, never a target);
#   missing    -- no such session;
#   unknown    -- no engine to ask (callers keep their pre-engine behaviour).
_vetcoders_vc_frame_surface_state() {
  local session_name="${1:-}"
  [[ -n "$session_name" ]] || {
    printf 'missing\n'
    return 0
  }
  _vetcoders_vc_frame_bin >/dev/null 2>&1 || {
    printf 'unknown\n'
    return 0
  }
  local state=""
  state="$(_vetcoders_vc_frame_session_state "$session_name")"
  case "$state" in
    live) ;;
    dead | missing)
      printf '%s\n' "$state"
      return 0
      ;;
    *)
      printf 'unknown\n'
      return 0
      ;;
  esac
  case "$(_vetcoders_vc_frame_session_client_state "$session_name")" in
    none) printf 'unattended\n' ;;
    *) printf 'usable\n' ;;
  esac
}

# True when THIS process's attached-frame markers name a surface someone is
# looking at. A bare `_vetcoders_in_vc_frame` is the environment's claim; this
# is the engine's confirmation of it. `unknown` (no engine to ask) keeps the
# claim, so a plain sourced checkout behaves as before.
_vetcoders_has_usable_vc_frame_surface() {
  _vetcoders_in_vc_frame || return 1
  local session_name=""
  session_name="$(_vetcoders_current_vc_frame_session_name)"
  [[ -n "$session_name" ]] || return 1
  case "$(_vetcoders_vc_frame_surface_state "$session_name")" in
    usable | unknown) return 0 ;;
  esac
  return 1
}

# Live (non-EXITED) vc-frame session names. One name per line. Multi-word hosts
# keep spaces (e.g. "vibecrafted workers"); status tags are stripped.
_vetcoders_list_live_vc_frame_sessions() {
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  local vc_frame_bin=""
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 0
  local listing=""
  listing="$("$vc_frame_bin" list-sessions 2>/dev/null || true)"
  [[ -n "$listing" ]] || listing="$("$vc_frame_bin" ls 2>/dev/null || true)"
  printf '%s\n' "$listing" \
    | _vetcoders_strip_ansi \
    | awk '
        NF == 0 { next }
        /EXITED/ { next }
        {
          line = $0
          sub(/[[:space:]]+\[.*$/, "", line)
          sub(/[[:space:]]+\([^)]*\)$/, "", line)
          gsub(/[[:space:]]+$/, "", line)
          if (line != "") print line
        }
      '
}

# Typed owner for interactive surface targeting (init / bare resume / operator).
# Policy (order is the contract — not provider-specific):
#   1. this project's canonical workspace-bound session, when that session is
#      live — the binding comes from the same owner vc-start prepares through
#   2. otherwise empty — the caller prepares THIS project's own target
#
# What is deliberately NOT here (2026-09-07): a live session whose name merely
# equals this repository's basename. A name is not a binding; see the resolver
# body. What is deliberately NOT here (2026-09-06): a session listed as
# `(attached)`/`(current)` by vc-frame, and a lone live session. Neither proves
# the current caller owns it. A `(attached)` marker means SOME client is
# attached — routinely another operator window on another repository — and a
# global count is not ownership at all. Adopting either let one unrelated live
# session capture this project's resume and dispatch the provider into it.
# A verified attachment of THIS caller is VC_FRAME_SESSION_NAME, which
# _vetcoders_prepare_operator_runtime honours (via _vetcoders_in_vc_frame)
# before this resolver ever runs; an explicit VIBECRAFTED_OPERATOR_SESSION is
# honoured there too.
#
# Unmatched live sessions are reported on stderr as context, never as a claim.
_vetcoders_resolve_interactive_operator_target() {
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  _vetcoders_vc_frame_bin >/dev/null 2>&1 || return 0

  # Portable live list (bash + zsh): newline-separated names, no shell arrays.
  local live_list="" live_count=0 name=""
  live_list="$(_vetcoders_list_live_vc_frame_sessions)"
  while IFS= read -r name; do
    [[ -n "$name" ]] || continue
    ((live_count += 1))
  done <<< "$live_list"

  local repo_root="" host="" bound=""
  repo_root="$(_vetcoders_effective_project_root)"
  host="$(basename "$repo_root")"

  # The binding comes from the ONE canonical workspace owner — the same one
  # vc-start prepares through. Anything already resolved (product entry choke
  # or an explicit operator override) is honoured as-is; otherwise ask that
  # owner here.
  bound="${VIBECRAFTED_OPERATOR_SESSION:-}"
  if [[ -z "$bound" ]] \
    && command -v _vetcoders_ensure_canonical_workspace_identity >/dev/null 2>&1; then
    if _vetcoders_ensure_canonical_workspace_identity >/dev/null 2>&1; then
      bound="${VIBECRAFTED_OPERATOR_SESSION:-}"
    fi
  fi
  if [[ -n "$bound" ]] && printf '%s\n' "$live_list" | grep -Fxq -- "$bound"; then
    printf '%s\n' "$bound"
    return 0
  fi

  # Deliberately absent (2026-09-07): adopting a live session because its name
  # equals this repository's basename. That is a coincidence of naming, not a
  # workspace binding — the catalogue binds THIS root to a workspace id, and a
  # same-basename checkout elsewhere produces an identically named session that
  # owns nothing here. The old basename branch is exactly how bare resume
  # captured a session vc-start had never bound.

  if ((live_count > 0)); then
    # Informational, never fatal: live sessions elsewhere are not a claim on
    # THIS project. The caller proceeds to prepare the project's own target.
    printf 'No project-bound operator target for %s; %d unrelated live vc-frame session(s):\n' \
      "$host" "$live_count" >&2
    while IFS= read -r name; do
      [[ -n "$name" ]] || continue
      printf '    - %s\n' "$name" >&2
    done <<< "$live_list"
    printf '  preparing this project'"'"'s own session; export VIBECRAFTED_OPERATOR_SESSION=<name> to target one of the above.\n' >&2
  fi
  return 0
}

# Back-compat alias — same typed owner as above.
_vetcoders_guess_active_vc_frame_session() {
  _vetcoders_resolve_interactive_operator_target
}

_vetcoders_current_vc_frame_session_name() {
  printf '%s\n' "${VC_FRAME_SESSION_NAME:-${ZELLIJ_SESSION_NAME:-}}"
}

_vetcoders_atuin_bin() {
  local override="${VIBECRAFTED_ATUIN_BIN:-}"
  if [[ -n "$override" && -x "$override" ]]; then
    printf '%s\n' "$override"
    return 0
  fi

  if [[ -n "${_VETCODERS_ATUIN_BIN:-}" && -x "${_VETCODERS_ATUIN_BIN}" ]]; then
    printf '%s\n' "${_VETCODERS_ATUIN_BIN}"
    return 0
  fi

  command -v atuin 2>/dev/null || return 1
}

_vetcoders_strip_ansi() {
  local python_bin=""
  python_bin="$(_vetcoders_internal_python)"
  "$python_bin" -c 'import re, sys; print(re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", sys.stdin.read()), end="")'
}

_vetcoders_vc_frame_session_state() {
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  local session_name="$1"
  local listing
  local vc_frame_bin=""

  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || {
    printf 'missing\n'
    return 0
  }

  listing="$("$vc_frame_bin" ls 2>/dev/null | _vetcoders_strip_ansi || true)"
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    case "$line" in
      "$session_name "*)
        if [[ "$line" == *"(EXITED"* ]]; then
          printf 'dead\n'
        else
          printf 'live\n'
        fi
        return 0
        ;;
    esac
  done <<< "$listing"

  printf 'missing\n'
}

_vetcoders_vc_frame_socket_dir() {
  if [[ -n "${VC_FRAME_SOCKET_DIR:-}" ]]; then
    printf '%s\n' "$VC_FRAME_SOCKET_DIR"
  elif [[ -n "${ZELLIJ_SOCKET_DIR:-}" ]]; then
    printf '%s\n' "$ZELLIJ_SOCKET_DIR"
  elif [[ "$(uname -s 2>/dev/null || true)" == "Darwin" ]]; then
    # macOS sockaddr_un is 104 bytes. TMPDIR is /var/folders/.../T (~50)
    # plus /vc-frame-$UID/contract_version_N already exhausts the budget
    # before a workspace-bound session name is appended.
    printf '/tmp/vc-frame-%s\n' "$(id -u)"
  fi
}

_vetcoders_record_vc_frame_attachment() {
  local state="$1"
  local runtime_session_id="$2"
  local replaces_runtime_session_id="${3:-}"
  local socket_dir="${4:-}"

  [[ -n "${VIBECRAFTED_WORKSPACE_ID:-}" ]] || return 0
  [[ -n "${VIBECRAFTED_SESSION_ID:-}" ]] || return 0
  [[ -n "${VIBECRAFTED_WORKSPACE_INSTANCE_ID:-}" ]] || return 0

  [[ -n "$socket_dir" ]] || socket_dir="$(_vetcoders_vc_frame_socket_dir)"
  local args=(
    workspace session-attach
    --workspace-id "$VIBECRAFTED_WORKSPACE_ID"
    --session-id "$VIBECRAFTED_SESSION_ID"
    --instance-id "$VIBECRAFTED_WORKSPACE_INSTANCE_ID"
    --runtime vc-frame
    --runtime-session-id "$runtime_session_id"
    --state "$state"
    --socket-dir "$socket_dir"
  )
  if [[ -n "$replaces_runtime_session_id" ]]; then
    args+=(--replaces-runtime-session-id "$replaces_runtime_session_id")
  fi
  local attach_status=0
  if declare -F _vetcoders_product_core_cli >/dev/null 2>&1; then
    _vetcoders_product_core_cli "${args[@]}" >/dev/null || attach_status=$?
  elif command -v vibecrafted >/dev/null 2>&1; then
    vibecrafted "${args[@]}" >/dev/null || attach_status=$?
  else
    return 0
  fi
  if [[ "$attach_status" -ne 0 ]]; then
    printf "vc-start: could not attach vc-frame session '%s' to WES " \
      "$runtime_session_id" >&2
    printf "(workspace=%s instance=%s session=%s, status=%s).\n" \
      "$VIBECRAFTED_WORKSPACE_ID" \
      "$VIBECRAFTED_WORKSPACE_INSTANCE_ID" \
      "$VIBECRAFTED_SESSION_ID" \
      "$attach_status" >&2
    printf "vc-start: re-run vc-start from the intended workspace root; " >&2
    printf "if the mismatch persists, inspect 'vibecrafted workspace list'.\n" >&2
    return "$attach_status"
  fi
}

_vetcoders_import_legacy_vc_frame_sessions() {
  local legacy_socket_dir="${VIBECRAFTED_LEGACY_VC_FRAME_SOCKET_DIR:-}"
  local current_socket_dir=""
  local vc_frame_bin=""
  local listing=""
  local line candidate session_name state seen

  [[ -n "$legacy_socket_dir" ]] || return 0
  current_socket_dir="$(_vetcoders_vc_frame_socket_dir)"
  [[ "$legacy_socket_dir" != "$current_socket_dir" ]] || return 0
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 0
  listing="$(
    VC_FRAME_SOCKET_DIR="$legacy_socket_dir" \
      ZELLIJ_SOCKET_DIR="$legacy_socket_dir" \
      "$vc_frame_bin" ls 2>/dev/null | _vetcoders_strip_ansi || true
  )"
  seen=$'\n'
  while IFS= read -r line; do
    [[ -n "$line" ]] || continue
    session_name="${line%% *}"
    [[ -n "$session_name" ]] || continue
    case "$seen" in
      *$'\n'"$session_name"$'\n'*) continue ;;
    esac
    seen="${seen}${session_name}"$'\n'
    state="dead"
    while IFS= read -r candidate; do
      [[ "$candidate" == "$session_name "* ]] || continue
      if [[ "$candidate" != *"(EXITED"* ]]; then
        state="live"
        break
      fi
    done <<< "$listing"
    _vetcoders_record_vc_frame_attachment \
      "$state" "$session_name" "" "$legacy_socket_dir" || return $?
  done <<< "$listing"
}

_vetcoders_operator_layout_file() {
  _vetcoders_frontier_file "vc-frame/layouts/operator.kdl"
}

_vetcoders_operator_session_name() {
  _vetcoders_normalize_ambient_context
  _vetcoders_operator_place_session_name
}

# G7 twin of spawn_effective_operator_session (scripts/lib/vc_frame.sh).
# Worker host session: override → workspace-bound catalog host → basename
# fallback. Cut A (2026-08-10): basename-only hosts collide across checkouts
# with the same name; catalog workspace_id is the durable ownership key.
# 2026-08-17: suffix is `-w` (no spaces). The older `{label}-{short} workers`
# form overflowed macOS sockaddr_un on the default TMPDIR socket root.
# The bare basename remains the human operator's interactive card and never
# hosts a worker tab.
_vetcoders_effective_worker_session() {
  if [[ -n "${VIBECRAFTED_WORKER_SESSION:-}" ]]; then
    printf '%s\n' "${VIBECRAFTED_WORKER_SESSION}"
    return 0
  fi
  local root_dir=""
  root_dir="$(_vetcoders_effective_project_root)"

  local resolved=""
  local python_bin=""
  python_bin="$(_vetcoders_internal_python)"
  if command -v "$python_bin" >/dev/null 2>&1; then
    resolved="$(
      SPAWN_ROOT="$root_dir" VIBECRAFTED_ROOT="$root_dir" "$python_bin" - <<'PY' 2>/dev/null
import os
from pathlib import Path
root = os.environ.get("SPAWN_ROOT") or os.environ.get("VIBECRAFTED_ROOT") or os.getcwd()
try:
    from vibecrafted_core.workspace_catalog import resolve_worker_host_session
    print(resolve_worker_host_session(root=root, env=os.environ), end="")
except Exception:
    print(f"{Path(root).name or 'vibecrafted'}-w", end="")
PY
    )" || resolved=""
  fi
  if [[ -n "$resolved" ]]; then
    printf '%s\n' "$resolved"
    return 0
  fi

  local host=""
  host="$(basename "$root_dir")"
  [[ -n "$host" ]] || return 1
  printf '%s-w\n' "$host"
}

_vetcoders_vc_frame_gc_script() {
  _vetcoders_workflow_script "vc-operator" "mission-control/vc-frame-gc.sh"
}

_vetcoders_wait_for_vc_frame_session() {
  local session_name="$1"
  local attempts="${2:-40}"
  local current=0

  # Sleep first: a server socket never appears in the same instant the client
  # is spawned, and a probe fired at t=0 races the client's own exit (a client
  # that returns immediately must not be shadowed by a still-running `ls`).
  while (( current < attempts )); do
    sleep 0.25
    [[ "$(_vetcoders_vc_frame_session_state "$session_name")" == "live" ]] && return 0
    ((current+=1))
  done

  return 1
}

_vetcoders_place_label_from_session_name() {
  # Strip old hashed recovery tails (`-rHHMMSS-PID`) and numeric incarnations
  # (`-2`). The SESSIONS rail is a place name, not a socket token.
  local name="${1:-workspace}"
  if [[ "$name" =~ ^(.*)-r[0-9]{6}-[0-9]+$ ]]; then
    name="${BASH_REMATCH[1]}"
  fi
  if [[ "$name" =~ ^(.*)-([0-9]{1,2})$ ]]; then
    name="${BASH_REMATCH[1]}"
  fi
  [[ -n "$name" ]] || name="workspace"
  printf '%s\n' "$name"
}

_vetcoders_next_free_place_session() {
  local place="${1:-workspace}"
  local max_len=24
  local n=2
  local suffix stem candidate budget
  while (( n <= 99 )); do
    suffix="-${n}"
    budget=$((max_len - ${#suffix}))
    (( budget < 1 )) && budget=1
    stem="$place"
    if (( ${#stem} > budget )); then
      stem="${stem:0:budget}"
      stem="${stem%-}"
      [[ -n "$stem" ]] || stem="ws"
    fi
    candidate="${stem}${suffix}"
    if [[ "$(_vetcoders_vc_frame_session_state "$candidate")" == "missing" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
    n=$((n + 1))
  done
  printf '%s\n' "${place:0:21}-x"
}

_vetcoders_recovery_vc_frame_session_name() {
  local original="${1:-}"
  local place=""
  # Catalog place wins: hashed dead names like `3m-4ad4-r034605-2072` are not
  # a workspace identity. Fall back to stripping the dead name.
  place="$(_vetcoders_operator_place_session_name 2>/dev/null || true)"
  if [[ -z "$place" ]]; then
    place="$(_vetcoders_place_label_from_session_name "${original:-workspace}")"
  fi
  _vetcoders_next_free_place_session "$place"
}

_vetcoders_run_new_vc_frame_session() {
  local vc_frame_bin="$1"
  local session_name="$2"
  local layout_file="$3"
  local replaces_runtime_session_id="${4:-}"
  shift 4

  # A foreground vc-frame client does not return until the operator detaches.
  # Persist the intended physical incarnation first, then promote it to live as
  # soon as the server socket appears. Otherwise the old ordering leaves WES
  # unaware of the active session for the entire lifetime of the app window.
  _vetcoders_record_vc_frame_attachment \
    missing "$session_name" "$replaces_runtime_session_id" || return $?

  (
    _vetcoders_wait_for_vc_frame_session "$session_name" &&
      _vetcoders_record_vc_frame_attachment \
        live "$session_name" "$replaces_runtime_session_id"
  ) &
  local attachment_recorder_pid=$!

  "$vc_frame_bin" "$@" \
    --session "$session_name" --new-session-with-layout "$layout_file"
  local frame_rc=$?

  # The frame client's exit status is the launcher's exit status. The recorder
  # only annotates WES: once the foreground client has returned, the session
  # either went live while it ran (the recorder already finished) or never
  # came up — polling `ls` for another ten seconds after the client is gone
  # buys nothing and must not turn a clean client exit into 1.
  kill "$attachment_recorder_pid" 2>/dev/null || true
  wait "$attachment_recorder_pid" 2>/dev/null || true
  return "$frame_rc"
}

# Create the operator's session WITHOUT handing the caller's terminal to a
# foreground client, and without needing a terminal at all: Frame's own
# detached-create is a finite call that starts the server and returns.
#
# Why this exists (2026-09-06): a foreground vc-frame client does not return
# until the Founder detaches, so any work sequenced after preparation — the
# provider tab above all — happened only once the window had been CLOSED.
# Splitting create from attach lets the caller finish preparing the session and
# then, as its last act, hand over the terminal.
_vetcoders_create_vc_frame_session_detached() {
  local vc_frame_bin="$1"
  local session_name="$2"
  local layout_file="$3"
  [[ -n "$vc_frame_bin" && -n "$session_name" ]] || return 1
  [[ -n "$layout_file" ]] || {
    printf 'Layout file missing; cannot prepare session %s.\n' "$session_name" >&2
    return 1
  }

  _vetcoders_record_vc_frame_attachment missing "$session_name" || return $?

  # `attach --create-background` is the ONLY create that works without a
  # terminal. Verified against Frame source 6ec4a6a5: src/main.rs:359 moves
  # --new-session-with-layout into the layout field while KEEPING the Attach
  # command, src/commands.rs:792 turns --create-background into
  # should_create_detached, and zellij-client/src/lib.rs:783 runs
  # start_server_detached — returning BEFORE the interactive TTY guard at :792.
  # The ClientInfo::New branch spawns the server with that layout and returns
  # on its own, so there is no client to reap here.
  #
  # Backgrounding an interactive client instead is what this replaces: a
  # non-interactive shell hands an async command /dev/null on stdin, so Frame
  # refuses at the TTY guard, the redirect swallows the message, and the
  # readiness loop can only time out.
  local create_output="" create_rc=0
  create_output="$(env -u VC_FRAME -u VC_FRAME_PANE_ID -u VC_FRAME_SESSION_NAME \
    -u ZELLIJ -u ZELLIJ_PANE_ID -u ZELLIJ_SESSION_NAME \
    "$vc_frame_bin" --new-session-with-layout "$layout_file" \
    attach --create-background "$session_name" 2>&1)" || create_rc=$?

  # One recoverable outcome: another caller won the race and the exact session
  # is already there. Frame reports that as exit 1 + "Session already exists"
  # (zellij-client/src/lib.rs, ClientInfo::Attach arm of start_server_detached),
  # never as an idempotent exit 0 — so the readiness probe below is what
  # settles it. Anything else is a real refusal and must not be waited out.
  if ((create_rc != 0)) &&
    ! _vetcoders_vc_frame_stderr_is_session_already_exists "$create_output"; then
    printf 'vc-frame refused to create the session %s (exit %s).\n' \
      "$session_name" "$create_rc" >&2
    [[ -z "$create_output" ]] || printf '%s\n' "$create_output" >&2
    return 1
  fi

  if ! _vetcoders_wait_for_vc_frame_session "$session_name" 40; then
    printf 'vc-frame session did not come up: %s\n' "$session_name" >&2
    [[ -z "$create_output" ]] || printf '%s\n' "$create_output" >&2
    return 1
  fi

  _vetcoders_record_vc_frame_attachment live "$session_name" || return $?
  export VIBECRAFTED_PREPARED_VC_FRAME_SESSION="$session_name"
}

# Record the intent to hand this terminal over, once everything else is ready.
# Only for a caller that HAS a terminal to give and is not already inside a
# frame: either we just created the session, or this very window was opened by
# _vetcoders_open_entry_in_vc_terminal so the operator could land in it.
_vetcoders_mark_pending_vc_frame_attach() {
  local session_name="${1:-}"
  local created="${2:-0}"
  [[ -n "$session_name" ]] || return 0
  [[ -t 0 && -t 1 ]] || return 0
  ! _vetcoders_in_vc_frame || return 0
  if ((created)) || [[ "${VIBECRAFTED_TERMINAL_ENTRY:-}" == "1" ]]; then
    export VIBECRAFTED_PENDING_VC_FRAME_ATTACH="$session_name"
  fi
  return 0
}

# The foreground handover. By contract this runs LAST — after the provider tab
# exists — and blocks until the Founder detaches. A caller that never prepared
# an attach is a no-op, so ordinary dispatch paths are untouched.
#
# _vetcoders_prepare_operator_runtime exports VC_FRAME_SESSION_NAME (and, on
# the guessed/explicit paths, ZELLIJ_SESSION_NAME too) into THIS shell purely
# for downstream dispatch targeting — so the AICX pack and provider tab land
# on the right project. Those markers are not proof that this process is
# itself already attached. The native binary disagrees: its own startup
# aliases VC_FRAME_SESSION_NAME into ZELLIJ_SESSION_NAME (zellij-utils
# envs::normalize_vc_frame_env_aliases), and src/commands.rs:844 panics
# ("You are trying to attach to the current session") whenever that value
# equals the attach target. A brand-new external client inheriting our own
# targeting marker looks, to the native guard, exactly like an illegal nested
# reattach. Clear both for ONLY this invocation — same contract the detached
# create path above already uses (env -u ... attach --create-background) —
# so the parent shell's targeting state and the explicit session argument are
# untouched, only the child's inherited attachment context is.
_vetcoders_attach_prepared_vc_frame_session() {
  # Declared-workspace entry from a LIVE attached client elsewhere: move that
  # client onto the prepared session (Frame's own switch-session, the same
  # shared-canvas move _vetcoders_ensure_vc_frame_session makes inside a frame)
  # instead of nesting a second foreground client, which Frame refuses. The
  # spec is "<ambient session>\t<target>" recorded by
  # _vetcoders_prepare_declared_workspace_target BEFORE this shell exported the
  # target as its own VC_FRAME_SESSION_NAME; the action is therefore addressed
  # to the ambient server explicitly, where our client actually is.
  if [[ -n "${VIBECRAFTED_PENDING_VC_FRAME_SWITCH:-}" ]]; then
    local switch_spec="$VIBECRAFTED_PENDING_VC_FRAME_SWITCH"
    unset VIBECRAFTED_PENDING_VC_FRAME_SWITCH
    local switch_from="${switch_spec%%$'\t'*}"
    local switch_to="${switch_spec#*$'\t'}"
    [[ -n "$switch_from" && -n "$switch_to" ]] || return 0
    local switch_bin=""
    switch_bin="$(_vetcoders_vc_frame_bin)" || return 1
    _vetcoders_record_vc_frame_attachment live "$switch_to" || true
    VC_FRAME_SESSION_NAME="$switch_from" ZELLIJ_SESSION_NAME="$switch_from" \
      "$switch_bin" --session "$switch_from" action switch-session "$switch_to" || {
      local switch_rc=$?
      printf 'resume: could not move the attached client from %s to %s (exit %s); the workspace and its provider tab exist: vc-frame attach %s\n' \
        "$switch_from" "$switch_to" "$switch_rc" "$switch_to" >&2
      return "$switch_rc"
    }
    return 0
  fi
  local session_name="${1:-${VIBECRAFTED_PENDING_VC_FRAME_ATTACH:-}}"
  [[ -n "$session_name" ]] || return 0
  unset VIBECRAFTED_PENDING_VC_FRAME_ATTACH
  local vc_frame_bin=""
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 1
  _vetcoders_record_vc_frame_attachment live "$session_name" || true
  env -u VC_FRAME -u VC_FRAME_PANE_ID -u VC_FRAME_SESSION_NAME \
    -u ZELLIJ -u ZELLIJ_PANE_ID -u ZELLIJ_SESSION_NAME \
    "$vc_frame_bin" attach "$session_name"
}

_vetcoders_ensure_vc_frame_session() {
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  local session_name="$1"
  local layout_file="$2"
  local vc_frame_bin=""
  shift 2

  _vetcoders_require_vc_frame || return 1
  _vetcoders_pin_vc_frame_config_dir || return $?
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 1

  local inside_vc_frame=0
  # Trusted attached-context signal only (_vetcoders_in_vc_frame): stale
  # VC_FRAME/ZELLIJ leaks in a parent shell must not reroute the launch into
  # background-create + switch-session aimed at a session with no live client.
  # The spawn-side twin (spawn_in_vc_frame_context, scripts/lib/vc_frame.sh)
  # is a separate dispatch surface with its own contract; not changed here.
  _vetcoders_in_vc_frame && inside_vc_frame=1

  local current_session="${VC_FRAME_SESSION_NAME:-${ZELLIJ_SESSION_NAME:-}}"

  # Already in the target session — nothing to do.
  if (( inside_vc_frame )) && [[ "$current_session" == "$session_name" ]]; then
    return 0
  fi

  unset VIBECRAFTED_PREPARED_VC_FRAME_SESSION

  case "$(_vetcoders_vc_frame_session_state "$session_name")" in
    live)
      if (( inside_vc_frame )); then
        "$vc_frame_bin" action switch-session "$session_name" || return $?
      else
        # The foreground attach blocks until the client detaches. WES must know
        # about the live physical session before handing control to the client.
        _vetcoders_record_vc_frame_attachment live "$session_name" || return $?
        "$vc_frame_bin" "$@" attach "$session_name" || return $?
      fi
      if (( inside_vc_frame )); then
        _vetcoders_record_vc_frame_attachment live "$session_name" || true
      fi
      export VIBECRAFTED_PREPARED_VC_FRAME_SESSION="$session_name"
      ;;
    dead)
      # Dead (EXITED) sessions are recovery evidence. Never kill and recreate
      # the same name during launch: that destroys the operator's last scrollback
      # exactly when a dirty shutdown needs preservation most.
      local dead_session_name="$session_name"
      # Preserve the old physical incarnation in WES before opening a new one.
      # If durable attachment fails, do not silently split runtime from truth.
      _vetcoders_record_vc_frame_attachment dead "$dead_session_name" || return $?
      session_name="$(_vetcoders_recovery_vc_frame_session_name "$dead_session_name")"
      printf "Session '%s' is dead; preserving it and creating '%s'.\n" \
        "$dead_session_name" "$session_name" >&2
      if [[ -n "$layout_file" ]]; then
        if (( inside_vc_frame )); then
          env -u VC_FRAME -u VC_FRAME_PANE_ID -u VC_FRAME_SESSION_NAME \
            -u ZELLIJ -u ZELLIJ_PANE_ID -u ZELLIJ_SESSION_NAME \
            "$vc_frame_bin" --session "$session_name" --new-session-with-layout "$layout_file" &
          local bg_pid_dead=$!
          local wait_dead=0
          while (( wait_dead < 20 )); do
            [[ "$(_vetcoders_vc_frame_session_state "$session_name")" == "live" ]] && break
            sleep 0.25
            ((wait_dead+=1))
          done
          if [[ "$(_vetcoders_vc_frame_session_state "$session_name")" != "live" ]]; then
            kill "$bg_pid_dead" 2>/dev/null || true
            wait "$bg_pid_dead" 2>/dev/null || true
            return 1
          fi
          kill "$bg_pid_dead" 2>/dev/null || true
          wait "$bg_pid_dead" 2>/dev/null || true
          "$vc_frame_bin" action switch-session "$session_name" || return $?
        else
          _vetcoders_run_new_vc_frame_session \
            "$vc_frame_bin" "$session_name" "$layout_file" "$dead_session_name" \
            "$@" || return $?
        fi
        if (( inside_vc_frame )); then
          _vetcoders_record_vc_frame_attachment live "$session_name" "$dead_session_name" || true
        fi
        export VIBECRAFTED_PREPARED_VC_FRAME_SESSION="$session_name"
      else
        echo "Session '$dead_session_name' is dead and no layout is available for a new recovery session." >&2
        return 1
      fi
      ;;
    *)
      if [[ -n "$layout_file" ]]; then
        if (( inside_vc_frame )); then
          # Create the session in the background with vc-frame env stripped to
          # prevent nested-client panic, then switch to it.
          env -u VC_FRAME -u VC_FRAME_PANE_ID -u VC_FRAME_SESSION_NAME \
            -u ZELLIJ -u ZELLIJ_PANE_ID -u ZELLIJ_SESSION_NAME \
            "$vc_frame_bin" --session "$session_name" --new-session-with-layout "$layout_file" &
          local bg_pid=$!
          # Wait briefly for session to appear.
          local wait_i=0
          while (( wait_i < 20 )); do
            [[ "$(_vetcoders_vc_frame_session_state "$session_name")" == "live" ]] && break
            sleep 0.25
            ((wait_i+=1))
          done
          if [[ "$(_vetcoders_vc_frame_session_state "$session_name")" != "live" ]]; then
            kill "$bg_pid" 2>/dev/null || true
            wait "$bg_pid" 2>/dev/null || true
            return 1
          fi
          # Kill the background client now that the session server is alive.
          kill "$bg_pid" 2>/dev/null || true
          wait "$bg_pid" 2>/dev/null || true
          "$vc_frame_bin" action switch-session "$session_name" || return $?
        else
          _vetcoders_run_new_vc_frame_session \
            "$vc_frame_bin" "$session_name" "$layout_file" "" "$@" || return $?
        fi
        if (( inside_vc_frame )); then
          _vetcoders_record_vc_frame_attachment live "$session_name" || true
        fi
        export VIBECRAFTED_PREPARED_VC_FRAME_SESSION="$session_name"
      else
        echo "Layout file missing and session not found." >&2
        return 1
      fi
      ;;
  esac
}

# Declared workspace target (2026-09-09, P0 on the installed 4.3.1 line):
# `vibecrafted resume <tool> --session <native id> --root R` is a DECLARATION
# ("to jest deklaracja"): open R's workspace and resume the agent THERE. Three
# identities are kept apart on purpose:
#   * native agent session  -- the provider id, only ever read into the argv;
#   * workspace identity    -- R's binding in the one canonical catalogue
#                              (_vetcoders_ensure_canonical_workspace_identity),
#                              which names the place session to host the tab;
#   * transport attachment  -- VC_FRAME_PANE_ID/VC_FRAME_SESSION_NAME, i.e.
#                              where THIS process happens to be attached.
# The third is ambient parent context. The generic branch below adopts it
# unconditionally ("already inside a frame -> attach to it"), which is exactly
# how an explicit repo was overridden: a stale `vibecrafted` marker became the
# target, the host was missing, the bounded create inherited that same marker
# and Frame's native guard panicked (src/commands.rs:844). Merely unsetting the
# marker would keep the wrong target; this owner resolves the RIGHT one.
#
# Contract, in order:
#   1. bind R through the canonical owner (creates the workspace when absent;
#      an ambient VIBECRAFTED_OPERATOR_SESSION only survives when it IS R's);
#   2. if the attached client already lives in R's live place session, reuse
#      it -- no create, no second client;
#   3. otherwise R's place session is the target: live -> reuse as is (never a
#      duplicate); dead -> preserved, a recovery incarnation is created;
#      missing -> created detached with the client context cleared;
#   4. decide how the Founder ENTERS it -- settled BEFORE anything is created
#      and executed last by _vetcoders_attach_prepared_vc_frame_session once
#      the provider tab exists: an attached client on a live ambient session
#      that someone is watching -> switch that client; a controlling terminal
#      -> foreground attach with a clean env; neither -> refuse here. A caller
#      with no surface never reaches this owner on the public path (the entry
#      opens the product terminal first); one that does anyway (a broken host,
#      a bypassed entry) gets an actionable failure instead of a session that
#      exists where nobody can see it and a "launched" nobody can act on.
# Other sessions are never killed, renamed or re-targeted. A failed create is
# a failed declaration: nothing is launched anywhere else instead.
_vetcoders_prepare_declared_workspace_target() {
  local declared_root="${1:-}"
  local verb="${2:-resume}"
  [[ -n "$declared_root" && -d "$declared_root" ]] || {
    printf '%s: declared workspace root is not a directory: %s\n' "$verb" "$declared_root" >&2
    return 1
  }
  command -v _vetcoders_ensure_canonical_workspace_identity >/dev/null || {
    printf '%s: the canonical workspace owner is unavailable; cannot bind %s\n' "$verb" "$declared_root" >&2
    return 1
  }

  # Captured BEFORE any targeting export below overwrites the marker: the
  # ambient session is the only handle that can later move the attached
  # client, and whether anyone is attached to it decides whether that move is
  # possible at all. Liveness alone is not that proof (a background session
  # has no client to move).
  local ambient_session="" ambient_surface="missing"
  if _vetcoders_in_vc_frame; then
    ambient_session="$(_vetcoders_current_vc_frame_session_name)"
    [[ -z "$ambient_session" ]] || ambient_surface="$(_vetcoders_vc_frame_surface_state "$ambient_session")"
  fi
  local ambient_operator="${VIBECRAFTED_OPERATOR_SESSION:-}"
  unset VIBECRAFTED_PENDING_VC_FRAME_ATTACH VIBECRAFTED_PENDING_VC_FRAME_SWITCH

  _vetcoders_ensure_canonical_workspace_identity "$declared_root" || return $?
  local place="${VIBECRAFTED_OPERATOR_SESSION:-}"
  if _vetcoders_is_legacy_operator_session_name "$place"; then
    place="$(_vetcoders_operator_session_name)"
  fi
  [[ -n "$place" ]] || {
    printf '%s: the workspace owner bound %s to no place session; refusing to guess one.\n' "$verb" "$declared_root" >&2
    return 1
  }
  if [[ -n "$ambient_operator" && "$ambient_operator" != "$place" ]]; then
    printf '%s: VIBECRAFTED_OPERATOR_SESSION=%s is ambient context, not the declared workspace; using %s for %s\n' \
      "$verb" "$ambient_operator" "$place" "$declared_root" >&2
  fi
  export VIBECRAFTED_DECLARED_WORKSPACE_ROOT="$declared_root"

  local state=""
  state="$(_vetcoders_vc_frame_session_state "$place")"

  if [[ -n "$ambient_session" && "$ambient_session" == "$place" && "$state" == live ]]; then
    # The attached client already lives in the declared workspace.
    export VIBECRAFTED_OPERATOR_SESSION="$place"
    export VC_FRAME_SESSION_NAME="$place"
    export ZELLIJ_SESSION_NAME="$place"
    return 0
  fi
  if [[ -n "$ambient_session" ]]; then
    printf '%s: attached vc-frame session %s (%s) is ambient context, not the declared workspace %s for %s\n' \
      "$verb" "$ambient_session" "$ambient_surface" "$place" "$declared_root" >&2
  fi

  # The entry decision comes first. A workspace nobody can be shown must not
  # be created on this caller's behalf.
  local entry_mode=""
  if [[ -n "$ambient_session" && "$ambient_surface" == usable ]]; then
    entry_mode="switch"
  elif [[ -t 0 && -t 1 ]] || [[ -n "${VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME:-}" ]]; then
    # The same suite-only bypass the generic create branch honours below:
    # the child of a terminal hand-off always has a TTY for real, and the
    # tests drive that child without allocating one.
    entry_mode="attach"
  else
    printf '%s: no attached vc-frame client and no controlling terminal here; the workspace %s for %s cannot be entered from this process.\n' \
      "$verb" "$place" "$declared_root" >&2
    printf '  run the %s from a terminal, or let the public entry open one (it does so by itself when no surface is available); nothing was created or launched.\n' \
      "$verb" >&2
    return 1
  fi

  local vc_frame_bin=""
  _vetcoders_require_vc_frame || return 1
  _vetcoders_pin_vc_frame_config_dir || return $?
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 1

  if [[ "$state" != live ]]; then
    local layout_file=""
    layout_file="$(_vetcoders_operator_layout_file 2>/dev/null || true)"
    [[ -n "$layout_file" ]] || {
      printf '%s: no operator layout is available; cannot create the workspace session %s for %s.\n' \
        "$verb" "$place" "$declared_root" >&2
      return 1
    }
    if [[ "$state" == dead ]]; then
      # Dead incarnations are recovery evidence; never recreate the same name.
      local dead_place="$place"
      _vetcoders_record_vc_frame_attachment dead "$dead_place" || return $?
      place="$(_vetcoders_recovery_vc_frame_session_name "$dead_place")"
      printf "Session '%s' is dead; preserving it and creating '%s'.\n" \
        "$dead_place" "$place" >&2
    fi
    if ! _vetcoders_create_vc_frame_session_detached "$vc_frame_bin" "$place" "$layout_file"; then
      printf '%s: could not create the workspace session %s for %s; nothing was launched elsewhere.\n' \
        "$verb" "$place" "$declared_root" >&2
      return 1
    fi
    place="${VIBECRAFTED_PREPARED_VC_FRAME_SESSION:-$place}"
  fi

  export VIBECRAFTED_OPERATOR_SESSION="$place"
  export VC_FRAME_SESSION_NAME="$place"
  export ZELLIJ_SESSION_NAME="$place"
  if [[ "$entry_mode" == switch ]]; then
    export VIBECRAFTED_PENDING_VC_FRAME_SWITCH="${ambient_session}"$'\t'"${place}"
  else
    export VIBECRAFTED_PENDING_VC_FRAME_ATTACH="$place"
  fi
  return 0
}

_vetcoders_prepare_operator_runtime() {
  vc_raise_launcher_limits
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  local runtime="${1:-$(_vetcoders_default_runtime)}"
  # defer-attach: the caller will create the provider tab itself and then call
  # _vetcoders_attach_prepared_vc_frame_session. Opt-in, so the six existing
  # callers keep their exact foreground behaviour.
  local defer_attach="${2:-}"
  # declared root: an explicit, already-normalized `--root/--repo` from a
  # public entry. Opt-in as well; only the declared-workspace owner above
  # treats the attached frame as ambient context.
  local declared_root="${3:-}"
  # verb label for messages: resume (default), init, operator, partner, fork.
  local verb="${4:-resume}"
  local session_name layout_file
  _vetcoders_normalize_ambient_context
  unset VIBECRAFTED_PENDING_VC_FRAME_ATTACH

  case "$runtime" in
    terminal|visible) ;;
    *) return 0 ;;
  esac

  if [[ -n "$declared_root" ]]; then
    _vetcoders_prepare_declared_workspace_target "$declared_root" "$verb"
    return $?
  fi

  # If we are already inside a vc-frame session, naturally attach to it --
  # when the engine confirms that session as a surface (live, attached client)
  # or cannot be asked. A marker whose session is dead, missing or unattended
  # is inherited context from a pane that is gone or a window nobody watches:
  # adopting it is how `init codex` from an agent shell hung its tab on a
  # freshly created background `vibecrafted` host the Founder never saw. Say
  # so, drop it for this launch only, and resolve the project's own target.
  if _vetcoders_in_vc_frame; then
    if _vetcoders_has_usable_vc_frame_surface; then
      VIBECRAFTED_OPERATOR_SESSION="$(_vetcoders_current_vc_frame_session_name)"
      export VIBECRAFTED_OPERATOR_SESSION
      export VC_FRAME_SESSION_NAME="$VIBECRAFTED_OPERATOR_SESSION"
      export ZELLIJ_SESSION_NAME="$VIBECRAFTED_OPERATOR_SESSION"
      return 0
    fi
    local stale_marker=""
    stale_marker="$(_vetcoders_current_vc_frame_session_name)"
    printf '%s: attached vc-frame marker %s (%s) is ambient context, not a visible surface; preparing this project'"'"'s own target.\n' \
      "$verb" "$stale_marker" "$(_vetcoders_vc_frame_surface_state "$stale_marker")" >&2
    unset VC_FRAME VC_FRAME_PANE_ID VC_FRAME_SESSION_NAME ZELLIJ ZELLIJ_PANE_ID ZELLIJ_SESSION_NAME
    if [[ "${VIBECRAFTED_OPERATOR_SESSION:-}" == "$stale_marker" ]]; then
      unset VIBECRAFTED_OPERATOR_SESSION
    fi
  fi

  if [[ -n "${VIBECRAFTED_OPERATOR_SESSION:-}" ]]; then
    # Honour an explicitly-provided operator session as the visible target
    # (vc-resume / CLI dispatch rely on this). The old catalog fallback
    # workspace-{8hex} is not a place — rewrite it to the human label.
    if _vetcoders_is_legacy_operator_session_name "$VIBECRAFTED_OPERATOR_SESSION"; then
      VIBECRAFTED_OPERATOR_SESSION="$(_vetcoders_operator_session_name)"
      export VIBECRAFTED_OPERATOR_SESSION
    fi
    # Honoured while the engine reports it live (or cannot be asked). A name
    # the engine reports dead or missing is inherited env, not a choice: the
    # spawn would resurrect a host under that name that nobody is attached to.
    local explicit_surface=""
    explicit_surface="$(_vetcoders_vc_frame_surface_state "$VIBECRAFTED_OPERATOR_SESSION")"
    case "$explicit_surface" in
      dead | missing)
        printf '%s: VIBECRAFTED_OPERATOR_SESSION=%s is ambient context (%s), not a visible surface; preparing this project'"'"'s own target.\n' \
          "$verb" "$VIBECRAFTED_OPERATOR_SESSION" "$explicit_surface" >&2
        unset VIBECRAFTED_OPERATOR_SESSION
        ;;
      *)
        export VC_FRAME_SESSION_NAME="${VC_FRAME_SESSION_NAME:-$VIBECRAFTED_OPERATOR_SESSION}"
        if [[ -n "$defer_attach" ]]; then
          # A live session nobody is attached to is entered from a terminal
          # (same handover as a freshly created one); a watched one is not
          # nested into.
          if [[ "$explicit_surface" == unattended ]]; then
            _vetcoders_mark_pending_vc_frame_attach "$VIBECRAFTED_OPERATOR_SESSION" 1
          else
            _vetcoders_mark_pending_vc_frame_attach "$VIBECRAFTED_OPERATOR_SESSION" 0
          fi
        fi
        return 0
        ;;
    esac
  fi

  # One canonical workspace owner for start and resume. This runs in the
  # CALLER's shell on purpose: the resolver below runs in a subshell, so it can
  # pick a name but can never carry the workspace/session/instance ids that WES
  # attachment needs. Resolving here also refuses BEFORE any provider or AICX
  # side effect when canonical ownership cannot be established — targeting a
  # session by name alone is what handed this project's resume to a session it
  # did not own.
  if command -v _vetcoders_ensure_canonical_workspace_identity >/dev/null 2>&1; then
    _vetcoders_ensure_canonical_workspace_identity || return $?
  fi

  # Detected interactive target (typed owner — not provider-specific).
  # Priority: attached/current → explicit override → canonical bound live.
  # No canonical live target leaves the session unset and prints candidates.
  local guessed_session
  guessed_session="$(_vetcoders_resolve_interactive_operator_target)"
  if [[ -n "$guessed_session" ]]; then
    export VIBECRAFTED_OPERATOR_SESSION="$guessed_session"
    export VC_FRAME_SESSION_NAME="$guessed_session"
    export ZELLIJ_SESSION_NAME="$guessed_session"
    # A terminal opened for this project must actually SHOW that project's
    # session; otherwise its primary shell falls through to a login shell
    # while the work lands in a window nobody is looking at.
    if [[ -n "$defer_attach" ]]; then
      _vetcoders_mark_pending_vc_frame_attach "$guessed_session" 0
    fi
    return 0
  fi

  # No attachable session exists, so the only remaining option is to CREATE
  # one — which vc-frame cannot do without a real PTY. Without a controlling TTY
  # (scripts, CI, in-repo agent dispatch), leave VIBECRAFTED_OPERATOR_SESSION
  # unset and return success so interactive callers can fail closed (refuse
  # headless downgrade) while non-interactive callers continue on the
  # session-free path. The test bypass env lets the suite exercise the create
  # branch without a real TTY.
  if [[ ! -t 0 || ! -t 1 ]] && [[ -z "${VIBECRAFTED_TEST_ALLOW_NON_TTY_VC_FRAME:-}" ]]; then
    # The canonical target is now known even when it is not live, but creating
    # it needs a real PTY. Advertising a session that does not exist would send
    # the caller's provider tab at a session nobody can attach to, so keep the
    # documented contract: no target here. The resolved workspace identities
    # stay exported — they describe the project, not a live session.
    unset VIBECRAFTED_OPERATOR_SESSION VC_FRAME_SESSION_NAME
    printf 'no TTY and no live canonical operator target; leaving operator session unset\n' >&2
    return 0
  fi

  session_name="${VIBECRAFTED_OPERATOR_SESSION:-$(_vetcoders_operator_session_name)}"
  layout_file="$(_vetcoders_operator_layout_file 2>/dev/null || true)"
  [[ -n "$layout_file" ]] || return 1

  if [[ -n "$defer_attach" ]]; then
    # Create only. The caller hangs the provider tab on the ready session and
    # hands the terminal over afterwards, in that order.
    local vc_frame_bin=""
    _vetcoders_require_vc_frame || return 1
    _vetcoders_pin_vc_frame_config_dir || return $?
    vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 1
    if _vetcoders_create_vc_frame_session_detached \
      "$vc_frame_bin" "$session_name" "$layout_file"; then
      session_name="${VIBECRAFTED_PREPARED_VC_FRAME_SESSION:-$session_name}"
      export VIBECRAFTED_OPERATOR_SESSION="$session_name"
      export VC_FRAME_SESSION_NAME="$session_name"
      _vetcoders_mark_pending_vc_frame_attach "$session_name" 1
      return 0
    fi
  elif _vetcoders_ensure_vc_frame_session "$session_name" "$layout_file"; then
    session_name="${VIBECRAFTED_PREPARED_VC_FRAME_SESSION:-$session_name}"
    export VIBECRAFTED_OPERATOR_SESSION="$session_name"
    export VC_FRAME_SESSION_NAME="$session_name"
    return 0
  fi

  printf 'Failed to prepare vc-frame operator session: %s\n' "$session_name" >&2
  return 1
}

# G3 + G3b twin of spawn_vc_frame_session_action (scripts/lib/vc_frame.sh).
# Same contract: session-not-found → one attach --create-background + retry;
# ambiguous ACK → presence probe then one retry; unrecoverable host failure
# returns 2. Idiomatic to this file (no shared source).
_vetcoders_vc_frame_stderr_is_session_not_found() {
  local text="${1:-}"
  [[ -n "$text" ]] || return 1
  printf '%s' "$text" | command grep -qiE \
    "Session ['\"][^'\"]+['\"] not found|There is no active session!"
}

# Detached create refused because the exact session is already up. Recoverable
# by readiness reconciliation, unlike every other non-zero exit from that call.
_vetcoders_vc_frame_stderr_is_session_already_exists() {
  local text="${1:-}"
  [[ -n "$text" ]] || return 1
  printf '%s' "$text" | command grep -qiE "Session already exists"
}

_vetcoders_vc_frame_stderr_is_ambiguous_action_ack() {
  local text="${1:-}"
  [[ -n "$text" ]] || return 1
  printf '%s' "$text" | command grep -qiE \
    "did not acknowledge completion|completion channel closed before acknowledgement|timed out after"
}

_vetcoders_vc_frame_action_name_arg() {
  local prev=""
  local arg=""
  for arg in "$@"; do
    if [[ "$prev" == "--name" ]]; then
      printf '%s\n' "$arg"
      return 0
    fi
    prev="$arg"
  done
  return 1
}

# Lightweight name presence via list-sessions/list-tabs JSON when available.
_vetcoders_vc_frame_tab_present() {
  local vc_frame_bin="${1:-}"
  local session_name="${2:-}"
  local tab_name="${3:-}"
  local raw=""
  [[ -n "$vc_frame_bin" && -n "$tab_name" ]] || return 1
  if [[ -n "$session_name" ]]; then
    raw="$("$vc_frame_bin" --session "$session_name" action list-tabs --json 2>/dev/null || true)"
  else
    raw="$("$vc_frame_bin" action list-tabs --json 2>/dev/null || true)"
  fi
  [[ -n "$raw" ]] || return 1
  printf '%s' "$raw" | command grep -Fq "\"$tab_name\"" 2>/dev/null
}

_vetcoders_vc_frame_create_host_session() {
  local vc_frame_bin="${1:-}"
  local session_name="${2:-}"
  [[ -n "$vc_frame_bin" && -n "$session_name" ]] || return 1
  local out="" action_status=0
  # Bounded create runs with the CURRENT client's attachment context cleared,
  # and only that (2026-09-09): the caller's targeting export or a stale pane
  # marker can name exactly the session being resurrected here, and Frame's
  # native guard (src/commands.rs:844, after envs::normalize_vc_frame_env_aliases
  # mirrors VC_FRAME_SESSION_NAME into ZELLIJ_SESSION_NAME) panics on that as an
  # illegal nested reattach instead of creating anything. Same contract as
  # _vetcoders_create_vc_frame_session_detached; the explicit session argument
  # and the parent shell's own state are untouched.
  out="$(env -u VC_FRAME -u VC_FRAME_PANE_ID -u VC_FRAME_SESSION_NAME \
    -u ZELLIJ -u ZELLIJ_PANE_ID -u ZELLIJ_SESSION_NAME \
    "$vc_frame_bin" attach --create-background "$session_name" 2>&1)" || action_status=$?
  if [[ -n "$out" ]]; then
    printf '%s\n' "$out" >&2
  fi
  if [[ "$(_vetcoders_vc_frame_session_state "$session_name")" == "live" ]]; then
    return 0
  fi
  [[ "$action_status" -eq 0 ]] || return "$action_status"
  return 1
}

_vetcoders_vc_frame_session_action() {
  local vc_frame_bin="${1:-}"
  local session_name="${2:-}"
  shift 2 || true
  VETCODERS_VC_FRAME_LAST_ERROR=""
  [[ -n "$vc_frame_bin" ]] || return 1
  [[ "$#" -ge 1 ]] || return 1

  local err_file out_file action_status=0 err=""
  local tab_name=""
  tab_name="$(_vetcoders_vc_frame_action_name_arg "$@" 2>/dev/null || true)"
  # The X's must END the template: BSD mktemp(1) treats `XXXXXX.err` as a
  # literal name, so every action on the host shared ONE file, and two
  # launches at the same instant (two shells, a worker beside the Founder)
  # failed each other with "mkstemp failed … File exists".
  err_file="$(mktemp "${TMPDIR:-/tmp}/vc-frame-action.err.XXXXXX")"
  out_file="$(mktemp "${TMPDIR:-/tmp}/vc-frame-action.out.XXXXXX")"

  _vetcoders_vc_frame_action_invoke() {
    if [[ -n "$session_name" ]]; then
      "$vc_frame_bin" --session "$session_name" "$@" >"$out_file" 2>"$err_file"
    else
      "$vc_frame_bin" "$@" >"$out_file" 2>"$err_file"
    fi
  }

  _vetcoders_vc_frame_ack_presence_ok() {
    local label="${1:-presence}"
    [[ -n "$tab_name" ]] || return 1
    sleep 1
    if _vetcoders_vc_frame_tab_present "$vc_frame_bin" "$session_name" "$tab_name"; then
      printf 'vc-frame action ACK ambiguous (%s) but tab %s is present; treating as success\n' \
        "$label" "$tab_name" >&2
      return 0
    fi
    return 1
  }

  action_status=0
  _vetcoders_vc_frame_action_invoke "$@" || action_status=$?
  err="$(cat "$err_file" 2>/dev/null || true)"
  if [[ -n "$err" ]]; then
    printf '%s\n' "$err" >&2
  fi

  if _vetcoders_vc_frame_stderr_is_session_not_found "$err"; then
    VETCODERS_VC_FRAME_LAST_ERROR="$err"
    if [[ -z "$session_name" ]]; then
      rm -f "$err_file" "$out_file"
      return 2
    fi
    printf 'hosting session missing; one-shot attach --create-background %s\n' \
      "$session_name" >&2
    if ! _vetcoders_vc_frame_create_host_session "$vc_frame_bin" "$session_name"; then
      VETCODERS_VC_FRAME_LAST_ERROR="${VETCODERS_VC_FRAME_LAST_ERROR}"$'\n'"attach --create-background '${session_name}' failed"
      rm -f "$err_file" "$out_file"
      return 2
    fi
    action_status=0
    _vetcoders_vc_frame_action_invoke "$@" || action_status=$?
    err="$(cat "$err_file" 2>/dev/null || true)"
    if [[ -n "$err" ]]; then
      printf '%s\n' "$err" >&2
    fi
    if _vetcoders_vc_frame_stderr_is_session_not_found "$err" || [[ "$action_status" -ne 0 ]]; then
      VETCODERS_VC_FRAME_LAST_ERROR="${err:-vc-frame action failed after host resurrect (exit ${action_status})}"
      rm -f "$err_file" "$out_file"
      return 2
    fi
  elif [[ "$action_status" -ne 0 ]]; then
    if _vetcoders_vc_frame_stderr_is_ambiguous_action_ack "$err"; then
      if _vetcoders_vc_frame_ack_presence_ok "first-ack"; then
        rm -f "$err_file" "$out_file"
        return 0
      fi
      printf 'vc-frame action ACK timeout; one retry after brief backoff\n' >&2
      sleep 2
      action_status=0
      _vetcoders_vc_frame_action_invoke "$@" || action_status=$?
      err="$(cat "$err_file" 2>/dev/null || true)"
      if [[ -n "$err" ]]; then
        printf '%s\n' "$err" >&2
      fi
      if [[ "$action_status" -eq 0 ]]; then
        rm -f "$err_file" "$out_file"
        return 0
      fi
      if _vetcoders_vc_frame_stderr_is_ambiguous_action_ack "$err" \
        && _vetcoders_vc_frame_ack_presence_ok "retry-ack"; then
        rm -f "$err_file" "$out_file"
        return 0
      fi
    fi
    VETCODERS_VC_FRAME_LAST_ERROR="${err:-vc-frame action exit ${action_status}}"
    rm -f "$err_file" "$out_file"
    return "$action_status"
  fi

  rm -f "$err_file" "$out_file"
  return 0
}

_vetcoders_spawn_into_operator_session() {
  vc_raise_launcher_limits
  local PATH="${PATH:-}"
  PATH="$(_vetcoders_path_with_bundled_bin_priority "$PATH")"
  export PATH
  local tab_name="$1"
  local command_text="$2"
  # Operator-UI path (vc-init / operator agent / resume): land in the prepared
  # operator seat. Skill *workers* use scripts/lib spawn_launch (G7 per-project
  # host). Optional: VIBECRAFTED_WORKER_SESSION forces the G7 worker host here
  # too (marbles fleets that share this entrypoint).
  local session_name=""
  if [[ -n "${VIBECRAFTED_WORKER_SESSION:-}" ]]; then
    session_name="$(_vetcoders_effective_worker_session 2>/dev/null || true)"
  else
    session_name="${VIBECRAFTED_OPERATOR_SESSION:-$(_vetcoders_operator_session_name)}"
  fi
  [[ -n "$session_name" ]] || return 1
  local root_dir="${_vetcoders_contract_root:-$(_vetcoders_repo_root)}"
  local layout_file state
  local cmd_script
  local vc_frame_bin=""
  local run_id="${VIBECRAFTED_RUN_ID:-interactive}"
  local action_status=0

  _vetcoders_require_vc_frame || return 1
  vc_frame_bin="$(_vetcoders_vc_frame_bin)" || return 1
  if ! _vetcoders_in_vc_frame && [[ -z "${VIBECRAFTED_OPERATOR_SESSION:-}" ]]; then
    layout_file="$(_vetcoders_operator_layout_file 2>/dev/null || true)"
    state="$(_vetcoders_vc_frame_session_state "$session_name")"
    if [[ "$state" != "live" ]]; then
      _vetcoders_ensure_vc_frame_session "$session_name" "$layout_file" || return 1
      session_name="${VIBECRAFTED_PREPARED_VC_FRAME_SESSION:-$session_name}"
      export VIBECRAFTED_OPERATOR_SESSION="$session_name"
      export VC_FRAME_SESSION_NAME="$session_name"
      export ZELLIJ_SESSION_NAME="$session_name"
    fi
  fi
  # vc-frame rejects inline command args carrying shell-quoted multibyte
  # prompt content (printf '%q' + Polish UTF-8). Store the wrapper under the
  # vibecrafted artifact tree so it survives resurrect/attach and leaves a
  # readable trail for debugging.
  cmd_script="$(_vetcoders_tmp_script_path "vc-spawn-cmd" "$root_dir")"
  _vetcoders_write_command_script "$cmd_script" "$command_text" || return 1
  # --after-base (W2-B-4c): run tabs grow from the base card, newest right of
  # it, instead of drifting to the rail's far end. Probe the binary — a stale
  # install without the flag degrades to the old append placement.
  local placement_flag=""
  local focus_flag=""
  local new_tab_help=""
  new_tab_help="$("$vc_frame_bin" action new-tab --help 2>&1 || true)"
  if [[ "$new_tab_help" == *"--after-base"* ]]; then
    placement_flag="--after-base"
  fi
  if [[ -n "${VIBECRAFTED_WORKER_SESSION:-}" && "$new_tab_help" == *"--no-focus"* ]]; then
    focus_flag="--no-focus"
  fi
  # G3: check exit + stderr; one create-background on session-not-found.
  if _vetcoders_vc_frame_session_action "$vc_frame_bin" "$session_name" \
    action new-tab \
    ${placement_flag:+"$placement_flag"} \
    ${focus_flag:+"$focus_flag"} \
    --name "$tab_name" \
    --cwd "$root_dir" \
    -- "$cmd_script"; then
    printf 'launch accepted: run_id=%s target=%s/%s watch=vc-frame attach %s\n' \
      "$run_id" "$session_name" "$tab_name" "$session_name"
    return 0
  else
    action_status=$?
  fi

  printf 'launch failed: run_id=%s target=%s/%s status=%s\n' \
    "$run_id" "$session_name" "$tab_name" "$action_status" >&2
  if [[ -n "${VETCODERS_VC_FRAME_LAST_ERROR:-}" ]]; then
    printf '%s\n' "$VETCODERS_VC_FRAME_LAST_ERROR" >&2
  fi
  return "$action_status"
}
