# GISPulse Packaging

Distribution packaging for Homebrew (macOS/Linux) and Scoop (Windows).

## Structure

```
packaging/
  homebrew/gispulse.rb    # Homebrew formula template (with {{PLACEHOLDERS}})
  scoop/gispulse.json     # Scoop manifest template (with {{PLACEHOLDERS}})
  update-formulae.sh      # Script to generate final files from templates
  dist/                   # Generated output (gitignored)
    homebrew/gispulse.rb
    scoop/gispulse.json
```

## Setup: Creating Distribution Repos

### Homebrew Tap

1. Create a GitHub repo: `imagodata/homebrew-gispulse`
2. Add a `Formula/` directory
3. Users install with:
   ```bash
   brew tap imagodata/gispulse
   brew install gispulse
   ```

### Scoop Bucket

1. Create a GitHub repo: `imagodata/scoop-gispulse`
2. Add a `bucket/` directory
3. Users install with:
   ```powershell
   scoop bucket add gispulse https://github.com/imagodata/scoop-gispulse
   scoop install gispulse
   ```

## Release Workflow

### Manual

```bash
# Generate formulae for a specific version
./update-formulae.sh 0.3.0

# Generate and push to tap/bucket repos
TAP_REPO_PATH=/path/to/homebrew-gispulse \
BUCKET_REPO_PATH=/path/to/scoop-gispulse \
./update-formulae.sh 0.3.0 --push
```

### CI Automation (GitHub Actions)

Add this job to your release workflow (`.github/workflows/release.yml`):

```yaml
  update-packaging:
    needs: [build-binaries]  # after binaries are uploaded to the release
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Generate formulae
        run: ./packaging/update-formulae.sh ${{ github.ref_name }}

      # --- Homebrew tap ---
      - uses: actions/checkout@v4
        with:
          repository: imagodata/homebrew-gispulse
          path: homebrew-tap
          token: ${{ secrets.TAP_GITHUB_TOKEN }}

      - name: Update Homebrew tap
        run: |
          cp packaging/dist/homebrew/gispulse.rb homebrew-tap/Formula/gispulse.rb
          cd homebrew-tap
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add Formula/gispulse.rb
          git commit -m "gispulse ${{ github.ref_name }}"
          git push

      # --- Scoop bucket ---
      - uses: actions/checkout@v4
        with:
          repository: imagodata/scoop-gispulse
          path: scoop-bucket
          token: ${{ secrets.TAP_GITHUB_TOKEN }}

      - name: Update Scoop bucket
        run: |
          cp packaging/dist/scoop/gispulse.json scoop-bucket/bucket/gispulse.json
          cd scoop-bucket
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add bucket/gispulse.json
          git commit -m "gispulse ${{ github.ref_name }}"
          git push
```

**Required secret:** `TAP_GITHUB_TOKEN` - a fine-grained PAT with write access to `homebrew-gispulse` and `scoop-gispulse` repos.

## Release Asset Naming Convention

The scripts expect the following asset names on each GitHub Release:

| Platform           | Archive name                                  |
|--------------------|-----------------------------------------------|
| macOS Apple Silicon| `gispulse-v{VERSION}-darwin-arm64.tar.gz`     |
| macOS Intel        | `gispulse-v{VERSION}-darwin-x86_64.tar.gz`    |
| Linux x86_64       | `gispulse-v{VERSION}-linux-x86_64.tar.gz`     |
| Windows x86_64     | `gispulse-v{VERSION}-windows-x86_64.zip`      |

Optional sidecar checksums: `{asset_name}.sha256` (one hash per file, avoids downloading full binaries during formula generation).

## Local Testing

### Homebrew

```bash
# Generate the formula
./update-formulae.sh 0.3.0

# Install from local formula file
brew install --formula packaging/dist/homebrew/gispulse.rb

# Verify
gispulse --version
gispulse doctor

# Uninstall
brew uninstall gispulse
```

### Scoop

```powershell
# Install from local manifest
scoop install .\packaging\dist\scoop\gispulse.json

# Verify
gispulse --version
gispulse doctor

# Uninstall
scoop uninstall gispulse
```

## Archive Contents

Each release archive should contain at minimum:

```
gispulse              # (or gispulse.exe on Windows) - standalone binary
completions/          # optional
  gispulse.bash
  _gispulse
  gispulse.fish
```

The completions are optional; the formula will install them if present in the archive.
