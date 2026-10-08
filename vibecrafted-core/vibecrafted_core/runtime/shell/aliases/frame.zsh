# Frame conveniences over the installed vc-frame. Each one forwards native argv
# and native confirmation: none adds --force or --yes, and nothing here runs
# vc-frame while the profile loads. Workspaces are created by vc-start, which
# declares the repository and the shared operator frame, so there is no
# shortcut for a bare `attach --create-background`.

## Look (read-only)
vcf-ls() { # list workspaces
  command vc-frame list-sessions "$@"
}

vcf-lt() { # list tabs of the current workspace
  command vc-frame action list-tabs "$@"
}

vcf-lp() { # list panes of the current workspace
  command vc-frame action list-panes "$@"
}

vcf-w() { # watch a workspace without taking input
  command vc-frame watch "$@"
}

vcf-dr() { # diagnose the vc-frame install
  command vc-frame doctor "$@"
}

## Move
vcf-a() { # attach to a workspace by name
  command vc-frame attach "$@"
}

vcf-dt() { # detach from the current workspace
  command vc-frame action detach "$@"
}

vcf-rn() { # rename the current workspace
  command vc-frame action rename-session "$@"
}

vcf-np() { # open a new pane in the current workspace
  command vc-frame action new-pane "$@"
}

vcf-nt() { # open a new tab in the current workspace
  command vc-frame action new-tab "$@"
}

vcf-ds() { # dump a pane screen to stdout or a file
  command vc-frame action dump-screen "$@"
}

## Remove (vc-frame asks where it asks; nothing here answers for you)
vcf-k() { # kill one running workspace by name
  command vc-frame kill-session "$@"
}

vcf-ka() { # kill every running workspace, after vc-frame asks
  command vc-frame kill-all-sessions "$@"
}

vcf-d() { # delete one exited workspace by name
  command vc-frame delete-session "$@"
}

vcf-da() { # delete every exited workspace, after vc-frame asks
  command vc-frame delete-all-sessions "$@"
}
