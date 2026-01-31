#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${BASE_DIR}/.venv"

: "${API_PORT:=5050}"
: "${NOW_PLAYING_FILE:=${BASE_DIR}/run/now_playing.txt}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "Python venv not found. Run: ${BASE_DIR}/run_local.sh setup"
  exit 1
fi

export API_PORT
export NOW_PLAYING_FILE
export PYTHONUNBUFFERED=1

exec "${VENV_DIR}/bin/python" "${BASE_DIR}/now_playing_api.py"
