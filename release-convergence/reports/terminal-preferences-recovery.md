# Terminal preferences recovery

## Replayed preference contract

`terminal-policy.toml` is the product-owned, user-editable policy imported by
the generated `vc-terminal/vc-terminal.toml` wrapper. The installer now stores
only the identities of explicit `window.blur`, `window.opacity`,
`window.decorations`, and `font.*` overrides in its runtime receipt. It never
stores preference values. This preserves an explicit choice through an upgrade
whose shipped default temporarily matches it, and through later default
changes.

The regression replay starts from one shipped policy, writes Founder chrome and
font choices, then installs two different shipped policies. It proves the
chosen chrome and font family remain present, the receipt is value-redacted,
and runtime resolution remains ready.

## Font ownership dependency

The parent `Vibecrafted.app` currently calls
`CTFontManagerRegisterFontsForURL(..., .session, ...)` immediately before it
launches the `vc-terminal.app` child. The release builder puts the bundled
`SpotMono.ttc` in the parent app's Resources, while the actual consuming
process is the helper's `Contents/MacOS/alacritty` executable. A parent
`.process` registration would not reach that child; retaining `.session` can
shadow an identically named font from `~/Library/Fonts` for the login session.

Required bounded follow-up in the sibling `vc-terminal` repository:

1. Place the fallback font in `vc-terminal.app/Contents/Resources`.
2. Have the Alacritty/vc-terminal process itself prefer an installed `Spot Mono`
   family and register that resource only as a process-scoped fallback when no
   installed family resolves.
3. Once that child-process contract is tested, remove the parent-app session
   registration and its parent resource copy from Vibecrafted's release path.

No installed app, user font, user preference, or live process was changed by
this source-only cut. The observed matching hashes establish duplicate bytes,
not deletion or a historical overwrite of blur/opacity.
