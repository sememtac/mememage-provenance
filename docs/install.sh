#!/usr/bin/env bash
#
# mememage.art/install.sh — one-line desktop install for the Mememage app.
# (mememage.art/install is the human download page; this is the pipe target.)
#
#   curl -fsSL https://mememage.art/install.sh | bash
#
# Pulls the right build from the latest GitHub release, VERIFIES it against the
# release SHA256SUMS.txt, and drops it in place. No Python needed — the app
# bundles the mint server and the full web UI.
#
# Want the library instead (encode / decode / verify in your own code)?
#   pip install mememage
set -euo pipefail

REPO="sememtac/mememage-provenance"
DL="https://github.com/$REPO/releases/latest/download"
RELEASES="https://github.com/$REPO/releases/latest"

m()   { printf '\033[38;5;170m▚\033[0m %s\n' "$*"; }        # magenta mark
die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# Verify a downloaded file against the release SHA256SUMS.txt. Fail closed: a
# missing checksum file, an unlisted asset, or a mismatch all abort the install.
verify() {  # verify <file> <asset_name>
  local file="$1" name="$2" sums="$tmp/SHA256SUMS.txt" want actual
  m "Verifying checksum…"
  curl -fsSL "$DL/SHA256SUMS.txt" -o "$sums" \
    || die "Could not fetch the checksum file (SHA256SUMS.txt). Aborting for safety.
  Download and verify manually: $RELEASES"
  want="$(awk -v n="$name" '$2 == n || $2 == "*"n {print $1; exit}' "$sums")"
  [ -n "$want" ] || die "No checksum published for $name. Aborting.  See: $RELEASES"
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$file" | awk '{print $1}')"
  else
    actual="$(shasum -a 256 "$file" | awk '{print $1}')"
  fi
  [ "$want" = "$actual" ] || die "Checksum mismatch for $name — do NOT run it.
  expected $want
  got      $actual"
  m "Checksum verified (SHA-256)."
}

OS="$(uname -s)"
ARCH="$(uname -m)"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT

case "$OS" in
  Darwin)
    if [ "$ARCH" = "x86_64" ]; then
      m "Heads up: this build is Apple-Silicon (arm64). On an Intel Mac, run"
      m "  pip install \"mememage[mint]\" && mememage serve"
      m "instead. Continuing anyway…"
    fi
    m "Downloading Mememage for macOS…"
    curl -fL --progress-bar "$DL/Mememage-Provenance-macOS.zip" -o "$tmp/m.zip" \
      || die "Download failed. Grab it manually: $RELEASES"
    verify "$tmp/m.zip" "Mememage-Provenance-macOS.zip"
    /usr/bin/unzip -oq "$tmp/m.zip" -d "$tmp"
    [ -d "$tmp/Mememage.app" ] || die "Unexpected archive contents (no Mememage.app)."
    dest="/Applications"
    [ -w "$dest" ] || { dest="$HOME/Applications"; mkdir -p "$dest"; }
    rm -rf "$dest/Mememage.app"
    cp -R "$tmp/Mememage.app" "$dest/Mememage.app"
    # Unsigned build — clear any quarantine flag so Gatekeeper opens it directly.
    xattr -dr com.apple.quarantine "$dest/Mememage.app" 2>/dev/null || true
    m "Installed → $dest/Mememage.app"
    if open "$dest/Mememage.app" 2>/dev/null; then
      m "Launching…"
    else
      m "Open it from $dest, or double-click Mememage in Finder."
    fi
    ;;

  Linux)
    m "Downloading Mememage for Linux…"
    curl -fL --progress-bar "$DL/Mememage-Provenance-Linux" -o "$tmp/mememage-app" \
      || die "Download failed. Grab it manually: $RELEASES"
    verify "$tmp/mememage-app" "Mememage-Provenance-Linux"
    bin="$HOME/.local/bin"; mkdir -p "$bin"
    mv "$tmp/mememage-app" "$bin/mememage-app"
    chmod +x "$bin/mememage-app"
    m "Installed → $bin/mememage-app"
    case ":$PATH:" in
      *":$bin:"*) : ;;
      *) m "Tip: add $bin to your PATH to run it by name." ;;
    esac
    m "Start it with:  mememage-app"
    ;;

  *)
    die "Unsupported system: $OS.
  Windows → download Mememage-Provenance-Windows.exe from $RELEASES
  Developers → pip install mememage"
    ;;
esac

m "Done. First launch opens the dashboard in your browser."
