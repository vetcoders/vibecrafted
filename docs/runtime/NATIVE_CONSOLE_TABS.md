# Native console navigation: Home, unified toolbar, AppKit tabs

Scope: the macOS shell (`vibecrafted-app/shell-agent/app/Vibecrafted`). The
server keeps its global sidebar and the Scaffold single-document studio; the
App owns how those pages are framed, navigated and kept apart.

## Ownership

| Surface                                      | Owner                                             | File                                                    |
| -------------------------------------------- | ------------------------------------------------- | ------------------------------------------------------- |
| Window shape, tab group, toolbar bridging    | `CommandDeckWindowFactory`                        | `Views/MainWindowController.swift`                      |
| Console tab (one, retained across close)     | `MainWindowController` + `AppDelegate.webSession` | `Views/MainWindowController.swift`, `AppDelegate.swift` |
| Tool / reference tabs, dedupe, close         | `NativeTabCoordinator`                            | `CommandDeck/NativeTabCoordinator.swift`                |
| One `WKWebView` per tab, history, load state | `WebConsoleSession`                               | `CommandDeck/WebConsoleHost.swift`                      |
| Route vs response decisions (pure)           | `WebNavigationPolicy`                             | `CommandDeck/WebNavigationPolicy.swift`                 |
| Registered destinations (pure)               | `ToolDestination`                                 | `CommandDeck/ToolDestinations.swift`                    |
| Chrome: navigation, status, actions          | `CommandDeckToolbar`                              | `CommandDeck/CommandDeckView.swift`                     |

The runtime, supervisor, terminal and repair engine stay with `AppDelegate`.
Tabs never start a service, and closing a tab touches nothing but that tab.

## Chrome

There is exactly one chrome per window: the SwiftUI `.toolbar` is bridged
into the unified titlebar with `NSHostingView.sceneBridgingOptions = [.toolbars]`
(macOS 14). It carries Back (⌘[), Forward (⌘]), Home (⇧⌘H), the runtime
status badge (phase · host · loading), Retry Connection (⌘R), Repair Runtime
(⇧⌘R), Open Terminal (⌥⌘T), Diagnostics and Open in Browser. At compact widths
AppKit moves items into the toolbar overflow menu; the item set does not change.

The recovery card keeps one primary verb for its phase (Retry, or Repair when
blocked) plus Stop Runtime. Keyboard shortcuts live in the toolbar only, so no
shortcut is bound twice in one window.

Home resolves to the product overview (`/`) of the _current_ runtime endpoint.
It never hardcodes a host or port and never restarts or reinstalls anything.
It stays valid while the page shows an error or a machine document; without a
runtime it records the wish and the next applied endpoint lands on Home.

Where Home returns is owned by whoever opens the tab (`WebTabHome`), never by
the URL that happened to open it. The console and every tool tab opened from a
link inside the product (`target=_blank`, including a raw API endpoint) return
to `/`; a destination returns to its own route (the Loctree report to
`/structure/report`), a configured service to its configured path, and a local
document tab re-presents its one file. Home is a real load on the tab's
origin, so Back still reaches the page it left.

A read-only reference view (JSON or text diverted from an ordinary link) runs
no script, so it cannot render the product overview itself. Home from it is
answered by the coordinator (`NativeTabCoordinator.openProductOverview()`):
the interactive overview tool tab on `/` of the current runtime is focused if
it is open and opened otherwise, through the same route, dedupe key and
origin check as any link-opened tool tab. The reference view keeps its
document, role, no-script setting and history; the console and every sibling
tab stay where they were. Without a runtime there is no overview to show, so
Home opens nothing and the next reconnect re-presents the document.

## Tabs

Every tab is an `NSWindow` in one tab group (`tabbingIdentifier`
`io.vetcoders.vibecrafted.command-deck`), so the native tab bar, Window menu
(Show Previous/Next Tab, Merge All Windows) and ⇧⌘{ / ⇧⌘} apply. Roles:

| Role        | Purpose                                                      | Scripts | Opens tabs                                |
| ----------- | ------------------------------------------------------------ | ------- | ----------------------------------------- |
| `console`   | the product console, keeps the runtime session               | yes     | yes (tool/reference)                      |
| `tool`      | interactive page opened via `target=_blank` or a destination | yes     | no (hands `_blank` to the system browser) |
| `reference` | read-only machine document (JSON, text)                      | no      | no                                        |

A destination or URL that is already open is focused, never duplicated.

Every tab also has a **scope**, fixed at creation, that a page can never widen:

| Scope           | Confined to                                         | Follows the runtime endpoint | Website data store                                 |
| --------------- | --------------------------------------------------- | ---------------------------- | -------------------------------------------------- |
| `runtime`       | the caretaker-resolved runtime origin               | yes                          | shared (console) or ephemeral for isolated content |
| `localDocument` | exactly one local HTML file                         | no                           | ephemeral                                          |
| `service`       | the origin of a console configured in `config.toml` | no                           | ephemeral                                          |

No tab of any role or scope has a JavaScript-to-native channel: there is no
`WKScriptMessageHandler` anywhere in the App.

## Route versus response

`WebNavigationPolicy.decide` still admits every same-origin route (a UI path
and an API path are indistinguishable up front). The _response_ is where the
two are told apart: a main-frame response in the console whose MIME is not an
interactive document (`text/html`, XHTML, SVG, PDF, images, media) is
cancelled before commit and shown in a reference tab. The console document,
its DOM edit state and its history are untouched. Sub-frames, `fetch` and
form POSTs (which redirect back to HTML) are not affected.

`target=_blank` / `window.open` never creates a WebKit view: same-origin pages
become a tool tab, foreign http(s) goes to the system browser, everything else
is refused. WebKit reports our own `.cancel` as `WebKitErrorDomain` 102; the
session treats that as a decision, not a failure.

## Destinations

| Destination         | Contract used                                                                                                                                                                                                                                                                                                                                                                | When unavailable                                                                                                                                                                           |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Loctree Report      | `/structure/report` on the connected runtime (redirects to the directory-style `/structure/report/`). The server serves `<root>/.loctree/report.html` of a canonical run root under a CSP `sandbox` (opaque origin, no `fetch`, no forms) with its sibling assets on `/structure/report/{asset}` and an in-memory Web Storage stand-in; the App adds an ephemeral data store | no runtime → menu item disabled with reason; no report → tab shows "Server returned HTTP 404" and the server names `loct report --output .loctree/report.html`                             |
| AICX Dashboard      | `aicx dashboard` output at `~/.aicx/aicx-dashboard.html` (or `$AICX_HOME`), loaded with read access to that one file, non-persistent data store                                                                                                                                                                                                                              | file missing → reason names the path and `aicx dashboard`                                                                                                                                  |
| Slack Agent Console | `[tools.slack-console] url` in `~/.config/vibecrafted/config.toml` (the operator-owned file that also holds `[server]`; `XDG_CONFIG_HOME` honoured). Opens in a `service`-scoped tab on that URL's origin                                                                                                                                                                    | not configured → reason names the owner (`vc-slack-agent` portal `/console`, `make portal-preview` :4300 or its deploy) and the file; invalid table → reason quotes the contract violation |

`View ▸ Open in Tab` and `View ▸ Open in Browser` list the catalog. Local
documents use Reveal in Finder for the external action, through the typed
native bridge and its home-directory root.

### Configured tool consoles

Consoles owned outside this repository are never guessed. The operator names
the served surface once, in the one product config file:

```toml
# ~/.config/vibecrafted/config.toml
[server]
bind_host = "127.0.0.1"
port = 3024
public_url = "http://127.0.0.1:3024"

[tools.slack-console]
url = "http://100.82.232.70:4300/console"   # what `make portal-preview` (or the deploy) serves
```

`vibecrafted_core.server_config.load_tool_destinations` and the App's
`ToolDestinationConfiguration` apply one contract: known keys only
(`slack-console`), one `url` per key, `http(s)` with a host, no credentials,
query or fragment. An empty `url` means "not configured". The tab is scoped to
that URL's origin: same-origin navigation stays, foreign http(s) leaves through
the system browser, the runtime endpoint has no influence on it, and it shares
nothing with the console's website data store. ATS applies as for the runtime:
cleartext `http` is admitted for loopback / local-network IP literals only.

### Server-side boundaries the tabs rely on

- `/structure/report/` and `/structure/report/{asset}` (`vibecrafted-server/web/src/tools.rs`;
  `/structure/report` answers `308` to the slash form): the document is served
  at a directory-style URL so Loctree's relative `<script src="loctree-*.js">`
  resolve onto the asset route. The report's own
  `<meta http-equiv="Content-Security-Policy">` is removed and replaced by a
  header policy — `sandbox allow-scripts allow-popups`, scripts from the page
  and from `<host>/structure/report/` only, `connect-src 'none'`,
  `form-action 'none'`, `frame-ancestors 'none'`. Because the sandboxed
  document has an opaque origin, `window.localStorage` throws there; the
  server installs a per-document in-memory stand-in as the first script so the
  report's tab wiring and theme toggle run — one independent storage per name
  (`localStorage` and `sessionStorage` never see or clear each other; proven
  by `web/tests/acceptance/storage_shim_probe.mjs`, run from the crate's unit
  tests when `node` is present). Nothing persists and `allow-same-origin` is
  never granted. Assets are regular files in the
  report's directory with a known extension; symlinks and traversal are
  refused. The document keeps its interactive graph and holds no control-plane
  authority.
- `/api/aicx/search` and `/api/aicx/reference`: the AICX corpus is private to
  the host. Both routes require a verified local peer — loopback, or the same
  interface the listener is bound to (a tailnet bind reached from this
  machine) — and fail closed when the peer is unknown. Search runs the
  installed `aicx` CLI with a bounded argv (`--json --no-semantic --limit 12`,
  optional exact `owner/repo`), a timeout, and projects hits to identity, date,
  snippets and a server-owned reference route; raw paths and diagnostics never
  leave the host. The reference route serves one authorised extract (regular
  file under `$AICX_HOME/extracts`, text extension, 256 KiB cap) as
  `text/plain`, which the console diverts into a read-only reference tab.

## Proof

`tests/tui/test_command_deck_integration.py` compiles the CommandDeck sources
with `swiftc` and drives a real `WKWebView` against a loopback fixture:
endpoint link and `_blank` keep the Scaffold DOM/edit state/history; tab
dedupe and tab-group membership; Home/back isolation in both directions;
closing a tab keeps the console window and web view; unavailable destinations
open nothing; a local document tab shows only its file; the report tab uses an
ephemeral store; a configured service tab opens without a runtime, stays on
its origin, hands foreign links to the system browser and ignores runtime
loss; runtime loss is shown on runtime tabs; the bridged toolbar exists with a
stable item set at 800 px and 1200 px; Home ownership: a `target=_blank` raw
endpoint tab returns from JSON and from an HTTP error to the runtime overview
on the same origin (and on a replacement endpoint) with Back/Forward intact
and the console untouched, a foreign origin gets no tab, the report
destination returns to the report; an ordinary API link diverted into a
reference view → Home opens the interactive overview tab on `/` of the same
origin (rendered, online, selected in the tab group), a second Home focuses
that one tab instead of a twin, an overview tab navigated to `/runs` or into
an HTTP error is brought back to `/` in place with its Back history kept, the
reference view keeps its document,
no-script setting and empty history, the console keeps its edit state, and
without a runtime Home opens nothing while the reconnect re-presents the
document.
`vibecrafted-server/web/tests/tools_http.rs`
proves the server boundaries (sandbox headers, asset confinement, peer gate,
argv, projection, timeout, reference authorisation).
