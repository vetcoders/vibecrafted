# Public-surface floor

The release pipeline has to be able to answer, for any product it ships:

> Does every public surface say the same version, and can a stranger install the thing today from what we publish?

`scripts/version_bump.py --check` only compares files inside the checkout.
`vibecrafted doctor --release` only compares Vibecrafted's `VERSION` to the latest GitHub Release and the Release source gate. Neither one looks at npm, crates.io, PyPI, Homebrew taps, the installer channel, site metadata, a README install section, brand spelling, or a tag that was pushed without a GitHub Release. That is the gap this floor closes. It is one probe with three stage owners, configured per product. It does not tag, publish, or deploy.

```bash
vc-dou --floor                         # this checkout, product vibecrafted
vc-dou --floor --product loctree       # live surfaces for another product
vc-hydrate --floor                     # checkout only; no network
vc-release --floor                     # ship preflight
vc-release --floor --require-release   # the Release object must exist
```

The same entry point is `python -m vibecrafted_core.public_surface {dou|hydrate|release}`.
Config is `packaging/public-surfaces/<product>.toml`, or `--config PATH`.

Exit `0` is a pass. Warnings are still printed and are not a silent green: an unproven registry says `unproven, not a green pass`. Exit `1` means at least one check at `fail` for this stage. Exit `2` is a bad config. `--acknowledge <surface-id>` downgrades that surface's fail to a warning and keeps it in the report; the install call-to-action stays blocked.

`install_cta: yes` is allowed only when the README (when this checkout is the product) has an install command and every surface marked `install` agrees. Otherwise the report says `install_cta: no`. Do not tell anyone to install while that line says no.

## Who checks what

| Check                                                                     | vc-dou                                       | vc-hydrate  | vc-release                                                                                                         |
| ------------------------------------------------------------------------- | -------------------------------------------- | ----------- | ------------------------------------------------------------------------------------------------------------------ |
| Live registries, channel, site version, GitHub Release, Homebrew          | compare, using each surface's `dou` severity | not fetched | compare, using each surface's `release` severity                                                                   |
| Tag with no GitHub Release                                                | `fail` unless the product says `warn`        | —           | `warn` by default. The publisher creates the Release for the version it is cutting; older orphan tags stay visible |
| GitHub Release is not the reference version yet                           | `fail`                                       | —           | `warn`, with the sentence that a tag alone does not finish the stage. `--require-release` turns it into `fail`     |
| In-repo projections (`version_bump`, pyproject, plugin, README badge)     | `fail` when this checkout is the product     | `fail`      | `fail` when this checkout is the product                                                                           |
| README install section and command pins                                   | `fail`                                       | `fail`      | `fail`                                                                                                             |
| README prose that names a different version                               | `warn`                                       | `warn`      | `warn`                                                                                                             |
| Brand spelling in the checkout (capital C inside Vetcoders)               | `fail` on the configured globs               | `fail`      | `fail`                                                                                                             |
| Brand spelling on a live page (the fetched HTML, not every script bundle) | `warn`                                       | not fetched | `warn`                                                                                                             |
| Registry that cannot be reached                                           | `warn` (unproven)                            | —           | `fail` when that surface's release severity is `fail`                                                              |

A product file overrides any of those severities with `dou = "fail"|"warn"|"ignore"` and `release = "..."`. `install = true` means a stranger might actually install from that surface, so disagreement blocks `install_cta` even when the severity is only a warning (Homebrew taps are the usual case).

`local_root_file` decides whether the checkout in `--repo` belongs to this product. Auditing Loctree from the Vibecrafted tree does not treat Vibecrafted's README as Loctree's.

## Expect

- `file:VERSION` — compare the world to this checkout. Right for a product whose repo is the thing being cut.
- `agree` — there is no checkout, or the checkout is not the stranger-facing publisher. Every live version must be the same number. Right for Loctree, AICX, Pensieve, Screenscribe when you are not standing in their trees.
- `literal:1.2.3` — a fixed reference.
- `surface:<id>` — one live surface is the reference and the others are compared to it.

## Adding a product

Copy `packaging/public-surfaces/screenscribe.toml`. Name only the registries that product actually publishes. AICX must not grow an npm or PyPI surface: those names belong to other people. Set `install = true` on the surfaces a stranger is told to use. The canonical brand spelling is `Vetcoders`. The forbidden form is the same word with a capital C; put that exact spelling in `brand.forbidden` and in `brand_html` surfaces.

## What a red floor does not do

It does not bump `VERSION`, cut a tag, open a GitHub Release, or deploy a site. Those stay operator buttons. `scripts/publish-vibecrafted-release.sh` runs `vc-release --floor` and stops when the floor exits `1`, before it creates a draft. The missing Release for the tag being published is allowed at that moment (`--allow-missing-release`); everything else is not.
