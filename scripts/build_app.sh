#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
BROWSER_DIR="${PROJECT_DIR}/build/playwright-browsers"
WORK_DIR="${PROJECT_DIR}/build/pyinstaller"
DIST_DIR="${PROJECT_DIR}/dist"
APP_PATH="${DIST_DIR}/Bing Rewards.app"

cd "${PROJECT_DIR}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  printf '%s\n' "Virtual environment missing. Run: bash scripts/setup.sh --no-launchd" >&2
  exit 1
fi

"${VENV_DIR}/bin/python" -m pip install -r requirements-build.txt
bash scripts/build_icon.sh
"${VENV_DIR}/bin/python" scripts/write_build_info.py
mkdir -p "${BROWSER_DIR}" "${DIST_DIR}"
PLAYWRIGHT_BROWSERS_PATH="${BROWSER_DIR}" \
  "${VENV_DIR}/bin/python" -m playwright install chromium

rm -rf "${WORK_DIR}" "${APP_PATH}"
"${VENV_DIR}/bin/pyinstaller" \
  --noconfirm \
  --clean \
  --workpath "${WORK_DIR}" \
  --distpath "${DIST_DIR}" \
  BingRewards.spec

mkdir -p "${APP_PATH}/Contents/Resources/ms-playwright"
ditto "${BROWSER_DIR}" "${APP_PATH}/Contents/Resources/ms-playwright"
# Playwright's installation registry contains paths from the build machine.
rm -rf "${APP_PATH}/Contents/Resources/ms-playwright/.links"
codesign --force --sign - "${APP_PATH}"
codesign --verify --deep --strict "${APP_PATH}"
bash scripts/build_dmg.sh

printf '%s\n' "Built: ${APP_PATH}"
