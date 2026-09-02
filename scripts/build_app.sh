#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
BROWSER_DIR="${PROJECT_DIR}/build/playwright-browsers"
WORK_DIR="${PROJECT_DIR}/build/pyinstaller"
DIST_DIR="${PROJECT_DIR}/dist"
APP_PATH="${DIST_DIR}/Bing Rewards.app"
ZIP_PATH="${DIST_DIR}/Bing-Rewards-macOS-arm64.zip"

cd "${PROJECT_DIR}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  printf '%s\n' "Virtual environment missing. Run: bash scripts/setup.sh --no-launchd" >&2
  exit 1
fi

"${VENV_DIR}/bin/python" -m pip install -r requirements-build.txt
mkdir -p "${BROWSER_DIR}" "${DIST_DIR}"
PLAYWRIGHT_BROWSERS_PATH="${BROWSER_DIR}" \
  "${VENV_DIR}/bin/python" -m playwright install chromium

rm -rf "${WORK_DIR}" "${APP_PATH}" "${ZIP_PATH}"
"${VENV_DIR}/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --workpath "${WORK_DIR}" \
  --distpath "${DIST_DIR}" \
  BingRewards.spec

mkdir -p "${APP_PATH}/Contents/Resources/ms-playwright"
ditto "${BROWSER_DIR}" "${APP_PATH}/Contents/Resources/ms-playwright"
codesign --force --sign - "${APP_PATH}"
codesign --verify --deep --strict "${APP_PATH}"
ditto -c -k --sequesterRsrc --keepParent "${APP_PATH}" "${ZIP_PATH}"

printf '%s\n' "Built: ${APP_PATH}"
printf '%s\n' "Archive: ${ZIP_PATH}"
