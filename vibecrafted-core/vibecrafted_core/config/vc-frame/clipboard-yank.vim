" Shared Composer/scrollback yank bridge. Buffer bytes are the copy source;
" terminal cells, gutters and viewport dimensions never enter this path.
if exists('##TextYankPost')
  function! VcYankDeliver(command, text) abort
    " File input avoids blocking while feeding a helper that never reads stdin.
    let l:input = tempname()
    try
      call writefile(split(a:text, "\n", 1), l:input, 'b')
      if has('nvim') && exists('*jobwait')
        let l:job = jobstart(['sh', '-c', 'exec ' . a:command . ' < ' . shellescape(l:input)])
        if l:job <= 0
          return 0
        endif
        let l:status = jobwait([l:job], 250)[0]
        if l:status < 0
          call jobstop(l:job)
        endif
        return l:status == 0
      elseif exists('*job_start')
        let l:job = job_start(split(a:command), {
              \ 'in_io': 'file', 'in_name': l:input,
              \ 'out_io': 'null', 'err_io': 'null'})
        let l:started = reltime()
        while job_status(l:job) ==# 'run' && reltimefloat(reltime(l:started)) < 0.25
          sleep 5m
        endwhile
        if job_status(l:job) ==# 'run'
          call job_stop(l:job, 'kill')
          return 0
        endif
        return job_status(l:job) ==# 'dead' && get(job_info(l:job), 'exitval', -1) == 0
      endif
      " Editors without jobs retain OSC52 instead of risking an unbounded wait.
      return 0
    finally
      call delete(l:input)
    endtry
  endfunction

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

    " A local terminal may reject OSC52. Give each helper at most 250ms,
    " including payloads above the OSC52 size cap, then try the next candidate.
    " Across SSH these commands target the guest, not the clipboard owner.
    if empty($SSH_CONNECTION) && empty($SSH_CLIENT) && empty($SSH_TTY)
      for l:command in ['pbcopy', 'wl-copy', 'xclip -selection clipboard', 'xsel --clipboard --input']
        if executable(split(l:command)[0]) && VcYankDeliver(l:command, l:text)
          break
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
