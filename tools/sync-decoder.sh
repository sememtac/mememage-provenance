#!/bin/bash
# tools/sync-decoder.sh — RETIRED (2026-07-04).
#
# This used to publish docs/ (decoder + validator) to the GitHub Pages repo
# sememtac/mememage-decoder as a static fallback. That repo has been renamed to
# sememtac/mememage-site and repurposed as the mememage.art product-page apex
# (see tools/sync-product.sh). GitHub redirects the old name, so running the old
# sync would CLOBBER the product page — it's disabled.
#
# The live decoder + validator now come from:
#   - souls.mememage.art  — the VPS, fed by the main-repo `git pull`
#   - the chain itself     — every conception carries decoder/validator chunks
#     (blasted to IA), so anyone can reconstruct a working pair from the chain.
#
# Deploy flow is now:  commit -> cache-bust -> push origin -> VPS `git pull`.
echo "sync-decoder.sh is RETIRED — the decoder Pages repo became the product-page apex." >&2
echo "Live decoder/validator: souls.mememage.art (VPS) + reconstructable from the chain." >&2
echo "Publishing the decoder here would clobber mememage.art. See the header comment." >&2
exit 1
