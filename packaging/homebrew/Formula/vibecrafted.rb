# frozen_string_literal: true

# Staged formula for vetcoders/homebrew-tap.
# sha256 is a placeholder. Do not `brew install` this file until
# v4.3.0 is tagged and the operator pastes the real archive digest.
class Vibecrafted < Formula
  desc "Release engine for AI-built software"
  homepage "https://vibecrafted.io"
  version "4.3.0"
  license "BUSL-1.1"

  # GitHub source archive of the annotated tag. The product does not yet
  # publish a 4.3.0 tarball on the Releases page (latest public release
  # is still v3.5.0).
  url "https://github.com/vetcoders/vibecrafted/archive/refs/tags/v#{version}.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"

  head "https://github.com/vetcoders/vibecrafted.git", branch: "main"

  depends_on "uv"

  on_macos do
    depends_on macos: :sonoma
  end

  def install
    libexec.install Dir["*"]
    (bin/"vibecrafted").write <<~SH
      #!/bin/bash
      set -euo pipefail
      export VIBECRAFTED_ROOT="#{libexec}"
      exec "#{libexec}/scripts/vibecrafted" "$@"
    SH
  end

  def caveats
    <<~EOS
      This formula installs the command deck. Runtime generations still
      live under ~/.local/share/vibecrafted (XDG), not the Cellar.

      After install:
        vibecrafted doctor

      There is no native Windows bottle. On Windows install WSL2 and use
      this formula inside the Linux distro, or follow docs/INSTALL.md.

      The signed desktop app is a separate cask (vibecrafted-app) and
      stays unpublished until a release actually attaches a DMG.
    EOS
  end

  test do
    assert_path_exists bin/"vibecrafted"
    assert_predicate bin/"vibecrafted", :executable?
  end
end
