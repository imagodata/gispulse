#!/bin/sh
# GISPulse installer — downloads the engine binary from GitHub Releases.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/imagodata/gispulse/main/scripts/install.sh | sh
#
# Or download first, inspect, then run:
#   curl -fsSL -o install.sh https://raw.githubusercontent.com/imagodata/gispulse/main/scripts/install.sh
#   less install.sh
#   sh install.sh
#
# Environment variables:
#   GISPULSE_VERSION    — version to install (default: latest)
#   GISPULSE_INSTALL_DIR — install directory (default: ~/.local/bin or /usr/local/bin if root)

set -eu

# ── Constants ────────────────────────────────────────────────────────────
REPO="imagodata/gispulse"
BINARY_NAME="gispulse-engine"
GITHUB_API="https://api.github.com/repos/${REPO}/releases"
GITHUB_DL="https://github.com/${REPO}/releases/download"

# ── Colors (disabled if not a terminal) ──────────────────────────────────
if [ -t 1 ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    BOLD=''
    RESET=''
fi

# ── Helpers ──────────────────────────────────────────────────────────────
info()  { printf "${GREEN}[info]${RESET}  %s\n" "$1"; }
warn()  { printf "${YELLOW}[warn]${RESET}  %s\n" "$1"; }
error() { printf "${RED}[error]${RESET} %s\n" "$1" >&2; exit 1; }

need_cmd() {
    if ! command -v "$1" > /dev/null 2>&1; then
        error "Required command not found: $1. Please install it and retry."
    fi
}

# ── Detect OS ────────────────────────────────────────────────────────────
detect_os() {
    case "$(uname -s)" in
        Linux*)  echo "linux" ;;
        Darwin*) echo "macos" ;;
        CYGWIN*|MINGW*|MSYS*) echo "windows" ;;
        *)       error "Unsupported operating system: $(uname -s)" ;;
    esac
}

# ── Detect Architecture ─────────────────────────────────────────────────
detect_arch() {
    case "$(uname -m)" in
        x86_64|amd64)    echo "x86_64" ;;
        aarch64|arm64)   echo "aarch64" ;;
        *)               error "Unsupported architecture: $(uname -m)" ;;
    esac
}

# ── Map OS/arch to target triple ─────────────────────────────────────────
# Must match the matrix in .github/workflows/release.yml
get_target_triple() {
    _os="$1"
    _arch="$2"

    case "${_os}-${_arch}" in
        linux-x86_64)    echo "x86_64-unknown-linux-gnu" ;;
        macos-aarch64)   echo "aarch64-apple-darwin" ;;
        macos-x86_64)    echo "x86_64-apple-darwin" ;;
        windows-x86_64)  echo "x86_64-pc-windows-msvc" ;;
        *)               error "No pre-built binary for ${_os}/${_arch}. Supported: linux/x86_64, macos/x86_64, macos/aarch64, windows/x86_64." ;;
    esac
}

# ── Resolve version ─────────────────────────────────────────────────────
resolve_version() {
    if [ -n "${GISPULSE_VERSION:-}" ]; then
        echo "${GISPULSE_VERSION}"
        return
    fi

    need_cmd curl

    _latest=$(curl -fsSL "${GITHUB_API}/latest" 2>/dev/null \
        | grep '"tag_name"' \
        | head -1 \
        | sed 's/.*"tag_name": *"//;s/".*//')

    if [ -z "${_latest}" ]; then
        error "Failed to fetch latest version from GitHub. Set GISPULSE_VERSION manually or check your network."
    fi

    echo "${_latest}"
}

# ── Determine install directory ──────────────────────────────────────────
get_install_dir() {
    if [ -n "${GISPULSE_INSTALL_DIR:-}" ]; then
        echo "${GISPULSE_INSTALL_DIR}"
        return
    fi

    if [ "$(id -u)" = "0" ]; then
        echo "/usr/local/bin"
    else
        echo "${HOME}/.local/bin"
    fi
}

# ── Download helper (curl or wget) ───────────────────────────────────────
download() {
    _url="$1"
    _dest="$2"

    if command -v curl > /dev/null 2>&1; then
        curl -fsSL -o "${_dest}" "${_url}"
    elif command -v wget > /dev/null 2>&1; then
        wget -q -O "${_dest}" "${_url}"
    else
        error "Neither curl nor wget found. Please install one and retry."
    fi
}

# ── Verify checksum ─────────────────────────────────────────────────────
verify_checksum() {
    _file="$1"
    _checksums="$2"
    _filename="$3"

    if [ ! -f "${_checksums}" ]; then
        warn "checksums.txt not found in release. Skipping verification."
        warn "TODO: Release workflow should generate checksums.txt (see scripts/install.sh comments)."
        return 0
    fi

    _expected=$(grep "${_filename}" "${_checksums}" | awk '{print $1}')
    if [ -z "${_expected}" ]; then
        warn "No checksum entry for ${_filename} in checksums.txt. Skipping verification."
        return 0
    fi

    if command -v sha256sum > /dev/null 2>&1; then
        _actual=$(sha256sum "${_file}" | awk '{print $1}')
    elif command -v shasum > /dev/null 2>&1; then
        _actual=$(shasum -a 256 "${_file}" | awk '{print $1}')
    else
        warn "Neither sha256sum nor shasum found. Skipping checksum verification."
        return 0
    fi

    if [ "${_expected}" != "${_actual}" ]; then
        error "Checksum mismatch for ${_filename}. Expected: ${_expected}, got: ${_actual}. The download may be corrupted."
    fi

    info "Checksum verified."
}

# ── Ensure directory is in PATH ──────────────────────────────────────────
ensure_in_path() {
    _dir="$1"

    case ":${PATH}:" in
        *":${_dir}:"*) return 0 ;;
    esac

    warn "${_dir} is not in your PATH."

    _shell_name=$(basename "${SHELL:-/bin/sh}")
    case "${_shell_name}" in
        zsh)   _rc="${HOME}/.zshrc" ;;
        bash)  _rc="${HOME}/.bashrc" ;;
        fish)  _rc="${HOME}/.config/fish/config.fish" ;;
        *)     _rc="${HOME}/.profile" ;;
    esac

    if [ "${_shell_name}" = "fish" ]; then
        _line="set -gx PATH ${_dir} \$PATH"
    else
        _line="export PATH=\"${_dir}:\$PATH\""
    fi

    printf "\n  Add this to %s:\n\n    %s\n\n" "${_rc}" "${_line}"
    printf "  Then restart your shell or run: %s\n\n" ". ${_rc}"
}

# ── Main ─────────────────────────────────────────────────────────────────
main() {
    printf "\n${BOLD}GISPulse Installer${RESET}\n\n"

    # Detect platform
    _os=$(detect_os)
    _arch=$(detect_arch)
    _triple=$(get_target_triple "${_os}" "${_arch}")

    info "Detected platform: ${_os}/${_arch} (${_triple})"

    # Resolve version
    _version=$(resolve_version)
    info "Version: ${_version}"

    # Binary filename
    if [ "${_os}" = "windows" ]; then
        _filename="${BINARY_NAME}-${_triple}.exe"
    else
        _filename="${BINARY_NAME}-${_triple}"
    fi

    # Download URLs
    _binary_url="${GITHUB_DL}/${_version}/${_filename}"
    _checksums_url="${GITHUB_DL}/${_version}/checksums.txt"

    # Temp directory for download
    _tmpdir=$(mktemp -d)
    trap 'rm -rf "${_tmpdir}"' EXIT

    # Download binary
    info "Downloading ${_filename}..."
    download "${_binary_url}" "${_tmpdir}/${_filename}" || \
        error "Failed to download binary from ${_binary_url}. Check that version ${_version} exists and has pre-built binaries."

    # Download and verify checksum
    # TODO: The release workflow does not currently generate checksums.txt.
    #       Add this step to .github/workflows/release.yml:
    #
    #   - name: Generate checksums
    #     run: |
    #       cd dist
    #       sha256sum gispulse-engine-* > checksums.txt
    #
    #   Then upload checksums.txt as a release asset alongside the binaries.
    download "${_checksums_url}" "${_tmpdir}/checksums.txt" 2>/dev/null || true
    verify_checksum "${_tmpdir}/${_filename}" "${_tmpdir}/checksums.txt" "${_filename}"

    # Install
    _install_dir=$(get_install_dir)
    mkdir -p "${_install_dir}"

    if [ "${_os}" = "windows" ]; then
        _dest="${_install_dir}/${BINARY_NAME}.exe"
    else
        _dest="${_install_dir}/${BINARY_NAME}"
    fi

    # Remove existing binary if present (idempotent)
    rm -f "${_dest}"

    mv "${_tmpdir}/${_filename}" "${_dest}"
    chmod +x "${_dest}"

    info "Installed ${BINARY_NAME} to ${_dest}"

    # PATH check
    ensure_in_path "${_install_dir}"

    # Success
    printf "${GREEN}${BOLD}GISPulse ${_version} installed successfully.${RESET}\n\n"
    printf "  Get started:\n\n"
    printf "    ${BINARY_NAME} --help        # Show available commands\n"
    printf "    ${BINARY_NAME} serve          # Start the engine server\n"
    printf "    ${BINARY_NAME} version        # Show version info\n\n"

    # Also mention Docker and pip alternatives
    printf "  Alternative installation methods:\n\n"
    printf "    docker pull ghcr.io/${REPO}:latest\n"
    printf "    pip install gispulse-sdk\n\n"
}

main "$@"
