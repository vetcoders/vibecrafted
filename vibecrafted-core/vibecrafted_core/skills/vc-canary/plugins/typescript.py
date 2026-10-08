"""TypeScript canary plugin contract enforced by canary_cli strict merge."""

GLOBS = ("*.ts", "*.tsx", "*.mts", "*.cts")
KIND_ENUM = (
    "function",
    "class",
    "interface",
    "type",
    "enum",
    "const",
    "method",
    "module",
)
REQUIRED_FIELDS = (
    "file",
    "name",
    "line",
    "kind",
    "role",
    "docstring_added",
    "authority",
)
COMPILE = "tsc --noEmit (where the repository configures TypeScript)"
LINT = "eslint {files} (where the repository configures ESLint)"
DOC_STYLE = (
    "TSDoc directly above exported declarations; describe behavior, not the name"
)
