# frozen_string_literal: true

# Staged cask for vetcoders/homebrew-tap.
# The CSV version and sha256 are placeholders. A published GitHub Release
# has never carried a DMG (latest public assets are still the v3.5.0
# source tarball). Do not `brew install --cask` until
# docs/RELEASE_CHECKLIST.md has been run and the operator pastes the
# real Vibecrafted_<version>-<YYYYMMDD>-<sha8>.dmg coordinates.
cask "vibecrafted-app" do
  version "4.3.0,YYYYMMDD,sha8"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"

  url "https://github.com/vetcoders/vibecrafted/releases/download/v#{version.csv.first}/Vibecrafted_#{version.csv.first}-#{version.csv.second}-#{version.csv.third}.dmg"
  name "Vibecrafted"
  desc "Release engine for AI-built software"
  homepage "https://vibecrafted.io"

  depends_on macos: ">= :sonoma"

  app "Vibecrafted.app"

  caveats <<~EOS
    macOS 14+ arm64 only. There is no Intel build and no native Windows
    package. Verify the adjacent .dmg.sha256 before first launch if you
    downloaded the DMG by hand.

    Fill version.csv (4.3.0, YYYYMMDD, sha8) and sha256 from the
    published asset name after `make publish-release`.
  EOS
end
