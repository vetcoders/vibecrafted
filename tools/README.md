# Vetcoders Hooks

Project tooling: Claude Code hooks for the vetcoders/LibraxisAI team.

## Quick Install

```bash
# Clone repo
git clone git@github.com:vetcoders/vetcoders-hooks.git
cd vetcoders-hooks

# Copy all hooks
cp *.sh $HOME/.claude/hooks/
chmod +x $HOME/.claude/hooks/*.sh

# Copy settings template (BACKUP YOUR settings.json FIRST!)
cp example-settings.json $HOME/.claude/settings.json
```

## Hook Categories

### Loctree - Structural Analysis

| Hook                    | Event                 | Purpose                                                   |
| ----------------------- | --------------------- | --------------------------------------------------------- |
| `loct-grep-augment.sh`  | PostToolUse:Grep,Bash | Auto-adds structural context (slice, where-symbol, focus) |
| `loct-smart-suggest.sh` | PostToolUse           | Proactive refactoring suggestions                         |

### Memory Hooks (rmcp-memex integration)

| Hook                       | Event            | Purpose                                               |
| -------------------------- | ---------------- | ----------------------------------------------------- |
| `memory-on-explicit.sh`    | UserPromptSubmit | Saves user commands matching "zapamiętaj", "remember" |
| `memory-on-ultrathink.sh`  | Stop             | Captures AI insights from ultrathink sessions         |
| `memory-on-compact.sh`     | PreCompact       | Saves session context before compact                  |
| `memory-context-loader.sh` | SessionStart     | Loads memory context at session start                 |

### Ultrathink Variants

| Hook                    | Purpose                        |
| ----------------------- | ------------------------------ |
| `ultrathink.sh`         | Main ultrathink trigger        |
| `ultrathink-wrapper.sh` | Wrapper with extended thinking |
| `simple-ultrathink.sh`  | Lightweight version            |

### Tool Augmentation

| Hook                           | Event                | Purpose                       |
| ------------------------------ | -------------------- | ----------------------------- |
| `brave-web-search.sh`          | PreToolUse:WebSearch | Custom web search handling    |
| `intelligent-tool-selector.sh` | PreToolUse           | Smart tool routing            |
| `load-project-context.sh`      | SessionStart         | Load project-specific context |

## MCP Servers

Copy `mcp-servers.json` to `$HOME/.claude/` for default MCP configuration:

| Server               | Type  | Purpose                                   |
| -------------------- | ----- | ----------------------------------------- |
| `memex`              | stdio | Local memex daemon (starts automatically) |
| `memex-sse`          | SSE   | Connect to running memex (multi-agent)    |
| `memex-host-a`       | SSE   | Connect to host-a's memex (remote)        |
| `youtube-transcript` | stdio | YouTube video transcripts                 |
| `brave-search`       | stdio | Web search via Brave API                  |
| `filesystem`         | stdio | File system access                        |

```bash
cp mcp-servers.json $HOME/.claude/
```

## Launchd Agents (macOS)

Daily memex optimization to prevent "too many open files" errors:

```bash
# Edit path in plist if needed, then:
cp launchd/com.vetcoders.memex-optimize.plist $VIBECRAFTED_ROOT/Library/LaunchAgents/
launchctl load $VIBECRAFTED_ROOT/Library/LaunchAgents/com.vetcoders.memex-optimize.plist
```

Runs daily at 4:00 AM. Logs: `$HOME/.ai-memories/logs/optimize.log`

## Requirements

- Claude Code CLI
- `loct` CLI (for loctree hooks) - `cargo install loctree`
- `rmcp-memex` (for memory hooks) - `cargo install rmcp-memex`
- `jq` for JSON parsing
- `BRAVE_API_KEY` env var (for brave-search)

## Configuration

See `example-settings.json` for complete configuration.

Key sections:

- **SessionStart** - load context at session start
- **PostToolUse:Grep** - augment searches with loctree + memex
- **PostToolUse:Bash** - augment bash grep/rg commands
- **UserPromptSubmit** - capture "zapamiętaj" commands
- **PreCompact** - save before compact
- **Stop** - capture ultrathink insights

---

Created by [Vibecrafted](https://vibecrafted.io) (c)2024-2026 [Vetcoders](http://vetcoders.io)
