#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="${BASE_DIR}/run"
: "${DOCKER_CONTAINER:=ronaut-nginx-rtmp}"

: "${CLEANUP_HLS:=1}"

find_docker() {
  if command -v docker >/dev/null; then
    DOCKER_BIN="$(command -v docker)"
  elif [[ -x "/usr/local/bin/docker" ]]; then
    DOCKER_BIN="/usr/local/bin/docker"
  elif [[ -x "/Applications/Docker.app/Contents/Resources/bin/docker" ]]; then
    DOCKER_BIN="/Applications/Docker.app/Contents/Resources/bin/docker"
  else
    DOCKER_BIN=""
  fi
}

stop_pid() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file")"
    if [[ -n "$pid" ]]; then
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  fi
}

stop_pid "${RUN_DIR}/streamer.pid"
stop_pid "${RUN_DIR}/api.pid"

find_docker
if [[ -n "${DOCKER_BIN:-}" ]]; then
  "$DOCKER_BIN" rm -f "$DOCKER_CONTAINER" >/dev/null 2>&1 || true
fi

if [[ "${CLEANUP_HLS}" == "1" ]]; then
  rm -f "${BASE_DIR}/hls/"*.m3u8 "${BASE_DIR}/hls/"*.ts 2>/dev/null || true
fi

echo "Local stack stopped."
