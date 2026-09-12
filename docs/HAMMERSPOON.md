# Hammerspoon URL handler stack — Plan 11

Vetcoders ships a repo-tracked Hammerspoon configuration that registers eight
`hammerspoon://vc-*` URL schemes with macOS Launch Services. Combined with the
iTerm2 OSC 8 hyperlink GA (Plan 10) and the vc_frame mesh-aware theming (Plan 12),
this closes the **stack agent-native runtime** loop documented in
doctrine 2026-05-08:

```
agent emits OSC 8 hyperlink in terminal output
  → operator Cmd+Click in iTerm2
    → macOS open URL ("hammerspoon://vc-*")
      → Hammerspoon URL handler (this stack)
        → AppleScript spawn iTerm2 tab
          → CLI dispatch (loct / aicx / vibecrafted marbles / editor)
            → result back in operator's workspace
```

Mainstream IDEs do not have this round-trip. Vibecrafted does.

## What this stack does

| Handler        | URL shape                                                         | Effect                                                                       |
| -------------- | ----------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| `vc-ping`      | `hammerspoon://vc-ping?msg=<text>`                                | Sanity check — shows alert, logs to console.                                 |
| `vc-open-file` | `hammerspoon://vc-open-file?path=<abs>`                           | Opens file in preferred editor (Cursor, VSCode, Zed, …).                     |
| `vc-loct`      | `hammerspoon://vc-loct?cmd=<cmd>&repo=<abs>`                      | Spawns iTerm2 tab running `loct <cmd>` (optional `cd <repo>` prefix).        |
| `vc-aicx`      | `hammerspoon://vc-aicx?query=<text>&project=<name>`               | Spawns iTerm2 tab running `aicx search <query>` (optional `--project`).      |
| `vc-atlas`     | `hammerspoon://vc-atlas?card=<id>&project=<abs>`                  | Opens `<project>/.loctree/context-atlas/<card>.md` in preferred editor.      |
| `vc-prism`     | `hammerspoon://vc-prism?task=<a+b+c>&project=<abs>`               | Spawns iTerm2 tab running `loct prism --task=<csv>` (`+` translates to `,`). |
| `vc-marbles`   | `hammerspoon://vc-marbles?repo=<abs>&iteration=<NN>&agent=<name>` | Spawns iTerm2 tab running `vibecrafted marbles <agent>`.                     |
| `vc-followup`  | `hammerspoon://vc-followup?repo=<abs>`                            | Opens latest report in `<repo>/.vibecrafted/reports/marbles/<YYYY_MMDD>/`.   |

Plan A shipped the first four handlers (2026-05-08). Plan 11 promoted the
config to a repo-tracked template (`config/hammerspoon/init.lua`), added the
remaining four handlers, and hardened the injection sanitization.

## Install

```sh
make install-hammerspoon
```

What happens:

- Copies `config/hammerspoon/init.lua` to `~/.hammerspoon/init.lua`.
- If an existing config is present and differs, backs it up to
  `~/.hammerspoon/init.lua.bak` after operator confirmation
  (use `--force` to skip the prompt).
- Verifies `hs.allowAppleScript(true)` is declared (required for any future
  `osascript reload` flow).
- Reloads Hammerspoon via `pkill Hammerspoon + open -a Hammerspoon`.
  We avoid `osascript reload` here because it requires the new init.lua to
  already be live — chicken-and-egg on first install.

Idempotent: rerunning with identical source content is a no-op.

Non-macOS hosts: the script exits 0 with a notice and does nothing.

### Direct script invocation

```sh
scripts/install-hammerspoon.sh             # interactive on overwrite
scripts/install-hammerspoon.sh --force     # overwrite without prompt
scripts/install-hammerspoon.sh --no-reload # do not pkill Hammerspoon
scripts/install-hammerspoon.sh --help
```

`HAMMERSPOON_DIR` env var overrides the target directory (default
`~/.hammerspoon`).

## Smoke

```sh
make test-hammerspoon
```

Runs four tiers:

1. **Shipped artifacts** — template, install script present and executable.
2. **Static analysis** — `bash -n` and `shellcheck` on bash, optional
   `luac -p` on Lua (Hammerspoon-extension syntax may not parse — soft skip).
3. **Structural grep** — all eight `hs.urlevent.bind("vc-*", ...)` calls
   present in `init.lua`; sanitizer constants (`SAFE_CHARSET`,
   `MAX_PARAM_LEN`, `SHELL_METACHAR_BLOCKLIST`, traversal check) present.
4. **Sanitization unit tests** — extracts the validator into a transient
   Lua harness and runs 8 positive + 4 negative cases. All positives must
   be accepted; all negatives must be rejected.
5. **Live integration (macOS only)** — surfaces the manual command for the
   operator to fire (does not auto-spawn iTerm2 tabs mid-CI).

Linux/CI hosts skip the live tier with an explicit message.

### Manual live smoke

```sh
open 'hammerspoon://vc-ping?msg=smoke-test'
```

Expected:

- macOS Hammerspoon shows alert `🟢 vc-ping ok — scheme handler aktywny`.
- `~/Library/Logs/Hammerspoon/Hammerspoon.log` contains `[vc-handler] vc-ping params: ...`.

## Injection sanitization (Plan 11 hardened)

Every handler routes its query parameters through `vc_params_valid()` before
shelling out. The validator enforces four layered defenses:

1. **Per-handler allowlist** — each handler declares the exact set of
   accepted query keys. Any extra param is rejected with
   `reject: unknown param "..."`. This catches malicious URLs that append
   shell-injection vectors as new params.

2. **Per-param charset regex** — `^[%w%s%-_=%./%+:]+$` allows only
   alphanumerics, whitespace, and the punctuation `- _ = . / + :`. Shell
   metachars (`; & | $ \` < > \* ? ' "`), control chars, and binary data are
   rejected at this gate.

3. **Path-traversal check** — any `..` substring is rejected, even when the
   charset would otherwise allow it. Prevents `?project=/Users/op/../etc/passwd`
   from resolving to a sensitive file.

4. **Length cap** — values longer than 256 chars are rejected. DoS defense
   against operators (or hostile MCP outputs) emitting megabyte URLs.

5. **Shell-metachar blocklist** — defense-in-depth. The blocklist would
   only ever fire if the charset regex were widened in a future change;
   today it is dead code by design, kept to make intent explicit.

The doctrine 2026-05-08 entry called out the original Plan A regex
(`^[%w%s%-_=%./%+]+$`) as the foundational defense. Plan 11 extended it
with the four additional layers above.

### Negative test cases (what gets blocked)

The smoke test verifies four default attacks are rejected:

| Attack                   | Example                         | Rejection reason                  |
| ------------------------ | ------------------------------- | --------------------------------- |
| Shell-metachar injection | `?cmd=health; rm -rf /`         | charset (`;` outside allowed set) |
| Path traversal           | `?path=/Users/op/../etc/passwd` | traversal (`..` substring)        |
| Length DoS               | `?query=AAA...` (300 chars)     | length (cap 256)                  |
| Unknown param            | `?repo=/abs&malicious=exploit`  | unknown:malicious                 |

## Cross-references

- **Plan 10 — iTerm2 stack GA** (`docs/ITERM2.md`): the OSC 8 hyperlink
  emitter that produces the URLs this stack consumes.
- **Plan 12 — vc_frame mesh-aware theming** (`docs/VC_FRAME.md`): the
  layout/theme layer the spawned tabs render into.
- **doctrine 2026-05-08**: stack agent-native runtime moment —
  _"memory (aicx) + structure (loctree) + execution (vc_frame + marbles + agents) +
  visual+dispatch (OSC primitives + Hammerspoon URL handlers) + discipline
  (vc-init + AGENT MODEL PARITY + Living Tree). Cross-layer round-trip w
  jednym kliku."_

## Troubleshooting

**`open 'hammerspoon://vc-ping'` does nothing.**
Hammerspoon is not running, or the URL handlers are not loaded.
Run `make install-hammerspoon` and verify Hammerspoon appears in the
menu bar. Check `~/Library/Logs/Hammerspoon/Hammerspoon.log` for a
boot banner that lists 8 registered handlers.

**`vc-loct` fires but the iTerm2 tab closes immediately.**
The CLI command exited and your shell does not have `read` as a builtin.
Every spawned command is wrapped with `; printf '[exit %d] press enter...' $?; read`
— make sure the spawned shell is bash or zsh. If you run a non-POSIX
login shell, edit `vc_wrap_with_read_tail()` in `config/hammerspoon/init.lua`.

**`vc-atlas` opens the wrong editor.**
The `PREFERRED_EDITORS` list (top of `init.lua`) probes editors in order:
Cursor, VSCode, Code, Zed, TextMate, BBEdit, MacVim. Re-order or trim to
match your preference. Falls back to `open <path>` (default system handler)
if none of the preferred editors are installed.

**`hs.allowAppleScript(true)` warning in install log.**
The destination init.lua lacks this declaration. AppleScript-based reload
(`osascript -e 'tell application "Hammerspoon" to execute lua code "hs.reload()"'`)
will be denied until the file is reloaded once via menu bar or `pkill +
open`. The install script does this automatically — re-run
`make install-hammerspoon`.

## File layout

```
vibecrafted/
├── config/
│   └── hammerspoon/
│       └── init.lua              # repo-tracked template (Plan 11)
├── scripts/
│   └── install-hammerspoon.sh    # operator install entry
├── tests/
│   └── hammerspoon_smoke.sh      # static + sanitizer + live tiers
└── docs/
    └── HAMMERSPOON.md            # this file
```

---

Vibecrafted. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
