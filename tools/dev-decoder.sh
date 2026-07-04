#!/bin/bash
# tools/dev-decoder.sh — Serve the decoder locally for development
#
# Usage:
#   bash tools/dev-decoder.sh              # serve at http://localhost:8888
#   bash tools/dev-decoder.sh --standalone # generate + open standalone.html
#
# Changes to JS/CSS/HTML are reflected on refresh (no build step).
# The split source files in docs/ are the source of truth.
# standalone.html is generated from them and should not be edited directly.

set -euo pipefail
cd "$(dirname "$0")/../docs"

case "${1:-serve}" in
    --standalone)
        python3 -c "
from mememage.site_pack import inline_all
html = inline_all()
with open('standalone.html', 'w') as f:
    f.write(html)
print(f'Generated standalone.html ({len(html)//1024} KB)')
"
        open standalone.html
        ;;
    *)
        echo "Decoder dev server: http://localhost:8888"
        echo "Edit docs/js/*.js, docs/css/*.css, docs/index.html — refresh to see changes"
        echo ""
        python3 -m http.server 8888
        ;;
esac
