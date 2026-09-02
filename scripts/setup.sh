#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"

cd "${PROJECT_DIR}"
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r requirements.txt
"${VENV_DIR}/bin/python" -m playwright install chromium
mkdir -p data/browser_profile data/logs data/screenshots

if [[ "${1:-}" == "--no-launchd" ]]; then
  printf '%s\n' "依赖安装完成，已按要求跳过 launchd 安装。"
  printf '%s\n' "下一步: ${VENV_DIR}/bin/python scripts/login.py"
  exit 0
fi

GENERATED_PLIST="$("${VENV_DIR}/bin/python" scripts/render_plist.py)"
LAUNCH_AGENTS_DIR="${HOME}/Library/LaunchAgents"
TARGET_PLIST="${LAUNCH_AGENTS_DIR}/com.user.bingrewards.plist"
mkdir -p "${LAUNCH_AGENTS_DIR}"
cp "${GENERATED_PLIST}" "${TARGET_PLIST}"

launchctl bootout "gui/${UID}" "${TARGET_PLIST}" >/dev/null 2>&1 || true
launchctl bootstrap "gui/${UID}" "${TARGET_PLIST}"

printf '%s\n' "安装完成，launchd 将按 config/config.yaml 中的时间执行。"
printf '%s\n' "下一步: ${VENV_DIR}/bin/python scripts/login.py"
