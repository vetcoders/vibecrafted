.DEFAULT_GOAL := help

PYTHON   ?= $(CURDIR)/scripts/project-python
INSTALLER := scripts/vetcoders_install.py
GUI_INSTALLER := scripts/installer_gui.py
MANIFEST := install.toml
INSTALLER_DIR := scripts/installer
SHELL_INSTALLER := vibecrafted-core/vibecrafted_core/runtime/scripts/install-shell.sh
SOURCE   := $(CURDIR)
BRANCH   ?= main
VERSION_FILE := VERSION
RUNTIME ?= none
INSTALL_SERVER_SERVICE_POLICY ?= ensure
INSTALL_TOOLS_SERVICE_POLICY ?= preserve
INSTALLER_CACHE_HOME ?= $(if $(XDG_CACHE_HOME),$(XDG_CACHE_HOME),$(HOME)/.cache)
INSTALLER_HOST_TAG := $(shell uname -s | tr '[:upper:]' '[:lower:]')-$(shell uname -m)
UV_PROJECT_ENVIRONMENT ?= $(INSTALLER_CACHE_HOME)/vibecrafted/venvs/installer-$(INSTALLER_HOST_TAG)
CARGO_BUILD_ROOT ?= $(INSTALLER_CACHE_HOME)/vibecrafted/build/$(INSTALLER_HOST_TAG)
# Shared source mounts (sshfs skews mtimes) can serve another host's stale
# __pycache__ as valid bytecode; route bytecode to a per-host cache so the
# in-tree cache is never read or written by install lanes.
export PYTHONPYCACHEPREFIX ?= $(INSTALLER_CACHE_HOME)/vibecrafted/pycache-$(INSTALLER_HOST_TAG)

# Make is a shell caller: resolve the staged runtime through the shell throne,
# never through a Python import or a literal XDG path. Keep the existence check
# separate because install-tools-held computes the destination before staging it.
define RESOLVE_STABLE_RUNTIME_ROOT
. "$(SOURCE)/scripts/lib/runtime-roots.sh"; stable_root="$$(default_vibecrafted_tools_home)/vibecrafted-current"
endef

define REQUIRE_STAGED_RUNTIME_ROOT
if [ ! -d "$$stable_root/vibecrafted-core" ]; then \
	printf "✗ current tools root drift: staged runtime missing at %s\n" "$$stable_root" >&2; \
	pause_runtime_contract_failure; \
	exit 1; \
fi
endef

.PHONY: help help-dev vibecrafted app dmg dmg-signed release-local notarize release runtime-pack portable publish-release release-rehearsal gui-install wizard wizard-dev check test test-core test-skills test-install test-parity test-vc-frame test-iterm2-migrate test-memex test-aicx-sync test-hammerspoon test-keychain-session dispatch-test unified-product-contract-gate exact-release-contract-gate release-version-gate payload-hygiene install install-source install-auto install-all install-python-tools install-bundle-tools install-tools install-tools-held install-vendored-binaries install-app-binaries install-hammerspoon skills helpers setup-dev dry-run doctor list update uninstall restore migrate migrate-dry init-hooks seed-commit-msg-hooks bundle bundle-check foundations foundations-check semgrep version version-show version-bump bump-patch bump-minor bump-major iterm-plugin iterm-plugin-refresh iterm-plugin-show iterm-plugin-uninstall iterm-plugin-migrate demo demo-full commit-safe test-race-protection skill-new server server-build build-server-release server-check server-test install-server install-server-payload install-server-service server-smoke

help:
	@printf "\n"
	@printf "  \033[1m\033[38;5;173m⚒  𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. %s\033[0m\n" "$$(cat $(VERSION_FILE) 2>/dev/null || echo dev)"
	@printf "\n"
	@printf "  make install      \033[2mInstall the receipted Runtime Pack\033[0m\n"
	@printf "  make doctor       \033[2mHealth check\033[0m\n"
	@printf "  make update       \033[2mPull latest + reinstall\033[0m\n"
	@printf "  make uninstall    \033[2mReverse the install\033[0m\n"
	@printf "  make test         \033[2mRun the gates\033[0m\n"
	@printf "  make check        \033[2mLint shell scripts\033[0m\n"
	@printf "  make release      \033[2mBuild, sign, notarize the canonical versioned DMG\033[0m\n"
	@printf "  make portable     \033[2mBuild the provenance-bound tarball for Linux/WSL2\033[0m\n"
	@printf "  make publish-release \033[2mCold-verify and publish both channels\033[0m\n"
	@printf "\n"
	@printf "  \033[2mdev targets: make help-dev\033[0m\n"
	@printf "\n"

help-dev:
	@printf "\n"
	@printf "  \033[1m\033[38;5;173m⚒  𝚅𝚒𝚋𝚎𝚌𝚛𝚊𝚏𝚝𝚎𝚍. dev targets\033[0m\n"
	@printf "\n"
	@printf "  \033[1minstall\033[0m   install · install-source · install-auto · install-all · install-python-tools · install-vendored-binaries · install-app-binaries · install-server · install-server-service · install-hammerspoon\n"
	@printf "            skills · helpers · setup-dev · wizard · wizard-dev · gui-install · dry-run · restore\n"
	@printf "            migrate · migrate-dry · foundations · foundations-check · bundle · bundle-check\n"
	@printf "  \033[1mtests\033[0m     test · test-core · test-skills · test-install · test-parity · test-vc-frame · test-iterm2-migrate\n"
	@printf "            test-memex · test-aicx-sync · test-hammerspoon · test-keychain-session · dispatch-test · test-race-protection · check · semgrep\n"
	@printf "  \033[1miterm2\033[0m    iterm-plugin · iterm-plugin-refresh · iterm-plugin-show · iterm-plugin-uninstall · iterm-plugin-migrate\n"
	@printf "  \033[1mserver\033[0m    server · server-build · server-check · server-test · server-smoke\n"
	@printf "  \033[1mrelease\033[0m   app · dmg · dmg-signed · release-local · notarize · release · portable · publish-release · release-rehearsal\n"
	@printf "  \033[1mversion\033[0m   version · version-show · version-bump · bump-patch · bump-minor · bump-major\n"
	@printf "  \033[1mhooks\033[0m     init-hooks · seed-commit-msg-hooks · commit-safe\n"
	@printf "  \033[1mmisc\033[0m      doctor · list · update · uninstall · demo · demo-full · skill-new\n"
	@printf "\n"
	@printf "  \033[2minstall-all builds the Rust app/server binaries (voc, vc-admin, vc-server) as real files into ~/.local/bin.\033[0m\n"
	@printf "  \033[2mvc-server is installed with install-all; use make server for a foreground dev run. RUNTIME=<horse> selects a lab runtime.\033[0m\n"
	@printf "\n"

vibecrafted: install

RELEASE_SCRIPT := scripts/build-vibecrafted-release.sh
PORTABLE_SCRIPT := scripts/build-portable-release.sh
RUNTIME_PACK_INSTALLER := scripts/install-runtime-pack.sh
RUNTIME_PACK_PACKAGER := scripts/package-runtime-pack.sh
RUNTIME_PACK ?=
KEYS ?= $(HOME)/.keys
# Extra builder flags, e.g. RELEASE_FLAGS=--snapshot-donors to build from
# detached worktrees at each donor HEAD instead of refusing a dirty donor.
# RELEASE_FLAGS reaches the builder as ARGV WORDS, never as shell text. Make
# expands its own variables into the recipe before zsh parses it, so the earlier
# spelling — $(RELEASE_FLAGS) spliced straight into the single-quoted `zsh -ic`
# argument — handed the value to zsh as source.
#
# MEASURED 2026-08-18, and the vector is narrower than it looks: a `;` in the
# value lands AFTER `exec`, so it never runs. Command substitution does, because
# zsh evaluates $(...) and backticks while building the exec's argv:
#   RELEASE_FLAGS='--snapshot-donors $(touch /tmp/proof)'   -> /tmp/proof exists
# Through the environment and split with zsh's ${=...} the same value arrives as
# four inert argv words and nothing is evaluated.
RELEASE_FLAGS ?=

app:
	@VC_RELEASE_FLAGS='$(RELEASE_FLAGS)' zsh -ic 'cd "$(CURDIR)" && KEYS="$(KEYS)" exec bash "$(RELEASE_SCRIPT)" --app-only $${=VC_RELEASE_FLAGS}'

dmg dmg-signed release-local:
	@VC_RELEASE_FLAGS='$(RELEASE_FLAGS)' zsh -ic 'cd "$(CURDIR)" && KEYS="$(KEYS)" exec bash "$(RELEASE_SCRIPT)" --no-notarize $${=VC_RELEASE_FLAGS}'

notarize:
	@VC_RELEASE_FLAGS='$(RELEASE_FLAGS)' zsh -ic 'cd "$(CURDIR)" && KEYS="$(KEYS)" exec bash "$(RELEASE_SCRIPT)" --notarize-only $${=VC_RELEASE_FLAGS}'

release:
	@VC_RELEASE_FLAGS='$(RELEASE_FLAGS)' zsh -ic 'cd "$(CURDIR)" && KEYS="$(KEYS)" exec bash "$(RELEASE_SCRIPT)" $${=VC_RELEASE_FLAGS}'

# Build the standalone macOS CLI carrier from the exact same Runtime Pack bytes
# embedded in Vibecrafted.app. The packager adds only the two native helpers
# that AppDelegate normally supplies from Contents/Helpers.
runtime-pack: app
	@version="$$(tr -d '[:space:]' < VERSION)"; \
	revision="$$(git rev-parse --short=8 HEAD)"; \
	date="$${VIBECRAFTED_RELEASE_DATE:-$$(date -u +%Y%m%d)}"; \
	arch="$$(uname -m | sed 's/^arm64$$/arm64/; s/^aarch64$$/arm64/; s/^x86_64$$/x64/')"; \
	output="dist/Vibecrafted_RuntimePack_$${version}-$${date}-$${revision}-darwin-$${arch}.tar.gz"; \
	test -s "$$output" \
		|| { echo 'release builder produced no standalone Runtime Pack' >&2; exit 1; }; \
	printf '%s\n' "$$output"

# The portable channel needs no signing identity and no notary account: it is a
# provenance-bound source distribution, so it builds anywhere git and python3 do.
portable:
	@bash "$(PORTABLE_SCRIPT)"

# Ask an artifact that ALREADY EXISTS whether it names the build host. Both
# release scripts run this gate before they sign or publish, but a release is
# expensive and the artifacts from before the gate existed are still on disk —
# so the same question has to be answerable without a rebuild.
#
#   make payload-hygiene ARTIFACT=dist/Vibecrafted.app
#   make payload-hygiene ARTIFACT=dist/Vibecrafted_4.1.0-20260817-237d2814.dmg
#
# A .dmg is mounted read-only and detached again; nothing is written anywhere.
PAYLOAD_HYGIENE_SCRIPT := scripts/payload-hygiene-artifact.sh
ARTIFACT ?=
payload-hygiene:
	@test -n "$(ARTIFACT)" || { \
		printf 'usage: make payload-hygiene ARTIFACT=<path to .app, .dmg or directory>\n' >&2; \
		exit 2; }
	@bash "$(PAYLOAD_HYGIENE_SCRIPT)" "$(ARTIFACT)"

# In-flight delivery verifier: OLD/CURRENT identity, dry-run of the real
# release recipes, portable inventory, optional payload-hygiene on bytes
# already on disk. Refuses to tag, notarize, upload, or cargo --release.
# Does not build a DMG.
#
#   make release-rehearsal
#   make release-rehearsal ARTIFACT=dist/Vibecrafted.app
RELEASE_REHEARSAL_SCRIPT := scripts/release-rehearsal.sh
release-rehearsal:
	@if [ -n "$(ARTIFACT)" ]; then \
		bash "$(RELEASE_REHEARSAL_SCRIPT)" "$(ARTIFACT)"; \
	else \
		bash "$(RELEASE_REHEARSAL_SCRIPT)"; \
	fi

publish-release:
	@zsh -ic 'cd "$(CURDIR)" && exec bash scripts/publish-vibecrafted-release.sh'

unified-product-contract-gate:
	@$(MAKE) --no-print-directory release-version-gate
	@set -eu; \
	uv run --project vibecrafted-core --with pytest python -m pytest \
		tests/tui/test_unified_app_contract.py \
		tests/tui/test_makefile_installer_contract.py \
		tests/tui/test_distribution_manifest.py \
		tests/tui/test_install_bootstrap.py \
		tests/tui/test_installer_doctor.py \
		tests/tui/test_installer_uninstall.py \
		tests/tui/test_staged_tools_sync.py \
		tests/tui/test_uv_bootstrap.py -q; \
	uv run --project vibecrafted-core --with pytest python -m pytest \
		vibecrafted-core/tests/test_walkaround_distribution.py \
		vibecrafted-core/tests/test_package_layout.py \
		vibecrafted-core/tests/test_doctor.py \
		vibecrafted-core/tests/test_runtime_receipt.py -q; \
	bash scripts/verify-vibecrafted-product.sh --self-test; \
	$(PYTHON) -m json.tool vibecrafted-core/vibecrafted_core/schemas/unified_product.schema.v1.json >/dev/null; \
	$(PYTHON) -m json.tool vibecrafted-core/vibecrafted_core/trust/release-policy.v1.json >/dev/null; \
	bash -n scripts/verify-vibecrafted-product.sh; \
	tmp="$$(mktemp -d "$${TMPDIR:-/tmp}/vibecrafted-contract-wheel.XXXXXX")"; \
	trap 'rm -rf "$$tmp"' EXIT; \
	uv build --wheel --project vibecrafted-core --out-dir "$$tmp/dist" >/dev/null; \
	uv venv "$$tmp/venv" >/dev/null; \
	uv pip install --python "$$tmp/venv/bin/python" "$$tmp"/dist/vibecrafted-*.whl >/dev/null; \
	(cd / && \
		runner="$$tmp/venv/bin/verify-vibecrafted-walkaround"; \
		env -u PYTHONPATH PYTHONNOUSERSITE=1 "$$runner" --help >/dev/null; \
		rc=0; env -u PYTHONPATH PYTHONNOUSERSITE=1 "$$runner" trust-probe "$$tmp/missing-challenge" "$$tmp/missing.sig" >/dev/null 2>&1 || rc=$$?; test "$$rc" -eq 22; \
		rc=0; env -u PYTHONPATH PYTHONNOUSERSITE=1 "$$runner" verify-release --release-output "$$tmp/release-output.json" --signature "$$tmp/release-output.json.sig" >/dev/null 2>&1 || rc=$$?; test "$$rc" -eq 22; \
		rc=0; env -u PYTHONPATH PYTHONNOUSERSITE=1 "$$runner" walkaround --release-output "$$tmp/release-output.json" --signature "$$tmp/release-output.json.sig" --output "$$tmp/walkaround.json" >/dev/null 2>&1 || rc=$$?; test "$$rc" -eq 22; \
		test ! -e "$$tmp/walkaround.json")

# Exact signed-distribution boundary. Unlike the source-only unified gate, this
# intentionally requires a complete release tuple already produced in dist/.
# Running the verifier twice proves the same immutable bytes remain valid after
# the first mount/probe cycle and catches mutable host metadata or stale inner
# Runtime Pack foundation hashes before upload.
EXACT_RELEASE_OUTPUT ?= dist/release-output.json
EXACT_RELEASE_SIGNATURE ?= dist/release-output.json.sig
exact-release-contract-gate:
	@test -f "$(EXACT_RELEASE_OUTPUT)" || { echo "missing $(EXACT_RELEASE_OUTPUT)" >&2; exit 2; }
	@test -f "$(EXACT_RELEASE_SIGNATURE)" || { echo "missing $(EXACT_RELEASE_SIGNATURE)" >&2; exit 2; }
	@set -eu; \
	for pass in 1 2; do \
		env -u PYTHONPATH PYTHONNOUSERSITE=1 uv run --project vibecrafted-core \
			verify-vibecrafted-walkaround verify-release \
			--release-output "$(EXACT_RELEASE_OUTPUT)" \
			--signature "$(EXACT_RELEASE_SIGNATURE)" >/dev/null; \
	done

release-version-gate:
	@$(PYTHON) scripts/version_bump.py --check --file "$(VERSION_FILE)"

tui-installer: init-hooks
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "bootstrapping uv..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
	fi; \
	export PATH="$$HOME/.local/bin:$$PATH"; \
	VIBECRAFTED_RUNTIME="$(RUNTIME)" UV_PROJECT_ENVIRONMENT="$(UV_PROJECT_ENVIRONMENT)" uv run --project $(INSTALLER_DIR) --quiet vetcoders-installer $(MANIFEST) --quiet

# BUNDLE_DIR accepts an external prebuilt Svelte site/dist tree
# (e.g. from the sibling vibecrafted-io repo). When empty, `make wizard`
# first tries to build and serve the sibling site checkout so the local
# control plane matches the branded install surface; otherwise it falls
# back to the built-in inline HTML.
BUNDLE_DIR ?=
BUNDLE_VERSION := $(shell sed -n '1p' "$(VERSION_FILE)" 2>/dev/null | tr -d '[:space:]')
BUNDLE_ARCHIVE ?= $(SOURCE)/dist/vibecrafted-$(BUNDLE_VERSION).tar.gz

wizard: init-hooks
	@if [ -n "$(BUNDLE_DIR)" ]; then \
		echo "[wizard] Launching wizard with explicit bundle $(BUNDLE_DIR)"; \
		$(PYTHON) $(GUI_INSTALLER) --source "$(SOURCE)" --bundle-dir "$(BUNDLE_DIR)"; \
		exit 0; \
	fi; \
	if [ -n "$$VIBECRAFTED_SITE_BUNDLE" ]; then \
		echo "[wizard] Using VIBECRAFTED_SITE_BUNDLE=$$VIBECRAFTED_SITE_BUNDLE"; \
		$(PYTHON) $(GUI_INSTALLER) --source "$(SOURCE)"; \
		exit 0; \
	fi; \
	site_repo=""; \
	for p in "$(CURDIR)/../vc-runtime/vibecrafted-io" "$(CURDIR)/../vibecrafted-io" "$$HOME/.vibecrafted/vc-runtime/vibecrafted-io" "$$HOME/Libraxis/vc-runtime/vibecrafted-io"; do \
		if [ -d "$$p/site" ]; then site_repo="$$p"; break; fi; \
	done; \
	if [ -z "$$site_repo" ]; then \
		echo "[wizard] vibecrafted-io sibling not found — falling back to inline HTML"; \
		$(PYTHON) $(GUI_INSTALLER) --source "$(SOURCE)"; \
		exit 0; \
	fi; \
	echo "[wizard] Building branded install surface at $$site_repo/site"; \
	if [ ! -d "$$site_repo/site/node_modules" ]; then \
		(cd "$$site_repo/site" && pnpm install --frozen-lockfile=false) || { echo "[wizard] site dependency install failed — falling back to inline HTML"; $(PYTHON) $(GUI_INSTALLER) --source "$(SOURCE)"; exit 0; }; \
	fi; \
	(cd "$$site_repo/site" && pnpm run build) || { echo "[wizard] site build failed — falling back to inline HTML"; $(PYTHON) $(GUI_INSTALLER) --source "$(SOURCE)"; exit 0; }; \
	echo "[wizard] Launching wizard with bundle from $$site_repo/site/dist"; \
	$(PYTHON) $(GUI_INSTALLER) --source "$(SOURCE)" --bundle-dir "$$site_repo/site/dist"

gui-installer: wizard

# Development helper preserved as an explicit alias for LiveInstaller work.
# `make wizard` already rebuilds the sibling site when it is available.
wizard-dev: wizard

install-all: init-hooks
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "bootstrapping uv..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
	fi; \
	export PATH="$$HOME/.local/bin:$$PATH"; \
	VIBECRAFTED_RUNTIME="$(RUNTIME)" UV_PROJECT_ENVIRONMENT="$(UV_PROJECT_ENVIRONMENT)" uv run --project $(INSTALLER_DIR) --quiet vetcoders-installer $(MANIFEST) --yes --quiet

# Output discipline: the shell installers (foundations, frontier, runtime) are
# chatty. By default their output goes to the install log so the compact Python
# installer's [1/4..4/4] storytelling is the only on-screen output.
# VERBOSE=1 shows the full bazaar.
VERBOSE ?= 0
INSTALL_LOG := $(HOME)/.vibecrafted/install.log
SLACK_AGENT_SOURCE ?= $(abspath $(SOURCE)/../vc-slack-agent)
# Invoke via bash, never via the execute bit: sshfs-backed mounts (colima
# containers viewing a macOS checkout) strip +x from files.
INSTALL_STEP := bash scripts/install-step.sh
ifeq ($(VERBOSE),1)
INSTALL_QUIET :=
else
INSTALL_QUIET := >> "$(INSTALL_LOG)" 2>&1
endif

# Headless entrypoint for install.sh (curl|bash). Mirrors the full
# non-interactive install. Was previously undefined, so the piped
# `curl ... | bash` path ran `make install-auto` as a silent no-op.
install-auto: install

install:
	@VIBECRAFTED_RUNTIME_PACK="$(RUNTIME_PACK)" bash "$(RUNTIME_PACK_INSTALLER)"

# Explicit source/compiler lane retained for the portable Linux/WSL carrier.
# It is not the normal customer installer: it may require Rust, cargo-leptos,
# sibling donors, and platform targets. macOS CLI/App install the exact same
# closed Runtime Pack through `make install` and AppDelegate respectively.
install-source:
	@mkdir -p "$(HOME)/.vibecrafted"
	@: > "$(INSTALL_LOG)"
	@printf "Installing Vibecrafted\n"
	@VIBECRAFTED_INSTALL_LOG="$(INSTALL_LOG)" VERBOSE="$(VERBOSE)" $(INSTALL_STEP) "foundations" -- bash -e -c 'make --no-print-directory init-hooks; bash scripts/install-foundations.sh'
	@VIBECRAFTED_INSTALL_LOG="$(INSTALL_LOG)" VERBOSE="$(VERBOSE)" $(INSTALL_STEP) "runtime tools" -- bash scripts/install-runtime.sh --runtime "$(RUNTIME)" --yes
	@VIBECRAFTED_INSTALL_LOG="$(INSTALL_LOG)" VERBOSE="$(VERBOSE)" $(INSTALL_STEP) "app binaries" -- bash -e -c 'make --no-print-directory install-vendored-binaries; make --no-print-directory install-app-binaries'
	@VIBECRAFTED_INSTALL_LOG="$(INSTALL_LOG)" VERBOSE="$(VERBOSE)" $(INSTALL_STEP) "skills and launchers" -- $(MAKE) --no-print-directory install-bundle-tools
	@VIBECRAFTED_INSTALL_LOG="$(INSTALL_LOG)" VERBOSE="$(VERBOSE)" $(INSTALL_STEP) "frontier config" -- bash -e -c '$(RESOLVE_STABLE_RUNTIME_ROOT); $(REQUIRE_STAGED_RUNTIME_ROOT); bash "$$stable_root/vibecrafted-core/vibecrafted_core/runtime/scripts/install-frontier-config.sh" --source "$$stable_root" || printf "[warn] Frontier config skipped (non-fatal)\n"'
	@VIBECRAFTED_INSTALL_LOG="$(INSTALL_LOG)" VERBOSE="$(VERBOSE)" $(INSTALL_STEP) "vc-frame config" -- bash -e -c 'export PATH="$$HOME/.local/bin:$$PATH"; $(RESOLVE_STABLE_RUNTIME_ROOT); $(REQUIRE_STAGED_RUNTIME_ROOT); tool_python="$$(uv tool dir --color never)/vibecrafted/bin/python"; test -x "$$tool_python"; PYTHONPATH="$$stable_root/vibecrafted-core" "$$tool_python" -c "from vibecrafted_core.vc_frame_delivery import wire_vc_frame_config; print(wire_vc_frame_config(force_frontier=True).render(), end=\"\")"'
	@if PATH="$$HOME/.local/bin:$$PATH" command -v vc-frame >/dev/null 2>&1; then \
	  printf "\nVibecrafted is ready.\n\nStart here:\n  vc-start\n\nHealth:\n  vibecrafted doctor\n\nLog:\n  ~/.vibecrafted/install.log\n"; \
	else \
	  printf "\nVibecrafted is ready (headless: the vc-frame cockpit is not installed; vc-start needs it).\n\nStart here:\n  export PATH=\"\$$HOME/.local/bin:\$$PATH\"\n  vibecrafted doctor\n  vibecrafted implement claude --prompt \"describe this repo\"\n  vibecrafted await claude --last\n\nLog:\n  ~/.vibecrafted/install.log\n"; \
	fi

# The explicit source/compiler lane calls `install-python-tools`; retain the
# alias for that portable residual without putting it back on `make install`.
install-python-tools: install-tools

# Full install keeps the installer, runtime publication, Python-tool replacement,
# and service reconciliation under one lease.  The old launcher drains its own
# verified service before the installer can publish vibecrafted-current.
ifneq (,$(findstring n,$(firstword $(filter-out --%,$(MAKEFLAGS)))))
install-bundle-tools:
	@printf '%s\n' '[install-tools] dry-run: build payload and run bundled install under one lease'
else
install-bundle-tools:
	@set -eu; \
	case "$(INSTALL_SERVER_SERVICE_POLICY)" in preserve|ensure|isolated) ;; *) echo "[server] INSTALL_SERVER_SERVICE_POLICY must be preserve, ensure, or isolated" >&2; exit 2 ;; esac; \
	payload=0; \
	if command -v cargo >/dev/null 2>&1 && command -v cargo-leptos >/dev/null 2>&1; then \
		$(MAKE) --no-print-directory build-server-release; \
		payload=1; \
	elif command -v cargo >/dev/null 2>&1; then \
		echo "[server] cargo-leptos not found — preserving the installed server payload" >&2; \
		echo "[server] interactive shell deferred: cargo install cargo-leptos && make install-bundle-tools" >&2; \
	else \
		echo "[server] cargo not found — preserving the installed server payload" >&2; \
	fi; \
	VIBECRAFTED_INSTALL_SERVICE_POLICY="$(INSTALL_SERVER_SERVICE_POLICY)" VIBECRAFTED_INSTALL_SERVER_PAYLOAD="$$payload" VIBECRAFTED_INSTALL_SERVER_BIN_DIR="$(BIN_DIR)" VIBECRAFTED_INSTALL_SERVER_SITE_ROOT="$(SERVER_INSTALL_SITE_ROOT)" $(PYTHON) -c 'import os, sys; from pathlib import Path; sys.path.insert(0, "$(SOURCE)/scripts"); import vetcoders_install as v; bin_dir = Path(os.environ["VIBECRAFTED_INSTALL_SERVER_BIN_DIR"]); payload = (bin_dir / "$(SERVER_BIN)", bin_dir / "$(SERVER_COMPAT_BIN)", Path(os.environ["VIBECRAFTED_INSTALL_SERVER_SITE_ROOT"])) if os.environ.get("VIBECRAFTED_INSTALL_SERVER_PAYLOAD") == "1" else (); service_policy = os.environ["VIBECRAFTED_INSTALL_SERVICE_POLICY"]; raise SystemExit(v.run_with_tools_install_lease(v.vibecrafted_home(), sys.argv[1:], service_policy=service_policy, runtime_payload_paths=payload))' "$(MAKE)" --no-print-directory install-tools-held INSTALL_BUNDLE=1 INSTALL_SERVER_PAYLOAD="$$payload"
endif

# De-fragile contract: the uv-tool editable source is the STABLE runtime home
# (~/.local/share/vibecrafted/tools/vibecrafted-current), NEVER the dev-workspace
# checkout ($(SOURCE)). An editable install pointed at the checkout breaks the
# daily-driver `vibecrafted` CLI the moment the dev tree switches to a branch
# without `vibecrafted_core/cli.py` (ModuleNotFoundError). `make install` already
# stages the runtime into the stable home (vetcoders_install.py refresh_current_tools)
# before this target runs; Make resolves that home via the sourced shell throne
# (honours VIBECRAFTED_TOOLS_HOME / VIBECRAFTED_RUNTIME_HOME / XDG_DATA_HOME)
# and refuse to fall back to the checkout if staging is missing.
ifneq (,$(findstring n,$(firstword $(filter-out --%,$(MAKEFLAGS)))))
install-tools:
	@printf '%s\n' '[install-tools] dry-run: acquire lease and run install-tools-held'
else
install-tools:
	@VIBECRAFTED_INSTALL_SERVICE_POLICY="$(INSTALL_TOOLS_SERVICE_POLICY)" $(PYTHON) -c 'import os, sys; sys.path.insert(0, "$(SOURCE)/scripts"); import vetcoders_install as v; raise SystemExit(v.run_with_tools_install_lease(v.vibecrafted_home(), sys.argv[1:], service_policy=os.environ["VIBECRAFTED_INSTALL_SERVICE_POLICY"]))' "$(MAKE)" --no-print-directory install-tools-held
endif

# Internal continuation. The outer target waits for this submake while retaining
# both the tools-install lease and, on macOS, the post-drain supervisor fence
# across publish -> uv replacement. Service reconciliation and handoff sealing
# remain in the outer Python owner so a shell failure cannot strand ownership.
# Dispatched workers legitimately carry PYTHONPATH for their immutable runtime
# generation; clear it only after source-side staging so it cannot override the
# replacement uv tool or its exact-generation import check.
install-tools-held:
	@set -eu; \
	if [ -z "$${VIBECRAFTED_INSTALL_LEASE_FD:-}" ]; then \
		echo "[install-tools] FATAL: internal install target requires the cross-process installer lease" >&2; \
		exit 1; \
	fi; \
	$(PYTHON) -c 'import sys; sys.path.insert(0, "$(SOURCE)/scripts"); import vetcoders_install as v; v._require_inherited_tools_install_lease(v.vibecrafted_home())'; \
	if ! command -v uv >/dev/null 2>&1; then \
		echo "bootstrapping uv..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
	fi; \
	export PATH="$$HOME/.local/bin:$$PATH"; \
	$(RESOLVE_STABLE_RUNTIME_ROOT); \
	if [ "$(INSTALL_BUNDLE)" = "1" ]; then \
		$(PYTHON) $(INSTALLER) install --source "$(SOURCE)" --compact --non-interactive --mirror; \
	else \
		echo "[install-tools] staging runtime under the cross-process installer lease..."; \
		$(PYTHON) -c 'import sys; sys.path.insert(0, "$(SOURCE)/scripts"); from pathlib import Path; import vetcoders_install as v; v.refresh_current_tools(Path("$(SOURCE)").resolve(), v.vibecrafted_home(), mirror=True)'; \
	fi; \
	$(REQUIRE_STAGED_RUNTIME_ROOT); \
	unset PYTHONPATH; \
	uv tool install --force --reinstall --editable "$$stable_root/vibecrafted-core"; \
	uv tool install --force --reinstall --editable "$$stable_root/plugins/iterm2"; \
	echo "[install-tools] NOTICE: replacing vibecrafted-mcp may close attached stdio clients; reconnect the operator session after install" >&2; \
	uv tool install --force --reinstall --editable "$$stable_root/vibecrafted-mcp" --with-editable "$$stable_root/vibecrafted-core"; \
	$(PYTHON) -c 'import sys; from pathlib import Path; sys.path.insert(0, "$(SOURCE)/scripts"); import vetcoders_install as v; v._install_launcher(Path(sys.argv[1]), dry_run=False, update_rc=False)' "$$stable_root"; \
	tool_root="$$(uv tool dir --color never)/vibecrafted"; \
	tool_python="$$tool_root/bin/python"; \
	if [ ! -x "$$tool_python" ]; then \
		echo "[install-tools] FATAL: expected uv tool interpreter missing at $$tool_python" >&2; \
		exit 1; \
	fi; \
	$(PYTHON) -c 'import sys; from pathlib import Path; sys.path.insert(0, "$(SOURCE)/scripts"); import vetcoders_install as v; v._install_secure_walkaround_launcher(Path(sys.argv[1]), Path(sys.argv[2]), launcher_path=Path(sys.argv[3]))' "$$stable_root" "$$tool_python" "$$tool_root/bin/verify-vibecrafted-walkaround"; \
	python_entrypoints="$$($(PYTHON) -c 'import sys; sys.path.insert(0, "$(SOURCE)/scripts"); import vetcoders_install as v; print(" ".join(v.PYTHON_ENTRYPOINT_LAUNCHERS))')"; \
	for entrypoint in $$python_entrypoints; do \
		entrypoint_tool_root="$$tool_root"; \
		if [ "$$entrypoint" = "vibecrafted-mcp" ]; then \
			entrypoint_tool_root="$${tool_root%/vibecrafted}/vibecrafted-mcp"; \
		fi; \
		entrypoint_path="$$entrypoint_tool_root/bin/$$entrypoint"; \
		if [ ! -x "$$entrypoint_path" ]; then \
			echo "[install-tools] FATAL: uv tool entrypoint $$entrypoint is missing or not executable" >&2; \
			exit 1; \
		fi; \
		entrypoint_shebang="$$(sed -n '1p' "$$entrypoint_path")"; \
		case "$$entrypoint_shebang" in \
			"#!$$entrypoint_tool_root/bin/python"|"#!$$entrypoint_tool_root/bin/python3") ;; \
			"#!/bin/sh") \
				if ! sed -n '2p' "$$entrypoint_path" | grep -F "$$entrypoint_tool_root/bin/python" >/dev/null; then \
					echo "[install-tools] FATAL: uv tool entrypoint $$entrypoint is not owned by the uv interpreter: $$entrypoint_shebang" >&2; \
					exit 1; \
				fi ;; \
			*) echo "[install-tools] FATAL: uv tool entrypoint $$entrypoint is not owned by the uv interpreter: $$entrypoint_shebang" >&2; exit 1 ;; \
		esac; \
	done; \
	for entrypoint in vibecrafted vc-workflow vc-guardian vc-server-supervisor verify-vibecrafted-walkaround; do \
		resolved="$$(command -v "$$entrypoint" 2>/dev/null || true)"; \
		if [ -z "$$resolved" ] || [ ! -x "$$resolved" ]; then \
			echo "[install-tools] FATAL: expected executable entrypoint $$entrypoint was not installed" >&2; \
			exit 1; \
		fi; \
		resolved_real="$$($(PYTHON) -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$$resolved")"; \
		if [ "$$entrypoint" = "vibecrafted" ]; then \
			expected_path="$$stable_root/vibecrafted-core/vibecrafted_core/deck/vibecrafted"; \
		else \
			expected_path="$$tool_root/bin/$$entrypoint"; \
		fi; \
		expected_real="$$($(PYTHON) -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$$expected_path")"; \
		if [ "$$resolved_real" != "$$expected_real" ]; then \
			echo "[install-tools] FATAL: $$entrypoint resolves to $$resolved_real, expected installed target $$expected_real" >&2; \
			exit 1; \
		fi; \
		if ! "$$resolved" --help >/dev/null 2>&1; then \
			echo "[install-tools] FATAL: installed entrypoint $$entrypoint does not execute successfully" >&2; \
			exit 1; \
		fi; \
		echo "[install-tools] installed: $$entrypoint -> $$resolved"; \
	done; \
	actual_core="$$("$$tool_python" -c 'from pathlib import Path; import vibecrafted_core; print(Path(vibecrafted_core.__file__).resolve().parent)')"; \
	expected_core="$$($(PYTHON) -c 'from pathlib import Path; import sys; print((Path(sys.argv[1]) / "vibecrafted_core").resolve())' "$$stable_root/vibecrafted-core")"; \
	if [ "$$actual_core" != "$$expected_core" ]; then \
		echo "[install-tools] FATAL: uv tool imports vibecrafted_core from $$actual_core, expected stable runtime $$expected_core" >&2; \
		exit 1; \
	fi; \
	if [ "$(INSTALL_SERVER_PAYLOAD)" = "1" ]; then \
		$(MAKE) --no-print-directory install-server-payload; \
	fi; \
	$(PYTHON) scripts/slack_provider.py install --framework-source "$(SOURCE)" --source "$(SLACK_AGENT_SOURCE)"; \
	echo "[install-tools] staged runtime and uv tools; outer lease owner will reconcile service ownership"

# install-all owns every binary the product ships into BIN (~/.local/bin).
# The vibecrafted-app members ship `voc` and `vc-admin`; vibecrafted-server
# ships `vc-server`. Build from source in release and copy REAL
# files. Never `cargo install` here — that creates ~/.local/bin -> ~/.cargo/bin
# symlink drift, the exact pattern the runtime contract bans in BIN.
APP_DIR := vibecrafted-app
APP_BINARIES := voc vc-admin
APP_BUILD_TARGET := $(CARGO_BUILD_ROOT)/vibecrafted-app
BIN_DIR := $(HOME)/.local/bin
VENDORED_FOUNDATION_BINARIES := vc-frame loctree-mcp loct aicx aicx-mcp
HOST_UNAME_S := $(shell uname -s)
HOST_UNAME_M := $(shell uname -m)
HOST_VENDOR_OS := $(if $(filter Darwin,$(HOST_UNAME_S)),darwin,$(if $(filter Linux,$(HOST_UNAME_S)),linux,$(shell uname -s | tr '[:upper:]' '[:lower:]')))
HOST_VENDOR_ARCH := $(if $(filter arm64 aarch64,$(HOST_UNAME_M)),arm64,$(if $(filter x86_64,$(HOST_UNAME_M)),x64,$(HOST_UNAME_M)))
HOST_VENDOR_PLATFORM := $(HOST_VENDOR_OS)-$(HOST_VENDOR_ARCH)
VENDORED_FOUNDATION_DIR := $(SOURCE)/bin/vendor/$(HOST_VENDOR_PLATFORM)

install-vendored-binaries:
	@mkdir -p "$(BIN_DIR)"
	@if [ ! -d "$(VENDORED_FOUNDATION_DIR)" ]; then \
		echo "[vendor] no vendored foundation binaries for $(HOST_VENDOR_PLATFORM) at $(VENDORED_FOUNDATION_DIR); keeping external fallback"; \
		exit 0; \
	fi
	@for bin in $(VENDORED_FOUNDATION_BINARIES); do \
		src="$(VENDORED_FOUNDATION_DIR)/$$bin"; \
		if [ ! -f "$$src" ]; then \
			echo "[vendor] $$bin not present in $(VENDORED_FOUNDATION_DIR); keeping external fallback"; \
			continue; \
		fi; \
		install -m 0755 "$$src" "$(BIN_DIR)/$$bin"; \
		chmod +x "$(BIN_DIR)/$$bin"; \
		echo "[vendor] installed $$bin -> $(BIN_DIR)/$$bin"; \
	done

# Degrade like the vendored lane when the toolchain is absent: a cargo-less
# host (slim containers) keeps a working framework install and gets the app
# binaries from vendor/ or a release bundle instead of failing the whole lane.
install-app-binaries:
	@if ! command -v cargo >/dev/null 2>&1; then \
		echo "[app] cargo not found — skipping $(APP_BINARIES) build (install rustup or use vendored/release binaries)" >&2; \
		exit 0; \
	fi; \
	set -e; \
	mkdir -p "$(HOME)/.vibecrafted" "$(BIN_DIR)" "$(APP_BUILD_TARGET)"; \
	echo "[app] building release binaries ($(APP_BINARIES)) from $(APP_DIR)"; \
	( cd $(APP_DIR) && CARGO_TARGET_DIR="$(APP_BUILD_TARGET)" cargo build --release --locked -p voc $(INSTALL_QUIET) ); \
	for bin in $(APP_BINARIES); do \
		rm -f "$(BIN_DIR)/$$bin"; \
		install -m 0755 "$(APP_BUILD_TARGET)/release/$$bin" "$(BIN_DIR)/$$bin"; \
	done; \
	echo "[app] installed: $(APP_BINARIES) -> $(BIN_DIR)"

skills:
	@$(PYTHON) $(INSTALLER) install --source "$(SOURCE)" --non-interactive

helpers:
	@bash $(SHELL_INSTALLER) --source "$(SOURCE)"

foundations:
	@bash scripts/install-foundations.sh

foundations-check:
	@bash scripts/install-foundations.sh --check

setup-dev: init-hooks
	@if ! command -v uv >/dev/null 2>&1; then \
		echo "bootstrapping uv..."; \
		curl -LsSf https://astral.sh/uv/install.sh | sh; \
	fi; \
	export PATH="$$HOME/.local/bin:$$PATH"; \
	VIBECRAFTED_RUNTIME="$(RUNTIME)" UV_PROJECT_ENVIRONMENT="$(UV_PROJECT_ENVIRONMENT)" uv run --project $(INSTALLER_DIR) --quiet vetcoders-installer $(MANIFEST) --advanced --quiet

dry-run:
	@uv run --project $(INSTALLER_DIR) --quiet vetcoders-installer $(MANIFEST) --dry-run

doctor:
	@$(PYTHON) $(INSTALLER) doctor

list:
	@$(PYTHON) $(INSTALLER) list --source "$(SOURCE)"

bundle:
	@$(PYTHON) scripts/build_marketplace_bundle.py --output "$(SOURCE)/dist/vibecrafted-framework.plugin"
	@set -e; \
	source_root="$$(cd "$(SOURCE)" && pwd -P)"; \
	source_parent="$$(dirname "$$source_root")"; \
	tmp_archive="$$(mktemp "$$source_parent/.vibecrafted-bundle-archive.XXXXXX")"; \
	trap 'rm -f "$$tmp_archive"' EXIT; \
	mkdir -p "$(dir $(BUNDLE_ARCHIVE))"; \
	$(PYTHON) scripts/distribution_manifest.py archive --source "$$source_root" --output "$$tmp_archive" --publish-output "$(BUNDLE_ARCHIVE)" --root-name "vibecrafted-$(BUNDLE_VERSION)"; \
	trap - EXIT

# Keep `-p` on extraction: bundle-check must preserve canonical archive modes
# even when the operator runs with a restrictive umask such as 077.
bundle-check:
	@set -e; \
	tmp_root="$${TMPDIR:-/tmp}"; \
	tmp_bundle="$$(mktemp "$$tmp_root/vibecrafted-bundle.XXXXXX")"; \
	tmp_runtime="$$(mktemp -d "$$tmp_root/vibecrafted-runtime.XXXXXX")"; \
	tmp_archive="$$tmp_runtime/vibecrafted-$(BUNDLE_VERSION).tar.gz"; \
	trap 'rm -f "$$tmp_bundle"; rm -rf "$$tmp_runtime"' EXIT; \
	$(PYTHON) scripts/build_marketplace_bundle.py --output "$$tmp_bundle"; \
	test -s "$$tmp_bundle" || { echo "Marketplace bundle generation produced an empty artifact."; exit 1; }; \
	$(PYTHON) scripts/distribution_manifest.py archive --source "$(SOURCE)" --output "$$tmp_archive" --root-name "vibecrafted-$(BUNDLE_VERSION)"; \
	mkdir -p "$$tmp_runtime/extracted"; \
	tar -xzpf "$$tmp_archive" -C "$$tmp_runtime/extracted"; \
	$(PYTHON) scripts/distribution_manifest.py check --root "$$tmp_runtime/extracted/vibecrafted-$(BUNDLE_VERSION)" --require-source-provenance; \
	echo "Marketplace bundle and runtime payload are valid."

version version-show:
	@version="$$(sed -n '1p' "$(VERSION_FILE)" 2>/dev/null | tr -d '[:space:]')"; \
	if [ -z "$$version" ]; then echo "VERSION file missing or empty: $(VERSION_FILE)" >&2; exit 1; fi; \
	printf "version: %s\n" "$$version"; \
	printf "tag: v%s\n" "$$version"; \
	if git rev-parse --verify "refs/tags/v$$version" >/dev/null 2>&1; then \
		echo "tag-state: exists"; \
	else \
		echo "tag-state: missing"; \
	fi

version-bump:
ifeq ($(origin VERSION),command line)
	@$(PYTHON) scripts/version_bump.py "$(VERSION)" --file "$(VERSION_FILE)"
else
	@echo "VERSION is required. Usage: make version-bump VERSION={patch|minor|major|x.y.z}" >&2 && exit 1
endif

bump-patch:
	@$(MAKE) version-bump VERSION=patch

bump-minor:
	@$(MAKE) version-bump VERSION=minor

bump-major:
	@$(MAKE) version-bump VERSION=major

semgrep:
	@if command -v semgrep >/dev/null 2>&1; then \
		semgrep scan --config auto --error --quiet --exclude-rule html.security.audit.missing-integrity.missing-integrity .; \
	else \
		uvx semgrep scan --config auto --error --quiet --exclude-rule html.security.audit.missing-integrity.missing-integrity .; \
	fi

test: test-keychain-session
	@if command -v uv >/dev/null 2>&1; then \
		PYTHONPATH="$(SOURCE)" uv run --with pytest pytest tests/tui -q; \
	else \
		PYTHONPATH="$(SOURCE)" $(PYTHON) -m pytest tests/tui -q; \
	fi

test-keychain-session:
	@bash scripts/tests/keychain-session-test.sh

test-core:
	@if command -v uv >/dev/null 2>&1; then \
		test_tools_home=$$(mktemp -d "$${TMPDIR:-/tmp}/vibecrafted-test-core-tools.XXXXXX"); \
		trap 'rm -rf "$$test_tools_home"' EXIT; \
		VIBECRAFTED_TOOLS_HOME="$$test_tools_home" uv run --project vibecrafted-core --with pytest python -m pytest vibecrafted-core/tests -q; \
	else \
		test_tools_home=$$(mktemp -d "$${TMPDIR:-/tmp}/vibecrafted-test-core-tools.XXXXXX"); \
		trap 'rm -rf "$$test_tools_home"' EXIT; \
		VIBECRAFTED_TOOLS_HOME="$$test_tools_home" PYTHONPATH="$(SOURCE)/vibecrafted-core" $(PYTHON) -m pytest vibecrafted-core/tests -q; \
	fi

dispatch-test:
	@if command -v uv >/dev/null 2>&1; then \
		uv run --project vibecrafted-core --with pytest python -m pytest vibecrafted-core/tests/dispatch -q; \
	else \
		PYTHONPATH="$(SOURCE)/vibecrafted-core" $(PYTHON) -m pytest vibecrafted-core/tests/dispatch -q; \
	fi

test-skills:
	@bash tests/skill_loader_smoke.sh

# Plan 03 (META_22) — install.sh / install.ps1 cross-platform smoke.
# Host-only assertions: pre-flight, detection helpers, hint matrix, .ps1
# entry shape. Full install matrix runs in .github/workflows/install-linux.yml.
test-install:
	@bash tests/install_smoke.sh

# update NEVER rewrites the working tree with another branch's content.
# The old `git checkout "$(BRANCH)" -- .` plastered a stale $(BRANCH) tree
# over index+worktree of whatever branch the Living Tree was on (2026-08-12:
# 174 files silently reverted to an Aug-8 main). Now: fast-forward only when
# already on $(BRANCH); on any other branch the tree is left untouched and
# the install runs from the current checkout as-is.
update:
	@if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then \
		current="$$(git rev-parse --abbrev-ref HEAD)"; \
		if [ "$$current" = "$(BRANCH)" ]; then \
			printf "Git repo on $(BRANCH) — fast-forwarding from origin/$(BRANCH)...\n"; \
			git fetch origin; \
			git merge --ff-only "origin/$(BRANCH)" || printf "No fast-forward possible — tree left as-is.\n"; \
		else \
			printf "Repo is on '%s', not '$(BRANCH)' — tree untouched; installing from the current checkout.\n" "$$current"; \
		fi; \
		printf "Re-installing...\n"; \
		$(PYTHON) $(INSTALLER) install --source "$(SOURCE)" --with-shell --mirror --non-interactive; \
	else \
		printf "Tarball install — re-running bootstrap installer...\n"; \
		bash "$(SOURCE)/install.sh" --ref "$(BRANCH)"; \
	fi

uninstall:
	@VIBECRAFTED_RUNTIME_PACK="$(RUNTIME_PACK)" bash "$(RUNTIME_PACK_INSTALLER)" --uninstall

restore:
	@$(PYTHON) $(INSTALLER) restore

check:
	@$(PYTHON) scripts/check_shell.py
	@echo "Check complete."

iterm-plugin:
	@uv run --project plugins/iterm2 --quiet python -m vibecrafted_iterm2.iterm2_profiles install

iterm-plugin-refresh:
	@uv run --project plugins/iterm2 --quiet python -m vibecrafted_iterm2.iterm2_profiles refresh

iterm-plugin-show:
	@uv run --project plugins/iterm2 --quiet python -m vibecrafted_iterm2.iterm2_profiles show

iterm-plugin-uninstall:
	@uv run --project plugins/iterm2 --quiet python -m vibecrafted_iterm2.iterm2_profiles uninstall

# Plan 10 (META_22) — operators with v1.7 [experimental] dynamic profiles run
# this once on v1.8.0 upgrade. Reads vibecrafted-experimental.json, writes
# vibecrafted.json with cleaned names + preserved GUIDs, .bak backup,
# removes the legacy file. Idempotent: re-running is safe (no-op).
iterm-plugin-migrate:
	@uv run --project plugins/iterm2 --quiet python -m vibecrafted_iterm2.iterm2_profiles migrate-from-experimental

demo:
	@bash scripts/vc-dashboard

demo-full:
	@bash scripts/vc-dashboard --html

init-hooks:
	@if [ "$$CI" = "true" ]; then \
		echo "CI detected - skipping git hook bootstrap."; \
	elif git rev-parse --git-dir >/dev/null 2>&1; then \
		git config core.hooksPath scripts/hooks >/dev/null; \
		chmod +x scripts/hooks/pre-commit scripts/hooks/pre-push scripts/hooks/commit-msg; \
		command -v uv >/dev/null 2>&1 || { echo "bootstrapping uv..."; curl -LsSf https://astral.sh/uv/install.sh | sh; }; \
		uvx ruff --version >/dev/null 2>&1 || echo "  [warn] ruff unavailable via uvx"; \
		command -v semgrep >/dev/null 2>&1 || uvx semgrep --version >/dev/null 2>&1 || echo "  [warn] semgrep unavailable"; \
		npx --yes prettier --version >/dev/null 2>&1 || echo "  [warn] prettier unavailable via npx"; \
	else \
		true; \
	fi

seed-commit-msg-hooks:
	@bash scripts/install-agent-commit-msg-hooks.sh ..

# -----------------------------------------------------------------------------
# Living Tree race protection (Plan 07 — doctrine 2026-04-16/17 incident learning)
#
# Two invocation modes:
#
#   Single-line:
#     make commit-safe MSG="<subject>" FILES="path1 path2"
#
#   Multi-line (Plan 07-b — closes Limitation #2):
#     make commit-safe MSG_FILE=/tmp/msg.txt FILES="path1 path2"
#
# MSG_FILE reads the commit message from a file (subject + blank line + body).
# Use this for any multi-line message — avoids Makefile $$ escaping vs. shell
# expansion interaction that historically broke MSG="..." with embedded
# newlines/quotes/dollars.
#
# Helper handles three race detectors: HEAD shift, foreign-file inclusion,
# and (informationally) tree-hash mismatch. Plan 07-b relaxed tree-hash
# alone from race-signal to informational notice (pre-commit hooks like
# prettier --write legitimately mutate staged content; that is not a race).
# -----------------------------------------------------------------------------

commit-safe:
	@if [ -z "$(FILES)" ]; then \
		echo "usage:" >&2; \
		echo "  make commit-safe MSG=\"<subject>\" FILES=\"path1 path2 ...\"" >&2; \
		echo "  make commit-safe MSG_FILE=<path>  FILES=\"path1 path2 ...\"" >&2; \
		echo "" >&2; \
		echo "Race-protected commit helper for Living Tree workflow." >&2; \
		echo "MSG_FILE supports multi-line commit bodies (Plan 07-b)." >&2; \
		exit 1; \
	fi
	@if [ -n "$(MSG_FILE)" ] && [ -n "$(MSG)" ]; then \
		echo "make commit-safe: pass MSG OR MSG_FILE, not both" >&2; \
		exit 1; \
	fi
	@if [ -z "$(MSG)" ] && [ -z "$(MSG_FILE)" ]; then \
		echo "make commit-safe: MSG=\"...\" or MSG_FILE=<path> is required" >&2; \
		exit 1; \
	fi
	@if [ -n "$(MSG_FILE)" ]; then \
		bash scripts/lib/living-tree-commit.sh --message-file "$(MSG_FILE)" -- $(FILES); \
	else \
		bash scripts/lib/living-tree-commit.sh "$(MSG)" -- $(FILES); \
	fi

test-race-protection:
	@bash tests/race_protection_test.sh

# -----------------------------------------------------------------------------
# Plan 06 (META_22) — AGENT MODEL PARITY automated enforcement.
#
# Verifies the bash + Python parity layers (scripts/lib/spawn.sh and
# vibecrafted-core/vibecrafted_core/agent_dispatch.py) reject same-family
# downgrades, allow cross-family delegation, and honor the
# VIBECRAFTED_SPAWN_ALLOW_DOWNGRADE=1 operator override with an audit
# warning. Captures doctrine 2026-04-10 doctrine.
# -----------------------------------------------------------------------------

test-parity:
	@bash tests/spawn_parity_test.sh
	@if command -v uv >/dev/null 2>&1; then \
		uv run --with pytest pytest tests/agent_dispatch_test.py -q; \
	else \
		PYTHONPATH="$(SOURCE)/vibecrafted-core" $(PYTHON) -m pytest tests/agent_dispatch_test.py -q; \
	fi

# -----------------------------------------------------------------------------
# Plan 04 — skill-authoring scaffolder.
#
# `make skill-new NAME=vc-my-skill` wraps tools/vc-skill-new.sh. The script
# enforces name validation (vc- prefix, lowercase, no collisions) and copies
# the package-owned skills/_template/ with placeholder substitution. See
# docs/CONTRIBUTING-SKILLS.md for the full operator authoring guide.
# -----------------------------------------------------------------------------

skill-new:
	@if [ -z "$(NAME)" ]; then \
		echo "usage: make skill-new NAME=vc-<skill-name>" >&2; \
		echo "" >&2; \
		echo "Scaffold a new vc-* skill from vibecrafted-core/vibecrafted_core/skills/_template/." >&2; \
		echo "See docs/CONTRIBUTING-SKILLS.md for the authoring guide." >&2; \
		exit 2; \
	fi
	@bash tools/vc-skill-new.sh "$(NAME)"

# -----------------------------------------------------------------------------
# Plan 12 (META_22) — vc-frame multi-agent layouts smoke gate.
#
# Verifies:
#   - all shipped layouts under config/vc-frame/layouts/*.kdl parse via
#     `vc-frame --layout <name> setup --check`
#   - all four mesh accent themes (mesh-red/purple/cyan/green) load
#   - auto-theme.sh passes bash -n + shellcheck
#   - auto-theme.sh resolves host -> theme from mesh.conf (or
#     VIBECRAFTED_MESH_MAP), honours aliases, and falls back to neutral
#     for unknown hosts
#
# Tolerant of missing vc-frame — falls back to script-level checks only.
# -----------------------------------------------------------------------------

test-vc-frame:
	@bash tests/vc-frame-layouts-smoke.sh

# -----------------------------------------------------------------------------
# Plan 10 (META_22) — iTerm2 stack GA promotion smoke gate.
#
# Verifies the migrate-from-experimental subcommand:
#   - sets up a fixture vibecrafted-experimental.json
#   - runs `python -m vibecrafted_iterm2.iterm2_profiles migrate-from-experimental`
#     against a sandboxed install dir
#   - asserts the new vibecrafted.json exists with cleaned profile names
#     and preserved GUIDs
#   - asserts the .bak backup was created and the legacy file removed
#   - asserts the migration is idempotent (second invocation is no-op)
#
# This is a bash smoke wrapper around the same logic that
# test_iterm2_profiles.py pytest suite covers in-process; both run on CI.
# -----------------------------------------------------------------------------

test-iterm2-migrate:
	@bash tests/iterm2_migration_test.sh

# -----------------------------------------------------------------------------
# Plan 09 (META_22) — memex cross-session retrieval client smoke gate.
#
# Two tiers run together:
#   1. bash integration smoke (tests/memex_integration_test.sh) — asserts
#      SKILL.md Sense 1 documentation, public surface, populated-memex
#      fallthrough via injected MCP stub, graceful degradation on
#      unreachable endpoint, config precedence (TOML > env), pure
#      defaults disable cleanly, empty-query short-circuit.
#   2. pytest unit tier (vibecrafted-core/tests/test_memex_client.py) —
#      covers HTTP success/failure parsing, MCP bridge transport,
#      config layer precedence, malformed responses, limit clamping.
#
# The bash tier owns the OPERATOR-VISIBLE contract (markdown + sandbox
# shell). The pytest tier owns the implementation correctness contract.
# Both must pass for `make test-memex` to be green.
# -----------------------------------------------------------------------------

test-memex:
	@bash tests/memex_integration_test.sh
	@if command -v uv >/dev/null 2>&1; then \
		uv run --project vibecrafted-core --with pytest python -m pytest vibecrafted-core/tests/test_memex_client.py -q; \
	else \
		PYTHONPATH="$(SOURCE)/vibecrafted-core" $(PYTHON) -m pytest vibecrafted-core/tests/test_memex_client.py -q; \
	fi

# -----------------------------------------------------------------------------
# Plan 08 (META_22) — AICX cross-machine sync v2 smoke gate.
#
# Two tiers run together:
#   1. bash end-to-end smoke (tests/aicx_sync_smoke.sh) — asserts the
#      two-machine fixture: dual-add discovery, dry-run is read-only,
#      authority-tier conflict resolution (repo_verified > aicx_agent),
#      same-tier tie surfacing, prior conflict-log decision honoured on
#      subsequent runs, corrupted chunk reported + skipped without crash,
#      CLI wrapper (scripts/aicx-sync.sh) help + unknown-command rejection
#      + TOML config-file fallback.
#   2. pytest unit tier (vibecrafted-core/tests/test_aicx_sync.py) —
#      covers Authority enum + aliases, AicxChunk normalization,
#      discover_chunks adds/conflicts/corrupted, resolve_conflict full
#      tier ladder + log honouring + last-write-wins on decisions,
#      apply_plan dry-run read-only invariant, record_decision validation,
#      CLI surface.
#
# The bash tier owns the OPERATOR-VISIBLE contract (CLI wrapper + cross-
# machine fixture). The pytest tier owns the implementation correctness
# contract. Both must pass for `make test-aicx-sync` to be green.
# -----------------------------------------------------------------------------

test-aicx-sync:
	@bash tests/aicx_sync_smoke.sh
	@if command -v uv >/dev/null 2>&1; then \
		uv run --project vibecrafted-core --with pytest python -m pytest vibecrafted-core/tests/test_aicx_sync.py -q; \
	else \
		PYTHONPATH="$(SOURCE)/vibecrafted-core" $(PYTHON) -m pytest vibecrafted-core/tests/test_aicx_sync.py -q; \
	fi

# -----------------------------------------------------------------------------
# Plan 11 (META_22) — Hammerspoon URL handler stack install + smoke gate.
#
# install-hammerspoon: copies config/hammerspoon/init.lua to
#   ~/.hammerspoon/init.lua, offering a .bak overwrite when an existing
#   config is present, and reloads Hammerspoon. macOS-only — exits 0 with
#   a notice on Linux/CI.
#
# test-hammerspoon: structural lints + sanitization unit tests (8 positive
#   + 4 negative cases) against the Lua param validator. Includes static
#   analysis (bash -n, shellcheck, optional luac -p) + handler-registration
#   grep checks. Live macOS integration is operator-driven (the test
#   surfaces the manual command rather than spawning iTerm2 tabs during CI).
#
# Stack agent-native runtime context (doctrine 2026-05-08): OSC 8 hyperlink
# → iTerm2 Cmd+Click → macOS open URL → Hammerspoon URL handler →
# AppleScript spawn iTerm2 tab → CLI dispatch. See docs/HAMMERSPOON.md.
# -----------------------------------------------------------------------------

install-hammerspoon:
	@bash scripts/install-hammerspoon.sh

test-hammerspoon:
	@bash tests/hammerspoon_smoke.sh

# -----------------------------------------------------------------------------
# Task 24.A-2 — vibecrafted-server (control-plane read surface).
#
# The Rust workspace under vibecrafted-server/ is the remote-observability
# server: `control-core` is a read-only typed mirror of the same
# ~/.vibecrafted/control_plane/ the Python runtime writes, and `web` is a
# Leptos 0.8 SSR axum app that exposes it. install-all installs the release
# binary and assets; these targets remain the reproducible run/build/verify
# entry points.
#
# Live reads (curl-smoke after `make server`):
#   GET /api/control/state         merged StateView (active/recent/warnings/events)
#   GET /api/control/runs          every runs/<id>.json snapshot, newest-first
#   GET /api/control/runs/{run_id} a single run, or 404 JSON
#   GET /api/control/events        SSE stream of events.jsonl (?since= / Last-Event-ID)
#
# The bin is built/run with the `ssr` feature (the bare `cargo build` main is a
# hydrate stub). vc-server constructs LeptosOptions itself, so foreground and
# installed runs are path-independent and do not require LEPTOS_* environment.
# The host linker fix (-ld_classic, for Leptos' long symbol names) lives in
# vibecrafted-server/.cargo/config.toml so plain cargo works too.
# -----------------------------------------------------------------------------
SERVER_DIR  := vibecrafted-server
SERVER_PACKAGE := vibecrafted-server-web
SERVER_BIN  := vc-server
SERVER_COMPAT_BIN := vibecrafted-server-web
SERVER_ADDR ?= 127.0.0.1:3024
VIBECRAFTED_RUNTIME_HOME ?= $(HOME)/.local/share/vibecrafted
SERVER_BUILD_TARGET := $(CARGO_BUILD_ROOT)/vibecrafted-server
SERVER_BUILD_SITE_ROOT := $(SERVER_BUILD_TARGET)/site
SERVER_INSTALL_SITE_ROOT := $(VIBECRAFTED_RUNTIME_HOME)/server/site

server-build:
	@mkdir -p "$(SERVER_BUILD_TARGET)"
	@cd $(SERVER_DIR) && CARGO_TARGET_DIR="$(SERVER_BUILD_TARGET)" cargo build -p $(SERVER_PACKAGE) --no-default-features --features ssr

server: server-build
	@echo "[server] control plane: $${VIBECRAFTED_HOME:-$$HOME/.vibecrafted}/control_plane"
	@echo "[server] listening on  http://$(SERVER_ADDR)   (Ctrl-C to stop)"
	@echo "[server] reads: /api/control/state  /api/control/runs  /api/control/runs/{run_id}"
	@echo "[server] reads: /api/control/lifecycle  /api/control/lifecycle/{run_id}"
	@echo "[server] stream: /api/control/events  (SSE, ?since= / Last-Event-ID)"
	@cd $(SERVER_DIR) && "$(SERVER_BUILD_TARGET)/debug/$(SERVER_PACKAGE)" --addr "$(SERVER_ADDR)"

server-check:
	@cd $(SERVER_DIR) && CARGO_TARGET_DIR="$(SERVER_BUILD_TARGET)" cargo clippy -p control-core -- -D warnings
	@cd $(SERVER_DIR) && CARGO_TARGET_DIR="$(SERVER_BUILD_TARGET)" cargo clippy -p $(SERVER_PACKAGE) --no-default-features --features ssr -- -D warnings

server-test:
	@cd $(SERVER_DIR) && CARGO_TARGET_DIR="$(SERVER_BUILD_TARGET)" cargo test -p control-core

build-server-release:
	@if ! command -v cargo >/dev/null 2>&1; then \
		echo "[server] cargo not found — skipping $(SERVER_PACKAGE) release build" >&2; \
		exit 0; \
	fi; \
	set -e; \
	if ! command -v cargo-leptos >/dev/null 2>&1; then \
		echo "[server] FATAL: cargo-leptos is required to build the interactive server shell" >&2; \
		exit 1; \
	fi; \
	if command -v wasm-bindgen >/dev/null 2>&1; then \
		lock_version="$$(cd "$(SERVER_DIR)" && cargo tree --locked -p wasm-bindgen --depth 0 --prefix none | awk 'NR == 1 { sub(/^v/, "", $$2); print $$2 }')"; \
		if [ -z "$$lock_version" ]; then \
			echo "[server] FATAL: could not resolve wasm-bindgen version from Cargo.lock" >&2; \
			exit 1; \
		fi; \
		cli_version="$$(wasm-bindgen --version | awk '{print $$2}')"; \
		if [ "$$cli_version" != "$$lock_version" ]; then \
			echo "[server] FATAL: wasm-bindgen CLI $$cli_version does not match Cargo.lock $$lock_version" >&2; \
			echo "[server] repair: cargo install --force wasm-bindgen-cli --version $$lock_version --locked" >&2; \
			exit 1; \
		fi; \
	fi; \
	echo "[server] building release package + hydration assets ($(SERVER_PACKAGE))"; \
	mkdir -p "$(SERVER_BUILD_TARGET)"; \
	ulimit -f unlimited; ( cd $(SERVER_DIR) && CARGO_TARGET_DIR="$(SERVER_BUILD_TARGET)" LEPTOS_SITE_ROOT="$(SERVER_BUILD_SITE_ROOT)" cargo leptos build --release --bin-cargo-args="--locked" --lib-cargo-args="--locked" ); \
	if [ ! -x "$(SERVER_BUILD_TARGET)/release/$(SERVER_PACKAGE)" ] || [ ! -d "$(SERVER_BUILD_SITE_ROOT)/pkg" ]; then \
		echo "[server] FATAL: cargo-leptos did not produce the server + site package" >&2; \
		exit 1; \
	fi; \
	if ! find "$(SERVER_BUILD_SITE_ROOT)/pkg" -type f -name '*.wasm' -print -quit | grep -q .; then \
		echo "[server] FATAL: hydration wasm is missing from $(SERVER_BUILD_SITE_ROOT)/pkg" >&2; \
		exit 1; \
	fi

install-server-payload:
	@set -eu; \
	if [ -z "$${VIBECRAFTED_INSTALL_LEASE_FD:-}" ]; then \
		echo "[server] FATAL: internal payload target requires the cross-process installer lease" >&2; \
		exit 1; \
	fi; \
	$(PYTHON) -c 'import sys; sys.path.insert(0, "$(SOURCE)/scripts"); import vetcoders_install as v; v._require_inherited_tools_install_lease(v.vibecrafted_home())'; \
	if ! command -v cargo >/dev/null 2>&1; then \
		echo "[server] cargo not found — preserving the installed server payload" >&2; \
		exit 0; \
	fi; \
	if [ ! -x "$(SERVER_BUILD_TARGET)/release/$(SERVER_PACKAGE)" ]; then \
		echo "[server] FATAL: release payload is missing; run make build-server-release" >&2; \
		exit 1; \
	fi; \
	mkdir -p "$(HOME)/.vibecrafted" "$(BIN_DIR)" "$(SERVER_INSTALL_SITE_ROOT)"; \
	rm -f "$(BIN_DIR)/$(SERVER_BIN)" "$(BIN_DIR)/$(SERVER_COMPAT_BIN)"; \
	install -m 0755 "$(SERVER_BUILD_TARGET)/release/$(SERVER_PACKAGE)" "$(BIN_DIR)/$(SERVER_BIN)"; \
	install -m 0755 "$(SERVER_BUILD_TARGET)/release/$(SERVER_PACKAGE)" "$(BIN_DIR)/$(SERVER_COMPAT_BIN)"; \
	echo "[server] copying interactive site assets to $(SERVER_INSTALL_SITE_ROOT)"; \
	rm -rf "$(SERVER_INSTALL_SITE_ROOT)"/*; \
	cp -R "$(SERVER_BUILD_SITE_ROOT)/." "$(SERVER_INSTALL_SITE_ROOT)/"; \
	echo "[server] installed: $(SERVER_BIN) -> $(BIN_DIR) (real file)"; \
	echo "[server] compat: $(SERVER_COMPAT_BIN) -> $(BIN_DIR) (real file)"; \
	echo "[server] assets -> $(SERVER_INSTALL_SITE_ROOT)"

ifneq (,$(findstring n,$(firstword $(filter-out --%,$(MAKEFLAGS)))))
install-server: build-server-release
	@printf '%s\n' '[server] dry-run: install payload under runtime transaction'
else
install-server: build-server-release
	@set -eu; \
	payload=0; \
	if command -v cargo >/dev/null 2>&1; then payload=1; fi; \
	VIBECRAFTED_INSTALL_SERVER_PAYLOAD="$$payload" VIBECRAFTED_INSTALL_SERVER_BIN_DIR="$(BIN_DIR)" VIBECRAFTED_INSTALL_SERVER_SITE_ROOT="$(SERVER_INSTALL_SITE_ROOT)" $(PYTHON) -c 'import os, sys; from pathlib import Path; sys.path.insert(0, "$(SOURCE)/scripts"); import vetcoders_install as v; bin_dir = Path(os.environ["VIBECRAFTED_INSTALL_SERVER_BIN_DIR"]); payload = (bin_dir / "$(SERVER_BIN)", bin_dir / "$(SERVER_COMPAT_BIN)", Path(os.environ["VIBECRAFTED_INSTALL_SERVER_SITE_ROOT"])) if os.environ.get("VIBECRAFTED_INSTALL_SERVER_PAYLOAD") == "1" else (); raise SystemExit(v.run_with_tools_install_lease(v.vibecrafted_home(), sys.argv[1:], runtime_payload_paths=payload, require_tools_handoff=False))' "$(MAKE)" --no-print-directory install-server-payload
endif

install-server-service:
	@if [ "$$(uname -s)" != "Darwin" ]; then \
		echo "[server-service] launchd not available — skipping persistent service"; \
		exit 0; \
	fi; \
	set -e; \
	export PATH="$(BIN_DIR):$$PATH"; \
	unset PYTHONPATH; \
	launcher="$$(command -v vibecrafted 2>/dev/null || true)"; \
	supervisor="$$(command -v vc-server-supervisor 2>/dev/null || true)"; \
	if [ -z "$$launcher" ] || [ ! -x "$$launcher" ]; then \
		echo "[server-service] FATAL: canonical vibecrafted launcher is unavailable after install" >&2; \
		exit 1; \
	fi; \
	if [ -z "$$supervisor" ] || [ ! -x "$$supervisor" ]; then \
		echo "[server-service] FATAL: vc-server-supervisor is unavailable after install" >&2; \
		exit 1; \
	fi; \
	echo "[server-service] installing or reconciling persistent supervision..."; \
	(cd / && "$$launcher" server service install); \
	echo "[server-service] restarting after server binary and asset replacement..."; \
	(cd / && "$$launcher" server service restart)

server-smoke: install-server
	@echo "[server-smoke] Run 1/3" && bash tests/server_smoke.sh
	@echo "[server-smoke] Run 2/3" && bash tests/server_smoke.sh
	@echo "[server-smoke] Run 3/3" && bash tests/server_smoke.sh
