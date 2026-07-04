#!/usr/bin/env bash
#
# bootstrap.sh — a fresh Ubuntu/Debian box → a working, publicly-trusted
# Mememage self-host, in ONE command. Run as root.
#
# It does everything: installs system deps, creates an unprivileged service
# user, sets up a Python venv + installs mememage, generates an admin token,
# runs `mememage install` (the auto-start service), opens the firewall, and
# runs vps-setup.sh (Let's Encrypt cert + nginx + clean port-less URLs). At the
# end it prints your dashboard + souls URLs.
#
# Prereq: DNS A record(s) for your domain(s) already point at this box, and the
# mememage source tree is present (this script lives in it, at <repo>/tools/).
#
# Usage (as root):
#   sudo bash <repo>/tools/bootstrap.sh \
#        --domain mint.example.com --email you@example.com \
#        [--souls souls.example.com] [--user mememage] [--port 8443] [--staging]
#
set -euo pipefail

DOMAIN=""; SOULS=""; EMAIL=""; SVC_USER="mememage"; PORT="8443"; STAGING=""
usage() { awk 'NR>1 && /^#/{sub(/^# ?/,"");print;next} NR>1{exit}' "$0"; exit "${1:-1}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --staging) STAGING="--staging"; shift ;;
    -h|--help) usage 0 ;;
    --domain|--souls|--email|--user|--port)
      # Clear error if a flag is missing its value — usually means the command
      # got split across lines. Beats bash's "$2: unbound variable".
      [ $# -ge 2 ] || { echo "ERROR: $1 needs a value (did the command wrap onto two lines? paste it as ONE line)." >&2; exit 1; }
      case "$1" in
        --domain) DOMAIN="$2" ;;
        --souls)  SOULS="$2" ;;
        --email)  EMAIL="$2" ;;
        --user)   SVC_USER="$2" ;;
        --port)   PORT="$2" ;;
      esac
      shift 2 ;;
    *) echo "Unknown arg: $1" >&2; usage 1 ;;
  esac
done
[ "$(id -u)" = "0" ] || { echo "ERROR: run as root (sudo bash ...)." >&2; exit 1; }

# Interactive fallback — so the typed command can be just `bash bootstrap.sh`
# (short, never wraps). When a value wasn't passed as a flag AND we have a
# terminal, just ask. Flags still win (for automation / non-interactive runs).
if [ -t 0 ]; then
  [ -n "$DOMAIN" ] || read -rp "Dashboard domain (e.g. mint.yourdomain.com): " DOMAIN
  [ -n "$EMAIL" ]  || read -rp "Email (for Let's Encrypt renewal notices): " EMAIL
  [ -n "$SOULS" ]  || read -rp "Souls domain for the decoder (optional — Enter to skip): " SOULS
fi
# Default is a real, browser-trusted production cert — no test-mode prompt to
# confuse an artist who just wants it to work. --staging stays as a flag-only
# escape hatch for power users iterating against the LE staging CA.

[ -n "$DOMAIN" ] || { echo "ERROR: a domain is required (flag --domain or the prompt)." >&2; usage 1; }
[ -n "$EMAIL" ]  || { echo "ERROR: an email is required (flag --email or the prompt)." >&2; usage 1; }

REPO_SRC="$(cd "$(dirname "$0")/.." && pwd)"   # the repo containing this script
SVC_HOME="/home/$SVC_USER"
REPO="$SVC_HOME/Mememage"
VENV="$SVC_HOME/.venv/mememage"

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null
apt-get install -y nginx certbot python3-certbot-nginx git python3-pip python3-venv rsync >/dev/null
systemctl enable --now nginx >/dev/null 2>&1 || true
ok "nginx, certbot, python venv, git"

# ---------------------------------------------------------------------------
say "Service user: $SVC_USER"
if ! id "$SVC_USER" >/dev/null 2>&1; then
  adduser --disabled-password --gecos "" "$SVC_USER" >/dev/null
fi
echo "$SVC_USER ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/$SVC_USER"
chmod 440 "/etc/sudoers.d/$SVC_USER"
# Let the same SSH key in (so you can log in directly as $SVC_USER later).
if [ -f /root/.ssh/authorized_keys ]; then
  install -d -m700 -o "$SVC_USER" -g "$SVC_USER" "$SVC_HOME/.ssh"
  cp /root/.ssh/authorized_keys "$SVC_HOME/.ssh/authorized_keys"
  chown "$SVC_USER:$SVC_USER" "$SVC_HOME/.ssh/authorized_keys"
  chmod 600 "$SVC_HOME/.ssh/authorized_keys"
fi
loginctl enable-linger "$SVC_USER" >/dev/null 2>&1 || true
SVC_UID="$(id -u "$SVC_USER")"
ok "ready (uid $SVC_UID, passwordless sudo, linger on)"

# Helper: run a command as the service user with a usable systemd --user env.
as_user() { sudo -u "$SVC_USER" env XDG_RUNTIME_DIR="/run/user/$SVC_UID" HOME="$SVC_HOME" bash -c "$1"; }

# ---------------------------------------------------------------------------
say "Mememage source"
if [ "$REPO_SRC" != "$REPO" ]; then
  # --exclude .env is load-bearing: without it, --delete wipes the user's .env
  # (their admin token + secrets) on every re-run, minting a fresh token and
  # silently changing the dashboard URL. Their config must survive re-runs.
  rsync -a --delete --exclude '.git' --exclude '.env' --exclude '__pycache__' \
        --exclude '*.pyc' "$REPO_SRC/" "$REPO/"
fi
chown -R "$SVC_USER:$SVC_USER" "$REPO"
ok "at $REPO"

# ---------------------------------------------------------------------------
say "Python venv + install"
# Install with the [mint] extra — Pillow + numpy are load-bearing for a mint
# server (bar embedding, thumbnails, the watermark). Without them the server
# boots and serves the dashboard but every conception fails at bar-embed.
# (Ed25519 signing stays optional — the dashboard's "Install signing" button
# adds cryptography on demand.)
as_user "python3 -m venv '$VENV' && '$VENV/bin/pip' install -q --upgrade pip && '$VENV/bin/pip' install -q -e '$REPO[mint]'"
ok "installed into $VENV"

# ---------------------------------------------------------------------------
say "Admin token"
# A public box should be token-gated. Generate one and write it to the repo
# .env (the server reads <repo>/.env), unless the user already set one.
if as_user "grep -q '^MINT_API_TOKEN=' '$REPO/.env' 2>/dev/null"; then
  ok "MINT_API_TOKEN already set — leaving it"
  TOKEN="$(as_user "grep '^MINT_API_TOKEN=' '$REPO/.env' | head -1 | cut -d= -f2-")"
else
  TOKEN="$(as_user "'$VENV/bin/python' -c 'from mememage.tokens import generate_word_token; print(generate_word_token(12))'")"
  as_user "umask 177; printf 'MINT_API_TOKEN=%s\n' '$TOKEN' >> '$REPO/.env'"
  ok "generated (shown at the end)"
fi

# ---------------------------------------------------------------------------
say "Install the mint service (auto-start)"
as_user "cd '$REPO' && XDG_RUNTIME_DIR=/run/user/$SVC_UID '$VENV/bin/mememage' install" >/dev/null 2>&1 || \
  as_user "cd '$REPO' && '$VENV/bin/mememage' install"
sleep 2
ok "mememage-mint service: $(as_user 'systemctl --user is-active mememage-mint' 2>/dev/null || echo unknown)"

# ---------------------------------------------------------------------------
say "TLS + nginx (clean public URLs)"
VPS_ARGS="--mint $DOMAIN --email $EMAIL --port $PORT $STAGING"
[ -n "$SOULS" ] && VPS_ARGS="$VPS_ARGS --souls $SOULS"
# MEMEMAGE_BOOTSTRAP tells vps-setup to skip its own summary — bootstrap prints
# the single final one below (with the real token, not a placeholder).
as_user "cd '$REPO' && MEMEMAGE_BOOTSTRAP=1 bash tools/vps-setup.sh $VPS_ARGS"

# ---------------------------------------------------------------------------
say "Done"
echo "  Dashboard:  https://$DOMAIN/$TOKEN"
[ -n "$SOULS" ] && echo "  Souls (decoder Source): https://$SOULS/"
echo
echo "  Bookmark the dashboard URL. To log in as the service user later:"
echo "    ssh $SVC_USER@<this-host>"
