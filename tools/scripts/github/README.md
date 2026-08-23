# Git Tools

CLI tools for GitHub repository management.

## repo-transfer.py

Universal tool for bulk repository management: list, transfer, delete, clean transfer.
Generates interactive HTML forms and executes operations from JSON.

### Requirements

- Python 3.11+
- [GitHub CLI](https://cli.github.com/) (`gh`) authenticated
- [uv](https://github.com/astral-sh/uv) (optional, for shebang execution)

### Usage

```bash
# Make executable
chmod +x repo-transfer.py

# Interactive form mode (starts HTTP server + opens browser)
./repo-transfer.py <source-owner> LibraxisAI Vetcoders

# Open form in browser without server
./repo-transfer.py <source-owner> LibraxisAI --open

# Execute operations from JSON
./repo-transfer.py --execute cleanup.json
./repo-transfer.py --execute cleanup.json --dry-run

# List repos
./repo-transfer.py --list <source-owner>
./repo-transfer.py --list <source-owner> --json
./repo-transfer.py --list <source-owner> --filter "vista-*"

# Direct operations
./repo-transfer.py --delete Owner/repo --yes
./repo-transfer.py --transfer Owner/repo --to NewOwner --yes
./repo-transfer.py --transfer Owner/repo --to NewOwner/new-name --yes
./repo-transfer.py --transfer Owner/repo --to NewOwner --clean --yes
```

### Features

| Feature             | Description                                                    |
| ------------------- | -------------------------------------------------------------- |
| **HTML Form**       | Interactive web UI for selecting actions per repo              |
| **HTTP Server**     | Live server with refresh button for dynamic updates            |
| **Bulk Operations** | Select all forks, all from org, etc.                           |
| **Transfer**        | Standard GitHub transfer (keeps fork relations)                |
| **Clean Transfer**  | `git clone --bare` + `git push --mirror` (breaks fork network) |
| **JSON Export**     | Export decisions as JSON for automated execution               |
| **Dry Run**         | Preview operations without executing                           |

### Form Actions

| Button     | Description                                               |
| ---------- | --------------------------------------------------------- |
| 🗑️ Delete  | Permanently delete repository                             |
| ✓ Keep     | Keep as is (no action)                                    |
| → Transfer | Standard transfer to new owner                            |
| ⚡ Clean   | Clean transfer (bare clone + mirror push, deletes source) |

### JSON Format

```json
{
  "generated_at": "2026-01-13T00:00:00.000Z",
  "summary": {
    "total": 133,
    "delete": 10,
    "keep": 100,
    "transfer": 5,
    "clean": 18
  },
  "actions": {
    "delete": [{ "repo": "Owner/repo-name" }],
    "keep": [],
    "transfer": [
      {
        "repo": "Owner/repo",
        "target": "NewOwner",
        "newName": "optional-new-name"
      }
    ],
    "clean": [{ "repo": "Owner/repo", "target": "NewOwner" }]
  }
}
```

### Clean Transfer

Standard GitHub transfer keeps fork relationships. Clean transfer:

1. Creates new empty repo on target
2. `git clone --bare` source repo
3. `git push --mirror` to target
4. Optionally deletes source

This breaks fork network and creates a completely independent copy.

---

_Copyright © 2024–2026 Vetcoders_
