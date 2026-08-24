"""TOML canary plugin contract enforced by canary_cli strict merge."""

GLOBS = ("*.toml",)
KIND_ENUM = ("config",)
REQUIRED_FIELDS = (
    "file",
    "name",
    "line",
    "kind",
    "role",
    "docstring_added",
    "authority",
)
COMPILE = "none"
LINT = "repository TOML linter where configured"
DOC_STYLE = "Role describes what the configuration causes, not what its keys are named"
