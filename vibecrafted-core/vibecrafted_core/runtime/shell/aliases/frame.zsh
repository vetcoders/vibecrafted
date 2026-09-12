# Optional Frame conveniences. Forward native argv and confirmation; never
# add --force and never run vc-frame during profile load.
vcf-lp() {
  command vc-frame action list-panes "$@"
}

vcf-ls() {
  command vc-frame list-sessions "$@"
}

vcf-da() {
  command vc-frame delete-all-sessions "$@"
}
