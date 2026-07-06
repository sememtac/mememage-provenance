#!/bin/bash
# tools/sync-product.sh — Publish the product landing page (docs/product.html)
# to the APEX Pages repo, which owns mememage.art.
#
# product.html becomes index.html at the repo root, so mememage.art/ IS the
# product page. Only the css/js/img it actually references are published (parsed
# from the page — NOT the whole tree, to avoid exposing the decoder/admin JS on a
# public repo). This writes the CNAME so the APEX repo owns mememage.art.
#
# NOTE (2026-07-04): the apex repo IS the former sememtac/mememage-decoder,
# renamed to sememtac/mememage-site — so it already holds the mememage.art CNAME
# (no domain move needed). The decoder/validator no longer live on GitHub Pages;
# the live pair is souls.mememage.art (VPS) and is reconstructable from the chain
# chunks on IA. sync-decoder.sh is retired accordingly.
#
# ALLOW-LIST: only the landing page + shared assets. No decoder/validator/soul
# faces are published here.
#
#   bash tools/sync-product.sh
set -euo pipefail

DOCS_DIR="$(cd "$(dirname "$0")/../docs" && pwd)"
SITE_REPO="/tmp/mememage-site"
# The apex Pages repo. Change this if you pick a different name (e.g. the user
# site sememtac/sememtac.github.io). Whatever it is, it must have Pages enabled
# and the mememage.art DNS pointing at GitHub Pages.
REMOTE="git@github.com:sememtac/mememage-site.git"
APEX="mememage.art"

# Clone or pull. rev-parse validates the cached clone (a corrupt .git husk passes
# `-d .git` but fails every git op) — re-clone fresh if it's broken.
if git -C "$SITE_REPO" rev-parse --git-dir >/dev/null 2>&1; then
    cd "$SITE_REPO"
    git pull --rebase origin main 2>/dev/null || true
else
    rm -rf "$SITE_REPO"
    git clone "$REMOTE" "$SITE_REPO"
    cd "$SITE_REPO"
fi

# Publish ONLY the assets product.html actually references (parsed from the page)
# — NEVER the whole css/js tree. This repo is public at mememage.art; syncing all
# of docs/js would re-expose the decoder/admin JS (dashboard.js, validator.js, …)
# that has nothing to do with the landing page. New assets on the page are picked
# up automatically; removed ones drop (css/js/img are rebuilt from scratch).
ASSETS=$(grep -oE '(href|src)="(css|js|img)/[^"?]+' "$DOCS_DIR/product.html" \
         | sed -E 's/.*"//' | sort -u)
rm -rf "$SITE_REPO/css" "$SITE_REPO/js" "$SITE_REPO/img"
for a in $ASSETS; do
    mkdir -p "$SITE_REPO/$(dirname "$a")"
    cp "$DOCS_DIR/$a" "$SITE_REPO/$a"
done

# Assets referenced only off-page (og:image meta, external READMEs) — the asset
# parser above misses them, so copy explicitly.
mkdir -p "$SITE_REPO/img"
cp "$DOCS_DIR/img/og.png"            "$SITE_REPO/img/og.png"             # social card
cp "$DOCS_DIR/img/mememage-icon.png" "$SITE_REPO/img/mememage-icon.png"  # icon for the GitHub READMEs

# SEO crawl files at the apex root.
cp "$DOCS_DIR/robots.txt"  "$SITE_REPO/robots.txt"
cp "$DOCS_DIR/sitemap.xml" "$SITE_REPO/sitemap.xml"

# The landing page IS the apex index.
cp "$DOCS_DIR/product.html" "$SITE_REPO/index.html"

# Install surface, published explicitly (not referenced by product.html):
#   /install      → install.html as a directory index (a HUMAN download page —
#                   browsers get HTML, not a scary octet-stream download; Windows
#                   visitors get the .exe button they actually need).
#   /install.sh   → the shell script for `curl -fsSL https://mememage.art/install.sh | bash`.
# Split because GitHub Pages serves an extensionless file as octet-stream, which
# Chrome flags/downloads — an HTML page can't double as a pipe target.
rm -rf "$SITE_REPO/install"                       # was a file in a prior sync; now a dir
mkdir -p "$SITE_REPO/install"
cp "$DOCS_DIR/install.html" "$SITE_REPO/install/index.html"
cp "$DOCS_DIR/install.sh"   "$SITE_REPO/install.sh"    # macOS / Linux pipe target
cp "$DOCS_DIR/install.ps1"  "$SITE_REPO/install.ps1"   # Windows PowerShell pipe target

# This repo owns the apex; .nojekyll so Pages skips Jekyll ({{...}} markers).
echo "$APEX" > "$SITE_REPO/CNAME"
touch "$SITE_REPO/.nojekyll"

if [ -n "$(git status --porcelain)" ]; then
    git add -A
    git commit -m "Update product page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
    git push origin main
    echo "Product page synced and pushed → https://$APEX/"
else
    echo "No changes to sync."
fi
