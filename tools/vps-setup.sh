#!/usr/bin/env bash
#
# vps-setup.sh — one-command public self-host for the Mememage mint server.
#
# Stands up the clean, trusted, port-less deployment that the project
# recommends, the same setup walked through by hand in the docs:
#
#   • Dashboard:      https://<mint-domain>/<MINT_API_TOKEN>   (no port, no path)
#   • Souls (Source): https://<souls-domain>/<id>.soul          (decoder Source)
#   • GPS-capture:    https://<mint-domain>/mint/<session>      (auto-generated)
#
# It issues a multi-SAN Let's Encrypt certificate, writes the nginx vhosts
# (both full reverse proxy — the mint server routes the admin face vs the
# public decoder/souls face itself by Host header), installs a
# renewal deploy-hook that re-copies the cert into the mint server and restarts
# it, and points the mint server at the clean domain. Idempotent — safe to
# re-run.
#
# Assumes: Ubuntu/Debian, nginx + certbot installed, passwordless sudo, and the
# mememage mint server already installed as a systemd --user service
# (`mememage install`). The DNS A records must already point at this host.
#
# Usage:
#   bash tools/vps-setup.sh --mint mint.example.com --email you@example.com \
#                           [--souls souls.example.com] [--port 8444] [--staging]
#
# Testing:  pass --staging to use Let's Encrypt's STAGING CA — the full ACME +
# nginx + systemd flow runs for real, but the cert is untrusted and there are
# no production rate limits, so you can re-run the whole script as many times
# as you like on a throwaway VPS. Drop --staging for the final, trusted cert.
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
MINT=""; SOULS=""; EMAIL=""; PORT="8444"; SVC_USER="$(id -un)"; STAGING=""
usage() {
  # Print the leading comment block (the doc header) as help, stripping "# ".
  awk 'NR>1 && /^#/ {sub(/^# ?/,""); print; next} NR>1 {exit}' "$0"
  exit "${1:-1}"
}
while [ $# -gt 0 ]; do
  case "$1" in
    --staging) STAGING=1; shift ;;   # LE staging CA — untrusted certs, no rate limits (testing)
    -h|--help) usage 0 ;;
    --mint|--souls|--email|--port|--user)
      [ $# -ge 2 ] || { echo "ERROR: $1 needs a value (did the command wrap onto two lines? paste it as ONE line)." >&2; exit 1; }
      case "$1" in
        --mint)  MINT="$2" ;;
        --souls) SOULS="$2" ;;
        --email) EMAIL="$2" ;;
        --port)  PORT="$2" ;;
        --user)  SVC_USER="$2" ;;
      esac
      shift 2 ;;
    *) echo "Unknown arg: $1" >&2; usage 1 ;;
  esac
done
[ -n "$MINT" ]  || { echo "ERROR: --mint <domain> is required" >&2; usage 1; }
[ -n "$EMAIL" ] || { echo "ERROR: --email <addr> is required (Let's Encrypt notices)" >&2; usage 1; }

SVC_UID="$(id -u "$SVC_USER")"
SVC_HOME="$(getent passwd "$SVC_USER" | cut -d: -f6)"
CERT_DIR="$SVC_HOME/.mememage/certs"
SERVER_JSON="$SVC_HOME/.mememage/server.json"
LINEAGE="/etc/letsencrypt/live/mememage"   # forced via --cert-name for determinism

say()  { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
say "Preflight"
[ "$(uname -s)" = "Linux" ] || die "This script targets a Linux VPS."
sudo -n true 2>/dev/null   || die "Passwordless sudo is required (certbot + nginx need root)."
command -v nginx   >/dev/null || die "nginx not installed.   Install: sudo apt install -y nginx"
command -v certbot >/dev/null || die "certbot not installed. Install: sudo apt install -y certbot python3-certbot-nginx"
# Accept either the live systemd view OR the unit file on disk — the lingering
# --user session can lag on a fresh SSH, but the installed unit is what matters.
{ XDG_RUNTIME_DIR="/run/user/$SVC_UID" systemctl --user list-unit-files 2>/dev/null \
    | grep -q '^mememage-mint\.service'; } \
  || [ -f "$SVC_HOME/.config/systemd/user/mememage-mint.service" ] \
  || die "mememage-mint user-service not found. Run \`mememage install\` first, then re-run this."

# Resolve this host's public IP, then confirm each domain points here.
hostip="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || \
          curl -fsS --max-time 10 https://ifconfig.me 2>/dev/null || true)"
[ -n "$hostip" ] || warn "Couldn't determine this host's public IP (offline?). Skipping the DNS match check."
resolve() { python3 -c "import socket,sys
try: print(socket.gethostbyname(sys.argv[1]))
except OSError: print('')" "$1"; }
# True when $1 resolves to this host (or we couldn't determine our own IP).
dns_points_here() {
  local ip; ip="$(resolve "$1")"
  [ -n "$ip" ] || return 1
  [ -z "$hostip" ] || [ "$ip" = "$hostip" ]
}

# Required: the mint/dashboard domain MUST point here — abort if not.
if dns_points_here "$MINT"; then
  ok "$MINT -> $(resolve "$MINT")"
else
  die "$MINT does not point to this host (${hostip:-unknown}). Add or fix the A record so $MINT resolves to this host, then re-run."
fi

# Optional: the souls domain. A bad/typo'd souls domain must NOT abort the whole
# setup — it's a bonus clean face. Warn, drop it, continue (souls stay reachable
# at $MINT/api/souls/). Fix the record and re-run to add https://$SOULS/.
if [ -n "$SOULS" ]; then
  if dns_points_here "$SOULS"; then
    ok "$SOULS -> $(resolve "$SOULS")"
  else
    warn "souls domain $SOULS does not point here — SKIPPING the clean souls face. Souls are still served at https://$MINT/api/souls/. Fix its A record and re-run to add https://$SOULS/."
    SOULS=""
  fi
fi
ok "user=$SVC_USER (uid $SVC_UID), port=$PORT, home=$SVC_HOME"

# Cloud images (Vultr, etc.) often ship ufw active, SSH-only — which drops the
# ACME challenge on :80 and the public :443/<port>. Open them so the cert can
# be issued and the server reached.
if command -v ufw >/dev/null 2>&1 && sudo ufw status 2>/dev/null | grep -q '^Status: active'; then
  for p in 80 443 "$PORT"; do sudo ufw allow "$p/tcp" >/dev/null 2>&1 || true; done
  sudo ufw reload >/dev/null 2>&1 || true
  ok "ufw active — opened 80, 443, $PORT"
else
  ok "ufw inactive/absent (ports already open)"
fi

# ---------------------------------------------------------------------------
# 1. Certificate (multi-SAN: mint [+ souls])
# ---------------------------------------------------------------------------
say "Issuing Let's Encrypt certificate${STAGING:+ (STAGING — untrusted, for testing)}"
# Staging<->production switch: if a cert under our name already exists and its
# mode differs from what's requested now, force a fresh issue. Otherwise certbot
# keeps the old cert ("not due for renewal") and a staging->production re-run
# would silently stay untrusted. Cert files update in place, so nginx keeps
# working — no delete, no broken vhost reference.
FORCE_FLAG=""
if sudo test -f /etc/letsencrypt/renewal/mememage.conf; then
  existing_staging=""
  sudo grep -qi 'staging' /etc/letsencrypt/renewal/mememage.conf 2>/dev/null && existing_staging=1
  if { [ -n "$STAGING" ] && [ -z "$existing_staging" ]; } \
     || { [ -z "$STAGING" ] && [ -n "$existing_staging" ]; }; then
    FORCE_FLAG="--force-renewal"
    ok "switching cert mode (staging<->production) — forcing a fresh issue"
  fi
fi
CERTBOT_ARGS=(certonly --nginx --cert-name mememage --expand
              --non-interactive --agree-tos -m "$EMAIL" -d "$MINT")
[ -n "$SOULS" ]      && CERTBOT_ARGS+=(-d "$SOULS")
# --staging uses LE's test CA: full ACME flow, certs NOT browser-trusted, but
# no production rate limits — so you can re-run the whole script repeatedly
# while testing. Drop --staging for the real, trusted cert.
[ -n "$STAGING" ]    && CERTBOT_ARGS+=(--test-cert)
[ -n "$FORCE_FLAG" ] && CERTBOT_ARGS+=("$FORCE_FLAG")
sudo certbot "${CERTBOT_ARGS[@]}"
# /etc/letsencrypt/live is root-only (0700), and this script runs unprivileged —
# probe with sudo, not a bare [ -f ] (which would false-negative).
sudo test -f "$LINEAGE/fullchain.pem" || die "Cert not found at $LINEAGE after certbot run."
ok "cert covers: $MINT${SOULS:+ + $SOULS}${STAGING:+  (staging/untrusted)}"

# ---------------------------------------------------------------------------
# 2. Point the mint server at the clean domain (server.json) + cert paths
# ---------------------------------------------------------------------------
say "Configuring the mint server (server.json)"
sudo -u "$SVC_USER" mkdir -p "$CERT_DIR"
sudo -u "$SVC_USER" python3 - "$SERVER_JSON" "$MINT" "$PORT" "$CERT_DIR" "$SOULS" <<'PY'
import json, os, sys
path, domain, port, certdir, souls = sys.argv[1:6]
d = {}
if os.path.exists(path):
    try: d = json.load(open(path))
    except Exception: d = {}
d["domain"] = domain
d["port"]   = int(port)
d["cert"]   = os.path.join(certdir, "mememage.crt")
d["key"]    = os.path.join(certdir, "mememage.key")
# The clean public souls face (when --souls is set). The self-push channel
# reads this so soul links become https://<souls>/<id>.soul and the
# conception page shows the domain instead of the raw bind host.
if souls:
    d["souls_domain"] = souls
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump(d, open(path, "w"), indent=2)
os.chmod(path, 0o600)
print(f"  domain={domain} port={port}" + (f" souls={souls}" if souls else ""))
PY
ok "server.json: domain=$MINT, port=$PORT${SOULS:+, souls=$SOULS}, cert -> $CERT_DIR/mememage.{crt,key}"

# ---------------------------------------------------------------------------
# 3. Renewal deploy-hook: copy cert into the mint server, restart it, reload nginx
# ---------------------------------------------------------------------------
say "Installing the renewal deploy-hook"
sudo loginctl enable-linger "$SVC_USER" >/dev/null 2>&1 || true
HOOK=/etc/letsencrypt/renewal-hooks/deploy/mememage.sh
sudo tee "$HOOK" >/dev/null <<HOOK_EOF
#!/bin/bash
# Auto-generated by mememage vps-setup.sh. On each renewal: copy the fresh cert
# into the mint server's (user-readable) cert dir, restart the user-service,
# reload nginx.
set -e
cp "$LINEAGE/fullchain.pem" "$CERT_DIR/mememage.crt"
cp "$LINEAGE/privkey.pem"  "$CERT_DIR/mememage.key"
chown $SVC_USER:$SVC_USER "$CERT_DIR/mememage.crt" "$CERT_DIR/mememage.key"
chmod 644 "$CERT_DIR/mememage.crt"
chmod 600 "$CERT_DIR/mememage.key"
sudo -u $SVC_USER XDG_RUNTIME_DIR=/run/user/$SVC_UID systemctl --user restart mememage-mint
systemctl reload nginx
HOOK_EOF
sudo chmod +x "$HOOK"
ok "deploy-hook at $HOOK (copies cert, restarts mint, reloads nginx)"
# Run it once now to install the cert + restart against the new config.
sudo "$HOOK"
sleep 2
[ "$(systemctl --user is-active mememage-mint)" = "active" ] \
  && ok "mint service restarted (active)" || warn "mint service is not active — check: journalctl --user -u mememage-mint"

# ---------------------------------------------------------------------------
# 4. nginx vhosts (443): both full-proxy — the mint server routes the
#    admin face (mint host) vs the public decode face (souls host) itself
#    by the Host header (_is_souls_face). The souls host serves the
#    decoder/validator + raw souls; the mint host serves the dashboard.
# ---------------------------------------------------------------------------
say "Writing nginx vhosts"
write_vhost() {  # $1 = server_name, $2 = proxy target path suffix (normally "")
  local name="$1" suffix="$2"
  sudo tee "/etc/nginx/sites-available/$name" >/dev/null <<NGINX_EOF
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name $name;

    ssl_certificate     $LINEAGE/fullchain.pem;
    ssl_certificate_key $LINEAGE/privkey.pem;

    # Above the backend's payload/soul caps (512 MiB) so the backend's 400 is
    # the authoritative "too large", not an nginx 413. Payload sources (audio,
    # video, big images) and large peer soul pushes run to hundreds of MB.
    client_max_body_size 600m;

    location / {
        proxy_pass https://127.0.0.1:$PORT$suffix;
        proxy_ssl_verify off;                 # backend is local; cert CN is the public name
        proxy_set_header Host \$host;          # port-less Host -> mint server emits clean URLs
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For \$remote_addr;
        proxy_request_buffering off;          # stream large uploads straight to the backend
        proxy_read_timeout 300s;              # headroom for large uploads over slow links
    }
}
NGINX_EOF
  sudo ln -sf "/etc/nginx/sites-available/$name" "/etc/nginx/sites-enabled/$name"
  ok "vhost $name -> 127.0.0.1:$PORT${suffix:-/ (full)}"
}
write_vhost "$MINT" ""                       # admin: dashboard + /mint + /api/*
# souls host is full-proxy too — the server's Host-aware router turns it
# into the public decode face (decoder/validator + raw souls at root),
# and existing souls.<domain>/<id>.soul URLs keep resolving.
[ -n "$SOULS" ] && write_vhost "$SOULS" ""
sudo nginx -t
sudo systemctl reload nginx
ok "nginx reloaded"

# ---------------------------------------------------------------------------
# 5. Verify (the way a browser / iPhone would)
# ---------------------------------------------------------------------------
say "Verifying"
# Two checks, both robust. (1) Reachability via the public URL, retried because
# nginx swaps workers async after a reload. (2) Trust is asserted from the
# ISSUED CERT FILE's issuer (deterministic) rather than a live TLS handshake —
# a box's own hairpin-to-itself trust check is timing-flaky and was crying wolf
# on a perfectly good cert.
verify_reachable() {  # $1 = url, $2 = label
  local http i
  for i in 1 2 3 4 5 6; do
    http="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 12 "$1" || echo 000)"
    [ "$http" != "000" ] && { ok "$2 reachable (HTTP $http)"; return; }
    sleep 2
  done
  die "$2 not reachable after retries. Check DNS / nginx / firewall."
}
verify_reachable "https://$MINT/health" "$MINT"
[ -n "$SOULS" ] && verify_reachable "https://$SOULS/" "$SOULS"

cert_issuer="$(sudo openssl x509 -in "$LINEAGE/fullchain.pem" -noout -issuer 2>/dev/null || echo '')"
if [ -n "$STAGING" ]; then
  ok "cert: STAGING — untrusted by design (drop --staging for a real cert)"
elif echo "$cert_issuer" | grep -qi "let's encrypt" && ! echo "$cert_issuer" | grep -qiE 'staging|fake'; then
  ok "cert: browser-trusted (${cert_issuer#issuer=})"
else
  warn "cert issuer looks non-production (${cert_issuer:-unknown}) — verify trust from an external client."
fi

# ---------------------------------------------------------------------------
# Admin-token presence (security on a public domain). The server reads
# MINT_API_TOKEN from <repo>/.env (this script lives in <repo>/tools/), so check
# THERE — not just ~/.env — or we'd warn "not set" when it actually is.
# ---------------------------------------------------------------------------
REPO_ENV="$(cd "$(dirname "$0")/.." 2>/dev/null && pwd)/.env"
token_set=""
{ [ -f "$REPO_ENV" ] && grep -q '^MINT_API_TOKEN=' "$REPO_ENV"; } && token_set=1
{ [ -z "$token_set" ] && [ -f "$SVC_HOME/.env" ] && grep -q '^MINT_API_TOKEN=' "$SVC_HOME/.env"; } && token_set=1
[ -n "${MINT_API_TOKEN:-}" ] && token_set=1
if [ -z "$token_set" ]; then
  warn "MINT_API_TOKEN is not set — your dashboard/API is open to anyone on a public domain."
  warn "Set one in the dashboard (Config -> Server -> Generate phrase) or in $REPO_ENV"
fi

# Final summary — skipped when bootstrap drives this (bootstrap prints the one
# real summary, with the actual token, so we don't print a second placeholder one).
if [ -z "${MEMEMAGE_BOOTSTRAP:-}" ]; then
  hint="<MINT_API_TOKEN>"; [ -n "$token_set" ] && hint="<your MINT_API_TOKEN>"
  say "Done — your clean public self-host"
  echo "  Dashboard:  https://$MINT/$hint"
  [ -n "$SOULS" ] && echo "  Decoder Source (souls): https://$SOULS/"
  echo "  GPS-capture links the phone receives are now clean: https://$MINT/mint/<session>"
  echo
  echo "  Renewal is automatic (certbot timer + the deploy-hook). Re-run this"
  echo "  script anytime to reconcile config."
fi
