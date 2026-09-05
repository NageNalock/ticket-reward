#!/bin/bash
# Convert the approved artwork into all native macOS icon representations.
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "$0")/.." && pwd)"
ICONSET_DIR="${PROJECT_DIR}/build/AppIcon.iconset"
mkdir -p "${ICONSET_DIR}"
for SIZE in 16 32 128 256 512; do
  sips -z "${SIZE}" "${SIZE}" "${PROJECT_DIR}/assets/mascot.png" \
    --out "${ICONSET_DIR}/icon_${SIZE}x${SIZE}.png" >/dev/null
  DOUBLE_SIZE=$((SIZE * 2))
  sips -z "${DOUBLE_SIZE}" "${DOUBLE_SIZE}" "${PROJECT_DIR}/assets/mascot.png" \
    --out "${ICONSET_DIR}/icon_${SIZE}x${SIZE}@2x.png" >/dev/null
done
iconutil -c icns "${ICONSET_DIR}" -o "${PROJECT_DIR}/assets/AppIcon.icns"
python3 "${PROJECT_DIR}/scripts/strip_asset_metadata.py" "${PROJECT_DIR}/assets/AppIcon.icns"
printf '%s\n' "Created assets/AppIcon.icns"
