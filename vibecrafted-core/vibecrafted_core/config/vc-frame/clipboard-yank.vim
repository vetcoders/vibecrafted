" Shared Composer/scrollback yank bridge. Buffer bytes are the copy source;
" terminal cells, gutters and viewport dimensions never enter this path.
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

    " A local terminal may reject OSC52. Deliver synchronously, before the
    " editor returns to input, including payloads above the OSC52 size cap.
    " Across SSH these commands target the guest, not the clipboard owner.
    if empty($SSH_CONNECTION) && empty($SSH_CLIENT) && empty($SSH_TTY)
      for l:command in ['pbcopy', 'wl-copy', 'xclip -selection clipboard', 'xsel --clipboard --input']
        if executable(split(l:command)[0])
          call system(l:command, l:text)
          if !v:shell_error
            break
          endif
        endif
      endfor
    endif

    " Retain the host clipboard route across SSH and vc-frame. An oversized
    " remote yank cannot use this bounded route; never substitute guest copy.
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
  augroup VcClipboardYank
    autocmd!
    autocmd TextYankPost * call VcYankBridge()
  augroup END
endif
