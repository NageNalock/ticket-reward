#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv"
LOG_DIR="${PROJECT_DIR}/data/logs"

mkdir -p "${LOG_DIR}"
cd "${PROJECT_DIR}"

if [[ -f "${PROJECT_DIR}/data/login_expired.flag" ]]; then
  printf '%s\n' "[$(date '+%Y-%m-%d %H:%M:%S')] Login expired, skipping run"
  exit 0
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  printf '%s\n' "Virtual environment missing. Run: bash scripts/setup.sh" >&2
  exit 1
fi

SCHEDULE_VALUES="$("${VENV_DIR}/bin/python" -c 'from src.utils.config_loader import load_config; s = load_config()["schedule"]; print(int(s["jitter_sec"]), int(s.get("retry_count", 3)), int(s.get("retry_interval_min", 30)))')"
IFS=' ' read -r JITTER_SEC RETRY_COUNT RETRY_INTERVAL_MIN <<< "${SCHEDULE_VALUES}"
if (( JITTER_SEC > 0 )); then
  sleep $(( RANDOM % (JITTER_SEC + 1) ))
fi

RUN_DAY="$(date '+%Y-%m-%d')"
RETRY_NUMBER=0

while true; do
  set +e
  "${VENV_DIR}/bin/python" -m src.main --mode headless --trigger scheduled
  EXIT_CODE=$?
  set -e

  if (( EXIT_CODE == 0 )); then
    exit 0
  fi
  if (( EXIT_CODE == 130 )) || [[ -f "${PROJECT_DIR}/data/login_expired.flag" ]]; then
    exit "${EXIT_CODE}"
  fi
  if (( RETRY_NUMBER >= RETRY_COUNT )); then
    exit "${EXIT_CODE}"
  fi

  NEXT_RETRY_EPOCH=$(( $(date '+%s') + RETRY_INTERVAL_MIN * 60 ))
  if [[ "$(date -r "${NEXT_RETRY_EPOCH}" '+%Y-%m-%d')" != "${RUN_DAY}" ]]; then
    printf '%s\n' "[$(date '+%Y-%m-%d %H:%M:%S')] Retry skipped because it would cross into the next day"
    exit "${EXIT_CODE}"
  fi

  RETRY_NUMBER=$(( RETRY_NUMBER + 1 ))
  printf '%s\n' "[$(date '+%Y-%m-%d %H:%M:%S')] Scheduled run failed with exit code ${EXIT_CODE}; retry ${RETRY_NUMBER}/${RETRY_COUNT} in ${RETRY_INTERVAL_MIN} minutes"
  sleep $(( RETRY_INTERVAL_MIN * 60 ))
done
