"""Shell canary plugin contract enforced by canary_cli strict merge."""

GLOBS = ("*.sh", "*.bash", "*.zsh")
KIND_ENUM = ("function", "script")
REQUIRED_FIELDS = (
    "file",
    "name",
    "line",
    "kind",
    "role",
    "docstring_added",
    "authority",
)
COMPILE = "bash -n {files}"
LINT = "shellcheck {files}"
DOC_STYLE = "Comment block directly above each function; describe observable behavior"
