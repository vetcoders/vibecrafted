#!/usr/bin/env bash
# scrollback-select.sh — mouseless scrollback selection (spec 1.2 §B)
#
# Dumps the focused pane's full scrollback into a read-only vim/nvim buffer.
# Visual modes work (v / V / Ctrl-v). Every yank is a REAL copy: OSC 52 fires
# in-editor through the vc-frame clipboard chain, and on exit the last yank is
# pushed to pbcopy/wl-copy/xclip and onto the Paste Stack.
#
# The editor runs with ONE generated -u vimrc (mirrors vc-composer.sh):
#  - classic vim hard-caps -c/+cmd arguments (~10), a sourced profile does not;
#  - the operator's own vimrc stays OUT, so a host `clipboard=unnamedplus`
#    without a provider can no longer throw an ERROR on every yank here.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PASTE_STACK=""
for candidate in \
  "${SCRIPT_DIR}/paste-stack.sh" \
  "${HOME}/.config/vetcoders/frontier/vc-frame/paste-stack.sh" \
  "${HOME}/.config/vc-frame/paste-stack.sh"
do
  if [[ -x "$candidate" ]]; then
    PASTE_STACK="$candidate"
    break
  fi
done

# Host-side clipboard fallback for the last yank (OSC 52 already fired
# in-editor; this covers hosts whose outer terminal rejects OSC 52, e.g.
# stock Terminal.app). Pipes only — never a `>` redirect: a file literally
# named `pbcopy` once landed in a repo from that exact typo class.
push_clipboard() {
  local file="$1"
  if command -v pbcopy >/dev/null 2>&1; then pbcopy <"$file" && return 0; fi
  if command -v wl-copy >/dev/null 2>&1; then wl-copy <"$file" && return 0; fi
  if command -v xclip >/dev/null 2>&1; then xclip -selection clipboard <"$file" && return 0; fi
  if command -v xsel >/dev/null 2>&1; then xsel --clipboard --input <"$file" && return 0; fi
  return 1
}

tmp="$(mktemp "${TMPDIR:-/tmp}/vc-scrollback.XXXXXX")"
yank_file="$(mktemp "${TMPDIR:-/tmp}/vc-scroll-yank.XXXXXX")"
vimrc="$(mktemp "${TMPDIR:-/tmp}/vc-scrollback-vimrc.XXXXXX")"
trap 'rm -f -- "$tmp" "$yank_file" "$vimrc"' EXIT

pane_id="$(
  vc-frame action list-panes --json --all --state 2>/dev/null | python3 -c '
import json, os, sys
current = os.environ.get("VC_FRAME_PANE_ID", "")
try:
    panes = json.load(sys.stdin)
    match = next((p for p in panes if p.get("is_focused") and str(p.get("id")) != current), None)
    if not match:
        match = next((p for p in panes if not p.get("is_plugin") and str(p.get("id")) != current), None)
    if match:
        prefix = "plugin_" if match.get("is_plugin") else ""
        print(f"{prefix}{match.get(\"id\")}")
except Exception:
    pass
' || true
)"

if [[ -n "$pane_id" ]]; then
  vc-frame action dump-screen --full --pane-id "$pane_id" --path "$tmp" 2>/dev/null || true
else
  vc-frame action dump-screen --full --path "$tmp" 2>/dev/null || true
fi
if [[ ! -s "$tmp" ]]; then
  printf '(empty scrollback)\n' >"$tmp"
fi

editor_bin="vim"
if command -v nvim >/dev/null 2>&1; then
  editor_bin="nvim"
fi

# Single sourced profile — same discipline as vc-composer.sh.
{
  cat <<'VIMRC_HEAD'
set nocompatible
" No line numbers: a mouse selection copies rendered cells, so a gutter would
" ride into every paste. In-editor yanks (v/V + y) never carried it anyway.
set nonumber
set laststatus=0
set noshowcmd
set noruler
set nowrap
set sidescroll=1

" Read-only pager ergonomics: q quits (instead of arming macro recording).
nnoremap <silent> q :q!<CR>
autocmd VimEnter * echo 'Scrollback: v/V select · y copy · q quit'
VIMRC_HEAD
  printf "let g:vc_yank_file='%s'\n" "${yank_file//\'/\'\'}"
  cat <<'VIMRC_YANK'
" Yank bridge — every yank is a REAL copy. Two roads, one truth:
"  1. OSC 52 through the pane: vc-frame's grid forwards it to the host
"     clipboard chain (copy_command or outer terminal). Needs no +clipboard
"     and no provider, so it cannot throw the Linux "provider" yank ERROR.
"  2. g:vc_yank_file: the shell pushes the last yank to pbcopy/wl-copy/xclip
"     and the Paste Stack on exit.
if exists('##TextYankPost')
  function! VcYankBridge() abort
    if get(v:event, 'operator', '') !=# 'y'
      return
    endif
    let l:text = join(get(v:event, 'regcontents', []), "\n")
    if get(v:event, 'regtype', 'v') ==# 'V'
      let l:text .= "\n"
    endif
    if empty(l:text)
      return
    endif
    if exists('g:vc_yank_file')
      call writefile(split(l:text, "\n", 1), g:vc_yank_file, 'b')
    endif
    " OSC 52 payload cap — oversized yanks still reach the exit fallback.
    if strlen(l:text) > 100000
      return
    endif
    let l:b64 = substitute(system('base64', l:text), '[\r\n]', '', 'g')
    if v:shell_error
      return
    endif
    let l:seq = "\x1b]52;c;" . l:b64 . "\x07"
    if has('nvim')
      call chansend(v:stderr, l:seq)
    elseif exists('*echoraw')
      call echoraw(l:seq)
    endif
  endfunction
  autocmd TextYankPost * call VcYankBridge()
endif
VIMRC_YANK
} >"$vimrc"

if [[ "$(basename -- "$editor_bin")" == nvim* ]]; then
  "$editor_bin" -R -u "$vimrc" -- "$tmp" || true
else
  "$editor_bin" -R -N -u "$vimrc" -- "$tmp" || true
fi

if [[ -s "$yank_file" ]]; then
  push_clipboard "$yank_file" || true
  if [[ -n "$PASTE_STACK" ]]; then
    "$PASTE_STACK" push "$yank_file" || true
  fi
fi
