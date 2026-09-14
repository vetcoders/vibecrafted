---
title: "Common issues"
description: "A failure playbook: symptom, diagnose command, and fix for the most frequent install and runtime problems."
section: troubleshooting
order: 20
---

# Common issues

Most Vibecrafted problems are drift problems: the launcher, the config, or the installed runtime no longer matches what you think is live. This playbook covers each frequent failure as symptom → diagnose → fix. When in doubt, start with `vibecrafted doctor`.

## `vibecrafted: command not found`

**Symptom.** A fresh terminal cannot find the CLI even though the install finished cleanly.

**Diagnose.**

```bash
command -v vibecrafted
ls ~/.local/bin/vibecrafted
```

**Fix.** Ordinary shell startup only needs the launcher directory on `PATH` (the installer can add this guarded line with your consent). Add it to the rc file you use:

```bash
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
exec $SHELL -l
```

If old startup lines are the problem, `vibecrafted doctor --fix-rc` repairs them.

## Launcher resolves to a stale generation

**Symptom.** You updated (or pulled runtime changes into a checkout and reinstalled), but behavior did not change; finished runs miss new finish hooks; the version stamp lags the intended HEAD.

**Diagnose.**

```bash
vibecrafted version
cat ~/.local/share/vibecrafted/tools/vibecrafted-current/VERSION
readlink ~/.local/share/vibecrafted/tools/vibecrafted-current
git rev-parse --short HEAD        # in your checkout — compare with the +g<sha> stamp
vibecrafted doctor                # fails if the launcher resolves outside the installed root
```

**Fix.** Re-run the install so a fresh generation is published and the pointer swaps:

```bash
make install          # or: make install-auto / vibecrafted update
vibecrafted doctor --fix-launchers   # if wrappers themselves are stale
```

Remember: the daily CLI runs the installed generation, never the git checkout. Editing the checkout changes nothing until you reinstall.

## Checkout-linked config detected

**Symptom.** `vibecrafted doctor` fails with active config, KDL, helper, or command-deck content referencing a source checkout — usually after hand-editing installed files or copying config from a repo.

**Diagnose.**

```bash
vibecrafted doctor --verbose
```

**Fix.** Never patch installed files by hand. Re-run the installer so config is regenerated inside the current generation and re-hashed into `runtime-manifest.json`:

```bash
make install
vibecrafted doctor
```

Installed artifacts must never point at a repository checkout — that rule is the [installed-over-checkout doctrine](/docs/configuration/), and publication itself fails on violations.

## Dirty-provenance receipt after manual copies

**Symptom.** `vibecrafted receipt` reports `DIRTY_BUILD_PROVENANCE`, `UNPUSHED`, or `SOURCE_AHEAD_OF_INSTALLED` for a fleet tool after someone copied a binary into place or installed from an uncommitted tree.

**Diagnose.**

```bash
vibecrafted receipt
vibecrafted receipt --json     # exact drift verdict per tool
```

**Fix.** Rebuild and reinstall the tool from a clean, pushed state of its source repo, then re-check until the row reads `CLEAN`. If the receipt cannot find the source checkout at all, point it explicitly:

```bash
VIBECRAFTED_FLEET_ROOT="$HOME/projects" vibecrafted receipt
```

`INSTALLED_NOT_ON_PATH` means `PATH` resolves a different binary than the installed one — check `command -v <tool>` against the installed path.

## Docker: doctor reports missing foundations

**Symptom.** In the light Docker image, the command deck works but `doctor` reports missing foundation binaries.

**Diagnose.**

```bash
docker run --rm -it -v "$PWD:/workspace" vetcoders/vibecrafted:local doctor
```

**Fix.** Build the full image, which installs agent CLIs and foundations at build time:

```bash
docker build --build-arg INSTALL_AGENT_CLIS=true --build-arg INSTALL_FOUNDATIONS=true \
  -t vetcoders/vibecrafted:full .
docker run --rm -it -v "$PWD:/workspace" vetcoders/vibecrafted:full doctor
```

Two more Docker specifics: agent CLIs still need their own credentials — mount only the config stores you intend the container to use (`-v "$HOME/.codex:/root/.codex"` etc.); and set `VIBECRAFTED_DOCKER_SEED_SKILLS=0` if you mount your own runtime store and do not want first-run skill seeding into `/workspace/.vibecrafted`.

## Install environment problems

| Symptom                                                  | Fix                                                                                                                            |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `make: command not found` / `python3: command not found` | The installer pre-flight prints the exact `apt`/`dnf`/`pacman`/`brew` command — run it and re-run the install                  |
| TLS errors behind a corporate proxy                      | Set `HTTPS_PROXY` / `HTTP_PROXY` before running `install.sh`; curl and `uv` both honor them                                    |
| Permission errors on `~/.vibecrafted/`                   | The installer never uses `sudo`; make sure the directory is writable by your user, or set `VIBECRAFTED_HOME` to a path you own |
| WSL2 TLS failures after Windows sleep                    | Clock skew — run `sudo hwclock -s` and retry                                                                                   |

## Still stuck

Attach the following to any bug report:

```bash
vibecrafted version
vibecrafted doctor --verbose
vibecrafted receipt --json
```

Those three outputs identify almost every install-state problem without back-and-forth. See [Doctor](/docs/doctor/) for how to read them.
