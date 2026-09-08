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

| Destination         | Contract used                                                                                                                                   | When unavailable                                                                        |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Loctree Report      | `/structure/report` on the connected runtime (served by the WEB-contract cut; the baseline server answers 404)                                  | no runtime → menu item disabled with reason; 404 → tab shows "Server returned HTTP 404" |
| AICX Dashboard      | `aicx dashboard` output at `~/.aicx/aicx-dashboard.html` (or `$AICX_HOME`), loaded with read access to that one file, non-persistent data store | file missing → reason names the path and `aicx dashboard`                               |
| Slack Agent Console | none; `vc-slack-agent` only has a dev server (`make portal`, :4300)                                                                             | always unavailable, reason shown; no port is guessed                                    |

`View ▸ Open in Tab` and `View ▸ Open in Browser` list the catalog. Local
documents use Reveal in Finder for the external action, through the typed
native bridge and its home-directory root.

## Proof

`tests/tui/test_command_deck_integration.py` compiles the CommandDeck sources
with `swiftc` and drives a real `WKWebView` against a loopback fixture:
endpoint link and `_blank` keep the Scaffold DOM/edit state/history; tab
dedupe and tab-group membership; Home/back isolation in both directions;
closing a tab keeps the console window and web view; unavailable destinations
open nothing; a local document tab shows only its file; runtime loss is shown;
the bridged toolbar exists with a stable item set at 800 px and 1200 px.
