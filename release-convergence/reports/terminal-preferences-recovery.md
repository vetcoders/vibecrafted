# Terminal preferences recovery

## Replayed preference contract

`terminal-policy.toml` is the product-owned, user-editable policy imported by
the generated `vc-terminal/vc-terminal.toml` wrapper. Its repository source is
`config/vc-terminal/vibecrafted.toml`. The installer stores only the identities
of explicit `window.blur`, `window.opacity`, `window.decorations`, and `font.*`
overrides in its runtime receipt. It never stores preference values. This
preserves an explicit choice through an upgrade whose shipped default
temporarily matches it, and through later default changes.

The regression replay starts from one shipped policy, writes Founder chrome and
font choices, then installs two different shipped policies. It proves the
chosen chrome and font family remain present, the receipt is value-redacted,
and runtime resolution remains ready.

## Shipped chrome default

Merge ownership alone did not deliver the product default. The shipped policy
still carried `decorations = "Transparent"`, so a fresh install produced an OS
titlebar with traffic lights on top of the chrome vc-frame already draws. The
canonical default is now:

| setting                          | value                 |
| -------------------------------- | --------------------- |
| `window.decorations`             | `"None"` (borderless) |
| `window.blur`                    | `true`                |
| `window.opacity`                 | `0.9`                 |
| `font.normal/bold/italic.family` | `"Spot Mono"`         |

`window.padding` moved from `{ x = 8, y = 24 }` to `{ x = 8, y = 8 }`. The
asymmetric top inset existed only to keep transparent title controls off the
grid; with no titlebar it was a workaround outliving its cause. This one is an
implementation judgement, not a Founder-stated value.

Three installer regressions in
`tests/tui/test_runtime_resolution_publication.py` hold the contract: a fresh
install lands the four product values; an upgrade moves an untouched default
forward; an upgrade preserves an explicit `decorations` answer while chrome the
owner never answered for still follows the product.

## Font ownership

The parent `Vibecrafted.app` called
`CTFontManagerRegisterFontsForURL(..., .session, ...)` immediately before
launching the terminal. `.session` is the login session, not the process. The
consequence was measurable rather than theoretical: on a host with the App
installed, a plain unrelated process asking CoreText for `Spot Mono` answered

```
matching_urls: ["/Applications/Vibecrafted.app/Contents/Resources/fonts/SpotMono.ttc",
                "/Users/<owner>/Library/Fonts/SpotMono.ttc"]
resolved_url:  "/Applications/Vibecrafted.app/Contents/Resources/fonts/SpotMono.ttc"
```

The App's copy reached processes it does not own, and outranked the owner's own
installed file.

The replacement is the route Apple documents for a consuming app:
`ATSApplicationFontsPath` in that app's `Info.plist`, naming a directory under
`Contents/Resources`.

https://developer.apple.com/documentation/bundleresources/information-property-list/atsapplicationfontspath

`vc-terminal-product-entry.sh` execs
`vc-terminal.app/Contents/MacOS/alacritty`, so the bundle that declares the key
is the main bundle of the process that draws the glyphs. The release builder's
`embed_terminal_font_resources` installs the licensed `SpotMono.ttc` into that
bundle's `Contents/Resources/fonts` and sets the key, before any signature is
spent. The parent app registers nothing and no longer carries an unreferenced
copy in its own Resources.

### Measured, not assumed

An isolated probe (`tests/tui/fixtures/font_probe.m`, driven by
`tests/tui/test_terminal_font_ownership.py`) builds throwaway app bundles in a
temporary directory and asks CoreText from inside a real consuming process. On
macOS 27:

- **A family the host does not have.** Same font bytes in `Contents/Resources`
  in both bundles. Without the key: `matching_urls: []`, and
  `CTFontCreateWithName` substitutes Helvetica. With the key: the family
  resolves to the bundle's own file. A user who never installed Spot Mono gets
  Spot Mono.
- **No global side effect.** The control bundle, asked again after the
  declaring process ran, still sees nothing. The registration is private to the
  declaring process — the opposite of `.session`.
- **A family the host already has.** The bundled copy joins the process's
  matching set, but resolution still returns the installed file. The bundled
  font is a fallback and never shadows what the owner installed.

Source-level assertions in the same file keep the parent app free of
`CTFontManagerRegisterFontsForURL` and keep the builder binding the font into
the terminal bundle.

No installed app, user font store, user preference, or live process was
modified. Only font files were read.

## Outstanding

The standalone Runtime Pack materializes its own
`libexec/vc-terminal.app` outside this cut. That bundle needs the same call —
see the integration note in the implementation report — and the builder's
licensed-font precondition currently runs only when `MODE != runtime-pack`.

𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. with AI Agents by Vetcoders (c)2024-2026 LibraxisAI
