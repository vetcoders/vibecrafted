"""Python canary plugin contract enforced by canary_cli strict merge."""

GLOBS = ("*.py",)
KIND_ENUM = ("def", "class", "module")
REQUIRED_FIELDS = (
    "file",
    "name",
    "line",
    "kind",
    "role",
    "docstring_added",
    "authority",
)
COMPILE = "python3 -m py_compile {files}"
LINT = "ruff check {files}"
DOC_STYLE = "PEP 257; 1–3 lines; no argument restating the name"
