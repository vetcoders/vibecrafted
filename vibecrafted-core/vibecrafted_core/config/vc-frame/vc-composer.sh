#!/usr/bin/env bash
# vc-composer.sh — Command Composer with Paste Stack integration (spec 1.2 §A)
#
# Contract (same door as compact-bar chip and Super+e / Cmd+E — Alt+e is free
# for Polish `ę` on macOS):
#   1. Draft in vim with: nonumber, laststatus=0, nowrap (clean -- INSERT --)
#   2. Ctrl+p opens the Paste Stack picker and inserts at cursor
#   3. `?` in normal mode toggles the built-in cheat sheet (q/Esc closes) —
#      backward-search is deliberately traded away; `/` still searches
#   4. On non-empty :wq/ZZ: push body to Paste Stack, hide floating panes,
#      write-chars into the underlying pane (unexecuted — Enter is human)
#   5. Any yank (y/yy/Y) is a REAL copy: OSC 52 fires in-editor through the
#      vc-frame clipboard chain, and pbcopy/wl-copy/xclip picks up the last
#      yank on exit for hosts whose outer terminal rejects OSC 52
#   6. Clean up the temp draft
#
# IMPORTANT: all settings go through ONE -u vimrc file. Classic vim hard-caps
# the number of -c / +cmd arguments (~10) and dies with:
#   Too many "+command", "-c command" or "--cmd command" arguments
#
# Installed at: $XDG_CONFIG_HOME/vibecrafted/vc-frame/vc-composer.sh
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
resolve_tool() {
  local name="$1"
  local candidate
  for candidate in \
    "${SCRIPT_DIR}/${name}" \
    "${XDG_CONFIG_HOME:-$HOME/.config}/vibecrafted/vc-frame/${name}"
  do
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

PASTE_STACK="$(resolve_tool paste-stack.sh || true)"

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

f="$(mktemp "${TMPDIR:-/tmp}/vc-composer.XXXXXX")" || exit 1
vimrc="$(mktemp "${TMPDIR:-/tmp}/vc-composer-vimrc.XXXXXX")" || exit 1
yank_file="$(mktemp "${TMPDIR:-/tmp}/vc-composer-yank.XXXXXX")" || exit 1
cleanup() { rm -f -- "$f" "$vimrc" "$yank_file"; }
trap cleanup EXIT

# Seed from Paste Stack top unless VC_COMPOSER_SEED=0.
seed="${VC_COMPOSER_SEED:-1}"
if [[ "$seed" != "0" && -n "$PASTE_STACK" ]]; then
  "$PASTE_STACK" top "$f" || true
fi

wrap_line='set nowrap'
if [[ "${VC_COMPOSER_WRAP:-0}" == "1" ]]; then
  wrap_line='set wrap'
fi

# Semantic caret (design: caret-semantics.md) — the cursor shape names the
# mode: insert=beam, normal=underline (brand), visual=blinking block,
# replace/cmdline=blinking underline, operator-pending=block.
# VC_COMPOSER_CARET=0 is the operator escape hatch: the generated vimrc then
# carries today's clean profile with no caret sequences at all.
caret="${VC_COMPOSER_CARET:-1}"
# Brand-gold caret color (OSC 12/112). Default OFF until the W2-B
# pass-through verdict; flip with VC_COMPOSER_CARET_COLOR=1.
caret_color="${VC_COMPOSER_CARET_COLOR:-0}"

# Single sourced profile — never stack a dozen -c flags (vim 9.x hard limit).
{
  cat <<'VIMRC_HEAD'
set nocompatible
" No line numbers: the Composer is prose, not code, and a mouse selection
" copies rendered cells — numbers in the gutter would ride into every paste.
set nonumber
set norelativenumber
set laststatus=0
set noshowcmd
set noruler
set textwidth=0
set nolinebreak
set sidescroll=1
set sidescrolloff=2
nnoremap <silent> <F2> :set wrap! wrap?<CR>
nnoremap <silent> <Leader>w :set wrap! wrap?<CR>

" ? = built-in cheat sheet. The Composer is a scratch drafting pad, not a
" vim session — a novice's "how do I even leave" beats backward-search.
nnoremap <silent> ? :call VcComposerHelp()<CR>
function! VcComposerHelp() abort
  let l:existing = bufnr('VC_COMPOSER_HELP')
  if l:existing != -1 && bufwinnr(l:existing) != -1
    execute bufwinnr(l:existing) . 'wincmd w'
    close
    return
  endif
  let l:lines = [
        \ '  Composer — cheat sheet                              (q closes)  ',
        \ '',
        \ '  WRITE       i   start typing (INSERT)      Esc  stop typing',
        \ '  SEND        :wq  or  ZZ   → text lands in your shell UNEXECUTED',
        \ '                             review it, then press Enter yourself',
        \ '  CANCEL      :q!            quit without sending anything',
        \ '  PASTE       Ctrl+p         insert from the Paste Stack',
        \ '  WRAP        F2             toggle line wrap',
        \ '  UNDO/REDO   u  /  Ctrl+r',
        \ '  COPY        yy line · v…y selection → system clipboard',
        \ '  LINES       dd delete · p paste below',
        \ '  MOVE        arrows work everywhere · gg top · G bottom',
        \ '',
        \ '  Empty draft on :wq = cancel. Nothing runs without your Enter.',
        \ ]
  botright new
  silent file VC_COMPOSER_HELP
  setlocal buftype=nofile bufhidden=wipe nobuflisted noswapfile
  setlocal nonumber norelativenumber winfixheight
  call setline(1, l:lines)
  execute 'resize' (len(l:lines) + 1)
  setlocal nomodifiable
  nnoremap <silent> <buffer> q :close<CR>
  nnoremap <silent> <buffer> ? :close<CR>
  nnoremap <silent> <buffer> <Esc> :close<CR>
endfunction

" One-line orientation hint in the free cmdline (laststatus=0 keeps it clear).
autocmd VimEnter * set nonumber norelativenumber
autocmd VimEnter * echo 'Composer: i = type · :wq = send · ? = help'
VIMRC_HEAD
  printf '%s\n' "$wrap_line"
  printf "let g:vc_yank_file='%s'\n" "${yank_file//\'/\'\'}"
  cat <<'VIMRC_YANK'
" Yank bridge — every yank is a REAL copy. Two roads, one truth:
"  1. OSC 52 through the pane: vc-frame's grid forwards it to the host
"     clipboard chain (copy_command or outer terminal). Needs no +clipboard
"     and no provider, so it cannot throw the Linux "provider" yank ERROR.
"  2. g:vc_yank_file: the shell pushes the last yank to pbcopy/wl-copy/xclip
"     on exit, for hosts whose outer terminal rejects OSC 52.
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
  if [[ -n "$PASTE_STACK" ]]; then
    # Escape single quotes for a vim string literal.
    local_ps="${PASTE_STACK//\'/\'\'}"
    cat <<EOF
nnoremap <silent> <C-p> :let __vc_ps=tempname() \\| execute 'silent !${local_ps} pick > ' . shellescape(__vc_ps) \\| if filereadable(__vc_ps) && getfsize(__vc_ps) > 0 \\| execute 'read' __vc_ps \\| endif \\| call delete(__vc_ps)<CR>
EOF
  fi
  if [[ "$caret" != "0" ]]; then
    cat <<'VIMRC_CARET'
" Semantic caret (caret-semantics.md): normal=underline _ (brand), insert=beam,
" visual=blinking block (hollow-block approximation), replace/cmdline=blinking
" underline, operator-pending=block. On exit the host cursor MUST return to its
" default — a composer that leaves the panel with a permanent beam is a
" regression worse than no feature.
if has('nvim')
  " nvim translates guicursor into DECSCUSR for the host terminal.
  set guicursor=n-sm:hor20,i-ci-si:ver25,v-ve:block-blinkwait175-blinkon400-blinkoff250,o:block,r-cr:hor20-blinkwait175-blinkon400-blinkoff250,c:hor20-blinkwait175-blinkon400-blinkoff250
  autocmd VimLeave * call chansend(v:stderr, "\x1b[0 q")
else
  " Classic vim: termcaps carry insert (DECSCUSR 6), replace (3), normal (4).
  let &t_SI = "\e[6 q"
  let &t_SR = "\e[3 q"
  let &t_EI = "\e[4 q"
  if exists('*echoraw')
    " Paint the brand caret immediately, not on the first mode roundtrip.
    autocmd VimEnter * call echoraw(&t_EI)
    " Visual and operator-pending have no termcap; ModeChanged (vim >=
    " 8.2.3770) is the only road. Without it: three states instead of five,
    " still one coherent language (accepted degradation).
    if has('autocmd') && exists('##ModeChanged')
      autocmd ModeChanged *:[vV\x16]* call echoraw("\e[1 q")
      autocmd ModeChanged *:no* call echoraw("\e[2 q")
      autocmd ModeChanged [vV\x16no]*:n call echoraw(&t_EI)
    endif
    if exists('##CmdlineEnter')
      autocmd CmdlineEnter * call echoraw("\e[3 q")
      autocmd CmdlineLeave * call echoraw(&t_EI)
    endif
  endif
  " Hand the host its default cursor back (DECSCUSR 0) on the way out.
  autocmd VimLeave * silent !printf '\033[0 q'
endif
VIMRC_CARET
    if [[ "$caret_color" == "1" ]]; then
      cat <<'VIMRC_CARET_COLOR'
" Brand-gold caret while composing (OSC 12); OSC 112 resets on leave.
if has('nvim')
  autocmd VimEnter * call chansend(v:stderr, "\x1b]12;#c99a3b\x07")
  autocmd VimLeave * call chansend(v:stderr, "\x1b]112\x07")
elseif exists('*echoraw')
  autocmd VimEnter * call echoraw("\x1b]12;#c99a3b\x07")
  autocmd VimLeave * call echoraw("\x1b]112\x07")
endif
VIMRC_CARET_COLOR
    fi
  fi
} >"$vimrc"

if [[ -n "${VC_COMPOSER:-}" ]]; then
  # Operator override is a full command line (e.g. pensieve --wait).
  # shellcheck disable=SC2086
  ${VC_COMPOSER} "$f"
else
  editor="${EDITOR:-vim}"
  # -u: only our profile. -N: nocompatible when -u is used. No extra -c.
  if [[ "$(basename -- "$editor")" == nvim || "$editor" == *nvim* ]]; then
    "$editor" -u "$vimrc" "$f"
  else
    "$editor" -N -u "$vimrc" "$f"
  fi
fi

if [[ -s "$yank_file" ]]; then
  push_clipboard "$yank_file" || true
fi

if [[ -s "$f" ]]; then
  if [[ -n "$PASTE_STACK" ]]; then
    "$PASTE_STACK" push "$f" || true
  fi
  vc-frame action toggle-floating-panes || true
  vc-frame action write-chars "$(cat -- "$f")"
fi
