#!/usr/bin/env bash
# deploy/public_mirror.sh — submission day (Chris). STUB: exactly what it must do, in order.
#
#  1. Export the working tree at the chosen commit into a temp directory (git archive).
#  2. Drop journal/ and AUDIT.md.
#  3. Trim ml/models/metrics.md to its headline block (the numbers said on stage).
#  4. Init a fresh repo there with ONE squashed commit.
#  5. Grep the tree for: .env, keys (backend/keys, *.key, private key material), ml/data,
#     raw timestamps (real dates outside demo/'s shifted or 2020 ranges), and any unqualified
#     "real night" claim (every "real" must sit beside the qualifier from DEMO_SCRIPT.md).
#     Abort on any hit.
#  6. Dry-run: print the tree and the grep results for review.
#  7. Push to the fresh public mirror named in the Meta form. The working repo stays private.
set -euo pipefail
echo "public_mirror.sh is a stub; see the header for the steps." >&2
exit 1
