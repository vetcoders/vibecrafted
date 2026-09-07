# shell-agent - UniFFI bridge + SwiftUI app
# Created by Vetcoders (c)2026 LibraxisAI

export MACOSX_DEPLOYMENT_TARGET ?= 14.0

.PHONY: help bindings xcode app app-debug dmg dmg-signed notarize release

HELP_C_CYAN   := \033[36m
HELP_C_GREEN  := \033[32m
HELP_C_YELLOW := \033[33m
HELP_C_RESET  := \033[0m

bindings:
	@echo "Building vibecrafted-shell-ffi (release)..."
	@cargo build -p vibecrafted-shell-ffi --release
	@./scripts/fix-dylib-install-names.sh
	@echo "Generating Swift bindings..."
	@cargo run -p vibecrafted-uniffi-bindgen -- generate --library ../target/release/libvibecrafted_shell_ffi.dylib --language swift --out-dir app/Vibecrafted/Bridge/
	@./scripts/normalize-bindings.sh app/Vibecrafted/Bridge
	@echo "Bindings ready: app/Vibecrafted/Bridge/"

xcode:
	@cd app && xcodegen generate
	@echo "Xcode project generated: app/Vibecrafted.xcodeproj"

app: bindings xcode
	@echo "Building Vibecrafted.app..."
	@mkdir -p build
	@rm -rf build/Vibecrafted.app
	@set -o pipefail && xcodebuild -project app/Vibecrafted.xcodeproj -scheme Vibecrafted -configuration Debug -derivedDataPath build/DerivedData build 2>&1 | tail -20
	@BUILT_APP=$$(find build/DerivedData -name Vibecrafted.app -type d | head -1); \
		if [ -z "$$BUILT_APP" ]; then \
			echo "ERROR: Vibecrafted.app not found in build/DerivedData"; \
			exit 1; \
		fi; \
		cp -R "$$BUILT_APP" build/Vibecrafted.app
	@/usr/libexec/PlistBuddy -c "Set :CFBundleIconFile Vibecrafted.icns" build/Vibecrafted.app/Contents/Info.plist 2>/dev/null || /usr/libexec/PlistBuddy -c "Add :CFBundleIconFile string Vibecrafted.icns" build/Vibecrafted.app/Contents/Info.plist
	@mkdir -p build/Vibecrafted.app/Contents/Resources
	@../../scripts/build-vibecrafted-icon.sh ../../docs/presence/logo-master.png build/Vibecrafted.app/Contents/Resources/Vibecrafted.icns
	@! find build/Vibecrafted.app/Contents/Resources -maxdepth 1 -type f -name '*.icns' ! -name 'Vibecrafted.icns' -print -quit | grep -q .
	@./scripts/fix-dylib-install-names.sh build/Vibecrafted.app
	@codesign --force --deep --sign - build/Vibecrafted.app
	@echo "App built: build/Vibecrafted.app"

dmg:
	@$(MAKE) -C ../.. dmg

dmg-signed:
	@$(MAKE) -C ../.. dmg-signed

notarize:
	@$(MAKE) -C ../.. notarize

release:
	@$(MAKE) -C ../.. release

app-debug: app

help:
	@printf "\n$(HELP_C_CYAN)shell-agent$(HELP_C_RESET) - UniFFI bridge + SwiftUI app\n\n"
	@printf "  $(HELP_C_YELLOW)BUILD$(HELP_C_RESET)\n"
	@printf "    $(HELP_C_GREEN)%-14s$(HELP_C_RESET) %s\n" "bindings" "Build Rust FFI and generate Swift bindings"
	@printf "    $(HELP_C_GREEN)%-14s$(HELP_C_RESET) %s\n" "xcode" "Generate app/Vibecrafted.xcodeproj"
	@printf "    $(HELP_C_GREEN)%-14s$(HELP_C_RESET) %s\n" "app" "Build and ad-hoc codesign Vibecrafted.app"
	@printf "    $(HELP_C_GREEN)%-14s$(HELP_C_RESET) %s\n" "app-debug" "Build the same native App debug target"
	@printf "\n  $(HELP_C_YELLOW)PACKAGE$(HELP_C_RESET)\n"
	@printf "    $(HELP_C_GREEN)%-14s$(HELP_C_RESET) %s\n" "dmg" "Build unsigned/ad-hoc DMG"
	@printf "    $(HELP_C_GREEN)%-14s$(HELP_C_RESET) %s\n" "dmg-signed" "Build signed DMG"
	@printf "    $(HELP_C_GREEN)%-14s$(HELP_C_RESET) %s\n" "notarize" "Delegate notarization to the product owner"
	@printf "    $(HELP_C_GREEN)%-14s$(HELP_C_RESET) %s\n" "release" "Delegate the single public release to the product owner"
