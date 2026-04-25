# typed: false
# frozen_string_literal: true

# Homebrew formula for GISPulse - Modular geospatial engine
# This formula installs pre-built standalone binaries from GitHub Releases.
#
# Placeholders (replaced by CI via update-formulae.sh):
#   {{VERSION}}        - Release version without 'v' prefix (e.g. 0.3.0)
#   {{SHA256_DARWIN_ARM64}}  - SHA256 of macOS Apple Silicon archive
#   {{SHA256_DARWIN_X86_64}} - SHA256 of macOS Intel archive
#   {{SHA256_LINUX_X86_64}}  - SHA256 of Linux x86_64 archive
#
# Inspired by mise, uv, ruff formulas that distribute standalone binaries.

class Gispulse < Formula
  desc "Modular geospatial engine with business rules, triggers, and dual-mode processing"
  homepage "https://github.com/imagodata/gispulse"
  version "{{VERSION}}"
  license "AGPL-3.0-or-later"

  on_macos do
    if Hardware::CPU.arm?
      url "https://github.com/imagodata/gispulse/releases/download/v{{VERSION}}/gispulse-v{{VERSION}}-darwin-arm64.tar.gz"
      sha256 "{{SHA256_DARWIN_ARM64}}"
    else
      url "https://github.com/imagodata/gispulse/releases/download/v{{VERSION}}/gispulse-v{{VERSION}}-darwin-x86_64.tar.gz"
      sha256 "{{SHA256_DARWIN_X86_64}}"
    end
  end

  on_linux do
    url "https://github.com/imagodata/gispulse/releases/download/v{{VERSION}}/gispulse-v{{VERSION}}-linux-x86_64.tar.gz"
    sha256 "{{SHA256_LINUX_X86_64}}"
  end

  def install
    bin.install "gispulse"
    # Install shell completions if present in the archive
    bash_completion.install "completions/gispulse.bash" if File.exist?("completions/gispulse.bash")
    zsh_completion.install "completions/_gispulse" if File.exist?("completions/_gispulse")
    fish_completion.install "completions/gispulse.fish" if File.exist?("completions/gispulse.fish")
  end

  def caveats
    <<~EOS
      GISPulse has been installed. To verify your environment, run:

        gispulse doctor

      This checks for PostGIS connectivity, GPKG/SpatiaLite support,
      and any missing optional dependencies.

      Documentation: https://github.com/imagodata/gispulse
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/gispulse --version")
  end
end
