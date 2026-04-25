#!/usr/bin/env bash
# update-formulae.sh - Update Homebrew formula and Scoop manifest for a new release.
#
# Usage:
#   ./update-formulae.sh <version>
#   ./update-formulae.sh 0.3.0
#   ./update-formulae.sh 0.3.0 --push
#
# The script:
#   1. Downloads (or computes) SHA256 checksums for the release assets
#   2. Generates the final Homebrew formula from the template
#   3. Generates the final Scoop manifest from the template
#   4. Optionally commits and pushes to the tap/bucket repos
#
# Prerequisites:
#   - gh CLI authenticated (for downloading release assets)
#   - jq (for JSON manipulation)
#   - The template files must exist in the same directory as this script

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="imagodata/gispulse"

# --- Argument parsing ---

VERSION="${1:-}"
PUSH=false

if [[ -z "$VERSION" ]]; then
    echo "Usage: $0 <version> [--push]"
    echo "Example: $0 0.3.0"
    exit 1
fi

# Strip leading 'v' if provided
VERSION="${VERSION#v}"

if [[ "${2:-}" == "--push" ]]; then
    PUSH=true
fi

TAG="v${VERSION}"
echo "==> Updating packaging for GISPulse ${TAG}"

# --- Dependency check ---

for cmd in gh jq sha256sum; do
    if ! command -v "$cmd" &>/dev/null; then
        # macOS uses shasum instead of sha256sum
        if [[ "$cmd" == "sha256sum" ]] && command -v shasum &>/dev/null; then
            sha256sum() { shasum -a 256 "$@"; }
            export -f sha256sum
        else
            echo "Error: '$cmd' is required but not found in PATH."
            exit 1
        fi
    fi
done

# --- Download assets and compute checksums ---

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT

echo "==> Downloading release assets to ${WORK_DIR}"

PLATFORMS=(
    "darwin-arm64:tar.gz"
    "darwin-x86_64:tar.gz"
    "linux-x86_64:tar.gz"
    "windows-x86_64:zip"
)

declare -A CHECKSUMS

for entry in "${PLATFORMS[@]}"; do
    platform="${entry%%:*}"
    ext="${entry##*:}"
    asset_name="gispulse-${TAG}-${platform}.${ext}"

    echo "    Downloading ${asset_name}..."

    # Try to download the asset; if a .sha256 sidecar exists, use that instead
    if gh release download "$TAG" --repo "$REPO" --pattern "${asset_name}.sha256" --dir "$WORK_DIR" 2>/dev/null; then
        # Sidecar checksum file exists - read it
        checksum=$(awk '{print $1}' "${WORK_DIR}/${asset_name}.sha256")
        echo "    [sidecar] ${platform}: ${checksum}"
    elif gh release download "$TAG" --repo "$REPO" --pattern "${asset_name}" --dir "$WORK_DIR" 2>/dev/null; then
        # No sidecar - download the full asset and compute
        checksum=$(sha256sum "${WORK_DIR}/${asset_name}" | awk '{print $1}')
        echo "    [computed] ${platform}: ${checksum}"
    else
        echo "    [warning] Asset ${asset_name} not found for release ${TAG}, using placeholder"
        checksum="ASSET_NOT_FOUND_${platform}"
    fi

    CHECKSUMS["$platform"]="$checksum"
done

# --- Generate Homebrew formula ---

echo "==> Generating Homebrew formula"

HOMEBREW_TEMPLATE="${SCRIPT_DIR}/homebrew/gispulse.rb"
HOMEBREW_OUT="${SCRIPT_DIR}/dist/homebrew/gispulse.rb"
mkdir -p "$(dirname "$HOMEBREW_OUT")"

sed \
    -e "s|{{VERSION}}|${VERSION}|g" \
    -e "s|{{SHA256_DARWIN_ARM64}}|${CHECKSUMS[darwin-arm64]}|g" \
    -e "s|{{SHA256_DARWIN_X86_64}}|${CHECKSUMS[darwin-x86_64]}|g" \
    -e "s|{{SHA256_LINUX_X86_64}}|${CHECKSUMS[linux-x86_64]}|g" \
    "$HOMEBREW_TEMPLATE" > "$HOMEBREW_OUT"

echo "    Written to ${HOMEBREW_OUT}"

# --- Generate Scoop manifest ---

echo "==> Generating Scoop manifest"

SCOOP_TEMPLATE="${SCRIPT_DIR}/scoop/gispulse.json"
SCOOP_OUT="${SCRIPT_DIR}/dist/scoop/gispulse.json"
mkdir -p "$(dirname "$SCOOP_OUT")"

sed \
    -e "s|{{VERSION}}|${VERSION}|g" \
    -e "s|{{SHA256_WINDOWS_X86_64}}|${CHECKSUMS[windows-x86_64]}|g" \
    "$SCOOP_TEMPLATE" > "$SCOOP_OUT"

echo "    Written to ${SCOOP_OUT}"

# --- Optional: push to tap/bucket repos ---

if [[ "$PUSH" == true ]]; then
    echo "==> Pushing to distribution repos"

    TAP_DIR="${TAP_REPO_PATH:-}"
    BUCKET_DIR="${BUCKET_REPO_PATH:-}"

    if [[ -n "$TAP_DIR" && -d "$TAP_DIR" ]]; then
        cp "$HOMEBREW_OUT" "${TAP_DIR}/Formula/gispulse.rb"
        pushd "$TAP_DIR" > /dev/null
        git add Formula/gispulse.rb
        git commit -m "gispulse ${TAG}"
        git push
        popd > /dev/null
        echo "    Homebrew tap updated"
    else
        echo "    [skip] TAP_REPO_PATH not set or directory not found"
        echo "           Set TAP_REPO_PATH to the local clone of imagodata/homebrew-gispulse"
    fi

    if [[ -n "$BUCKET_DIR" && -d "$BUCKET_DIR" ]]; then
        cp "$SCOOP_OUT" "${BUCKET_DIR}/bucket/gispulse.json"
        pushd "$BUCKET_DIR" > /dev/null
        git add bucket/gispulse.json
        git commit -m "gispulse ${TAG}"
        git push
        popd > /dev/null
        echo "    Scoop bucket updated"
    else
        echo "    [skip] BUCKET_REPO_PATH not set or directory not found"
        echo "           Set BUCKET_REPO_PATH to the local clone of imagodata/scoop-gispulse"
    fi
fi

echo ""
echo "==> Done. Generated files:"
echo "    Homebrew: ${HOMEBREW_OUT}"
echo "    Scoop:    ${SCOOP_OUT}"
echo ""
echo "To test the Homebrew formula locally:"
echo "    brew install --formula ${HOMEBREW_OUT}"
