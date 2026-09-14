"""JavaScript canary plugin contract enforced by canary_cli strict merge."""

GLOBS = ("*.js", "*.jsx", "*.mjs", "*.cjs")
KIND_ENUM = ("function", "class", "interface", "type", "const", "method", "module")
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
LINT = "eslint {files} (where the repository configures ESLint)"
DOC_STYLE = (
    "JSDoc directly above exported declarations; describe behavior, not the name"
)
