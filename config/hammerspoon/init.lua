-- vibecrafted/config/hammerspoon/init.lua — Plan 11 (META_22).
--
-- Repo-tracked Hammerspoon configuration template that ships the Vetcoders
-- URL handler stack. Operators install with `make install-hammerspoon`
-- (scripts/install-hammerspoon.sh copies this file to ~/.hammerspoon/init.lua,
-- offers .bak overwrite on re-run, triggers Hammerspoon reload).
--
-- Stack agent-native runtime glue (per doctrine 2026-05-08):
--   OSC 8 hyperlink → iTerm2 Cmd+Click → macOS open URL →
--   Hammerspoon url handler → AppleScript spawn iTerm2 tab → CLI dispatch.
--
-- Plan A handlers (shipped 2026-05-08, preserved verbatim):
--   vc-ping       — sanity check that scheme dispatch is active
--   vc-open-file  — open file in preferred editor
--   vc-loct       — `loct <cmd>` in new iTerm2 tab (optional cd <repo>)
--   vc-aicx       — `aicx search <query>` in new iTerm2 tab
--
-- Plan 11 handlers (new):
--   vc-atlas      — open atlas card from .loctree/context-atlas/
--   vc-prism      — run `loct prism --task=<csv>` in new iTerm2 tab
--   vc-marbles    — spawn marbles run in new iTerm2 tab
--   vc-followup   — open latest marbles report in preferred editor
--
-- Injection sanitization is HARDENED in Plan 11:
--   - per-handler allowlist of accepted query params (unknown params rejected)
--   - per-param regex allowlist using the Lua-safe charset:
--       ^[%w%s%-_=%./%+:]+$
--     (alfanum, whitespace, `-_=./+:` — `:` for URL-style values like
--      `path:loctree-rs/src/`)
--   - path-traversal rejection: any `..` substring is blocked
--   - shell metachar rejection: backticks, `$`, `;`, `&`, `|` blocked
--   - value length cap: 256 chars (DoS defense)
--
-- Vibecrafted with AI Agents (c)2024-2026 LibraxisAI

hs.allowAppleScript(true)

-- ============================================================================
-- Logging primitives
-- ============================================================================

local function vc_log(msg)
    print("[vc-handler] " .. tostring(msg))
end

local function vc_alert(msg, sec)
    hs.alert.show(msg, sec or 1.5)
    vc_log(msg)
end

-- ============================================================================
-- Injection sanitization (Plan 11 hardened)
-- ============================================================================
-- Per-param value validation. Returns true if value passes all checks,
-- false otherwise. Side-effect: emits hs.console log line on rejection
-- so the operator can see why the URL was refused.

local MAX_PARAM_LEN = 256
local SAFE_CHARSET = "^[%w%s%-_=%./%+:]+$"

local SHELL_METACHAR_BLOCKLIST = {
    "`", "$", ";", "&", "|", "\n", "\r", "<", ">", "\\",
    "*", "?", "'", "\"",
}

local function vc_param_valid(name, value)
    -- type-guard before the length/charset checks below: a non-string value
    -- (boolean/number from a malformed URL handler) would crash `#value` /
    -- `value:match()`, so reject anything that is not a non-empty string.
    if type(value) ~= "string" or value == "" then
        vc_log(string.format("reject: param %q is empty/nil/non-string", name))
        return false
    end
    if #value > MAX_PARAM_LEN then
        vc_log(string.format(
            "reject: param %q length %d exceeds cap %d",
            name, #value, MAX_PARAM_LEN
        ))
        return false
    end
    if not value:match(SAFE_CHARSET) then
        vc_log(string.format(
            "reject: param %q value %q outside safe charset",
            name, value
        ))
        return false
    end
    if value:find("..", 1, true) then
        vc_log(string.format(
            "reject: param %q contains path-traversal '..' (%s)",
            name, value
        ))
        return false
    end
    for _, ch in ipairs(SHELL_METACHAR_BLOCKLIST) do
        if value:find(ch, 1, true) then
            vc_log(string.format(
                "reject: param %q contains shell metachar %q",
                name, ch
            ))
            return false
        end
    end
    return true
end

-- Validate the full params table against an allowed-keys list. Any unknown
-- key is rejected (defense-in-depth — surfaces malicious extra params and
-- typos in operator-authored hyperlinks). Returns true|false.
local function vc_params_valid(params, allowed_keys)
    if type(params) ~= "table" then
        vc_log("reject: params is not a table")
        return false
    end
    local allow_set = {}
    for _, k in ipairs(allowed_keys) do allow_set[k] = true end
    for k, v in pairs(params) do
        if not allow_set[k] then
            vc_log(string.format("reject: unknown param %q", k))
            return false
        end
        if not vc_param_valid(k, v) then
            return false
        end
    end
    return true
end

-- ============================================================================
-- Editor + iTerm2 spawn helpers
-- ============================================================================

local PREFERRED_EDITORS = {
    "Cursor", "Visual Studio Code", "Code", "Zed", "TextMate", "BBEdit", "MacVim",
}

local function vc_open_in_editor(path)
    if not path or path == "" then
        vc_alert("vc: missing path", 2)
        return false
    end
    for _, app in ipairs(PREFERRED_EDITORS) do
        local _, ok = hs.execute(
            string.format("open -a %q %q 2>/dev/null", app, path)
        )
        if ok then return true end
    end
    hs.execute(string.format("open %q", path))
    return true
end

local function vc_iterm_run_in_new_tab(command_text)
    local script = string.format([[
        tell application "iTerm2"
            activate
            tell current window
                create tab with default profile
                tell current session of current tab
                    write text %q
                end tell
            end tell
        end tell
    ]], command_text)
    local _, ok = hs.osascript.applescript(script)
    return ok
end

-- Wrap a CLI command with `read` tail so the iTerm2 tab stays open after the
-- command finishes — operator can read output + exit code before closing.
local function vc_wrap_with_read_tail(cmd)
    return string.format(
        "%s; echo ''; printf '\\n[exit %%d] press enter to close...' $?; read",
        cmd
    )
end

-- ============================================================================
-- Plan A handler: vc-open-file
-- hammerspoon://vc-open-file?path=/abs/path/to/file
-- ============================================================================

hs.urlevent.bind("vc-open-file", function(eventName, params)
    if not vc_params_valid(params, {"path"}) then
        vc_alert("vc-open-file: invalid params", 2)
        return
    end
    local path = params.path
    if vc_open_in_editor(path) then
        vc_alert("📄 " .. (path:match("([^/]+)$") or path), 1)
    end
end)

-- ============================================================================
-- Plan A handler: vc-loct
-- hammerspoon://vc-loct?cmd=health&repo=/abs/path
-- ============================================================================

hs.urlevent.bind("vc-loct", function(eventName, params)
    if not vc_params_valid(params, {"cmd", "repo"}) then
        vc_alert("vc-loct: invalid params", 2)
        return
    end
    local cmd = params.cmd
    if not cmd then
        vc_alert("vc-loct: missing ?cmd=", 2)
        return
    end
    local repo = params.repo
    local cd_part = repo and string.format("cd %q && ", repo) or ""
    local full = vc_wrap_with_read_tail(
        string.format("%sloct %s", cd_part, cmd)
    )
    if vc_iterm_run_in_new_tab(full) then
        vc_alert("⚒  loct " .. cmd, 1)
    else
        vc_alert("vc-loct: iTerm2 spawn failed", 2)
    end
end)

-- ============================================================================
-- Plan A handler: vc-aicx
-- hammerspoon://vc-aicx?query=text+to+search&project=optional
-- ============================================================================

hs.urlevent.bind("vc-aicx", function(eventName, params)
    if not vc_params_valid(params, {"query", "project"}) then
        vc_alert("vc-aicx: invalid params", 2)
        return
    end
    local query = params.query
    if not query then
        vc_alert("vc-aicx: missing ?query=", 2)
        return
    end
    local project = params.project
    local proj_part = project and string.format(" --project %q", project) or ""
    local full = vc_wrap_with_read_tail(
        string.format("aicx search %q%s", query, proj_part)
    )
    if vc_iterm_run_in_new_tab(full) then
        vc_alert("🔎 aicx " .. query, 1)
    else
        vc_alert("vc-aicx: iTerm2 spawn failed", 2)
    end
end)

-- ============================================================================
-- Plan A handler: vc-ping (sanity test)
-- hammerspoon://vc-ping?msg=hello
-- ============================================================================

hs.urlevent.bind("vc-ping", function(eventName, params)
    -- vc-ping is the only handler that accepts arbitrary `msg=` for
    -- operator-visible debugging — still goes through sanitizer.
    if not vc_params_valid(params, {"msg", "hello"}) then
        vc_alert("vc-ping: invalid params", 2)
        return
    end
    vc_alert("🟢 vc-ping ok — scheme handler aktywny", 2)
    vc_log(string.format("vc-ping params: %s", hs.inspect(params)))
end)

-- ============================================================================
-- Plan 11 handler: vc-atlas
-- hammerspoon://vc-atlas?card=<id>&project=<abs-path>
--
-- Resolves <project>/.loctree/context-atlas/<card-id>.md and opens it in
-- the preferred editor. If <project> is omitted, opens the card relative
-- to the current working directory of Hammerspoon (operator's $HOME).
-- ============================================================================

hs.urlevent.bind("vc-atlas", function(eventName, params)
    if not vc_params_valid(params, {"card", "project"}) then
        vc_alert("vc-atlas: invalid params", 2)
        return
    end
    local card = params.card
    if not card then
        vc_alert("vc-atlas: missing ?card=", 2)
        return
    end
    local project = params.project
    local atlas_path
    if project then
        atlas_path = string.format(
            "%s/.loctree/context-atlas/%s.md", project, card
        )
    else
        atlas_path = string.format(".loctree/context-atlas/%s.md", card)
    end
    if vc_open_in_editor(atlas_path) then
        vc_alert("📚 atlas/" .. card, 1)
    else
        vc_alert("vc-atlas: open failed", 2)
    end
end)

-- ============================================================================
-- Plan 11 handler: vc-prism
-- hammerspoon://vc-prism?task=task1,task2,task3&project=<abs-path>
--
-- Spawns `loct prism --task=<csv>` in a new iTerm2 tab. The task value
-- accepts comma-separated identifiers (the safe charset allows `.` and `+`
-- but `,` is excluded — we accept the CSV via `+` separator and translate).
-- Operators emit hyperlinks with `task=alpha+beta+gamma` which becomes
-- `--task=alpha,beta,gamma` on the CLI.
-- ============================================================================

hs.urlevent.bind("vc-prism", function(eventName, params)
    if not vc_params_valid(params, {"task", "project"}) then
        vc_alert("vc-prism: invalid params", 2)
        return
    end
    local task = params.task
    if not task then
        vc_alert("vc-prism: missing ?task=", 2)
        return
    end
    local task_csv = task:gsub("%+", ",")
    local project = params.project
    local cd_part = project and string.format("cd %q && ", project) or ""
    local full = vc_wrap_with_read_tail(
        string.format("%sloct prism --task=%q", cd_part, task_csv)
    )
    if vc_iterm_run_in_new_tab(full) then
        vc_alert("🔮 prism " .. task_csv, 1)
    else
        vc_alert("vc-prism: iTerm2 spawn failed", 2)
    end
end)

-- ============================================================================
-- Plan 11 handler: vc-marbles
-- hammerspoon://vc-marbles?repo=<abs-path>&iteration=<NN>
--
-- Spawns a marbles run in a new iTerm2 tab. The iteration param is
-- informational (operator-readable label); the CLI itself reads marbles
-- state from `.vibecrafted/`. Default agent: vibecrafted (matches the
-- /vc-marbles skill dispatcher default).
-- ============================================================================

hs.urlevent.bind("vc-marbles", function(eventName, params)
    if not vc_params_valid(params, {"repo", "iteration", "agent"}) then
        vc_alert("vc-marbles: invalid params", 2)
        return
    end
    local repo = params.repo
    if not repo then
        vc_alert("vc-marbles: missing ?repo=", 2)
        return
    end
    local agent = params.agent or "vibecrafted"
    local iteration_label = params.iteration and (" # iter=" .. params.iteration) or ""
    local cmd = string.format(
        "cd %q && vibecrafted marbles %s%s",
        repo, agent, iteration_label
    )
    local full = vc_wrap_with_read_tail(cmd)
    if vc_iterm_run_in_new_tab(full) then
        vc_alert("🪨 marbles " .. agent, 1)
    else
        vc_alert("vc-marbles: iTerm2 spawn failed", 2)
    end
end)

-- ============================================================================
-- Plan 11 handler: vc-followup
-- hammerspoon://vc-followup?repo=<abs-path>
--
-- Resolves the latest marbles report in
-- <repo>/.vibecrafted/reports/marbles/<YYYY_MMDD>/ and opens it in the
-- preferred editor. If multiple dated directories exist, picks the
-- lexicographically greatest (YYYY_MMDD sorts correctly).
-- ============================================================================

hs.urlevent.bind("vc-followup", function(eventName, params)
    if not vc_params_valid(params, {"repo"}) then
        vc_alert("vc-followup: invalid params", 2)
        return
    end
    local repo = params.repo
    if not repo then
        vc_alert("vc-followup: missing ?repo=", 2)
        return
    end
    -- Resolve latest dated dir + latest report file inside it.
    -- We shell out for the listing (operators' repos can be huge — keep
    -- the Lua side cheap).
    local cmd_resolve = string.format(
        "ls -1 %q/.vibecrafted/reports/marbles 2>/dev/null | sort -r | head -1",
        repo
    )
    local latest_date, ok1 = hs.execute(cmd_resolve)
    if not ok1 or not latest_date or latest_date == "" then
        vc_alert("vc-followup: no marbles reports found", 2)
        return
    end
    latest_date = latest_date:gsub("%s+$", "")
    -- `latest_date` is a directory name from `ls`; it is interpolated with
    -- %s (unquoted) into the shell command below, so a name carrying shell
    -- metacharacters would be command injection. Date dirs are `YYYY_MMDD`
    -- (alnum + underscore) — reject anything else before shelling out.
    if not latest_date:match("^[%w_]+$") then
        vc_alert("vc-followup: invalid date dir name", 2)
        return
    end
    local cmd_file = string.format(
        "ls -1 %q/.vibecrafted/reports/marbles/%s/*.md 2>/dev/null | sort -r | head -1",
        repo, latest_date
    )
    local latest_file, ok2 = hs.execute(cmd_file)
    if not ok2 or not latest_file or latest_file == "" then
        vc_alert("vc-followup: no .md report in " .. latest_date, 2)
        return
    end
    latest_file = latest_file:gsub("%s+$", "")
    if vc_open_in_editor(latest_file) then
        vc_alert("🪨📜 " .. latest_date, 1)
    else
        vc_alert("vc-followup: open failed", 2)
    end
end)

-- ============================================================================
-- Boot banner
-- ============================================================================

print("=== Vetcoders URL handlers załadowane ===")
print("Plan A:   vc-ping, vc-open-file, vc-loct, vc-aicx")
print("Plan 11:  vc-atlas, vc-prism, vc-marbles, vc-followup")
print("Test:  open 'hammerspoon://vc-ping?msg=hello'")
hs.alert.show("vc-* URL handlers (8) aktywne", 2)
