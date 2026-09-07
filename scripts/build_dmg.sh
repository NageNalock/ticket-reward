#!/bin/bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "$0")/.." && pwd)"
APP_PATH="${PROJECT_DIR}/dist/Bing Rewards.app"
DMG_NAME="Bing-Rewards-macOS-arm64.dmg"
DMG_PATH="${PROJECT_DIR}/dist/${DMG_NAME}"

if [[ ! -d "${APP_PATH}" ]]; then
  printf '%s\n' "Application bundle missing. Run: bash scripts/build_app.sh" >&2
  exit 1
fi

codesign --verify --deep --strict "${APP_PATH}"
mkdir -p "${PROJECT_DIR}/build"
TEMP_DIR="$(mktemp -d "${PROJECT_DIR}/build/dmg.XXXXXX")"
trap 'rm -rf "${TEMP_DIR}"' EXIT
STAGING_DIR="${TEMP_DIR}/payload"
mkdir -p "${STAGING_DIR}"

# Copy only the signed app and installation shortcut, without local xattrs.
ditto --noextattr --noqtn --norsrc "${APP_PATH}" "${STAGING_DIR}/Bing Rewards.app"
ln -s /Applications "${STAGING_DIR}/Applications"
"${PROJECT_DIR}/.venv/bin/python" "${PROJECT_DIR}/scripts/dmg_layout.py" "${STAGING_DIR}"
codesign --verify --deep --strict "${STAGING_DIR}/Bing Rewards.app"

hdiutil create \
  -volname "Bing Rewards" \
  -fs HFS+ \
  -format ULMO \
  -nospotlight \
  -anyowners \
  -srcfolder "${STAGING_DIR}" \
  "${TEMP_DIR}/installer.dmg"
hdiutil verify "${TEMP_DIR}/installer.dmg"
mv -f "${TEMP_DIR}/installer.dmg" "${DMG_PATH}"
(
  cd "${PROJECT_DIR}/dist"
  shasum -a 256 "${DMG_NAME}" > SHA256SUMS.txt
)
printf '%s\n' "Installer: ${DMG_PATH}"
