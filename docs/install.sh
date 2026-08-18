#!/bin/sh
set -eu

# Doc-relative shim for the canonical installer one directory up.
#
# `bash` is explicit on purpose. `scripts/build-portable-release.sh` states the
# contract: "The entrypoint is `bash install.sh`, not `./install.sh`: the packer
# canonicalises modes and the repository file carries no executable bit, so do
# not test for one." A bare `exec "$script_dir/../install.sh"` inherits that
# missing bit and dies with 126 on every fresh clone — which is precisely the
# surface a Windows user meets first.
script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
exec bash "$script_dir/../install.sh" "$@"
