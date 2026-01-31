#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="${BASE_DIR}/run"
LOG_DIR="${BASE_DIR}/logs"
HLS_DIR="${BASE_DIR}/hls"
CONF_TEMPLATE="${BASE_DIR}/nginx_local.conf"
CONF_RENDERED="${RUN_DIR}/nginx_local.conf"

: "${RTMP_PORT:=1935}"
: "${HTTP_PORT:=8080}"
: "${API_PORT:=5050}"
: "${MOVIES_DIR:=/Users/andreabenedetti/Movies}"
: "${CLIP_SECONDS:=30}"
: "${START_OFFSET:=0}"
: "${NOW_PLAYING_FILE:=${RUN_DIR}/now_playing.txt}"
: "${FFMPEG_BIN:=/Users/andreabenedetti/opt/anaconda3/envs/ronaut-ffmpeg/bin/ffmpeg}"
: "${FFPROBE_BIN:=/Users/andreabenedetti/opt/anaconda3/envs/ronaut-ffmpeg/bin/ffprobe}"
: "${LOCK_FILE:=${RUN_DIR}/stream.lock}"
: "${DOCKER_IMAGE:=alfg/nginx-rtmp}"
: "${DOCKER_CONTAINER:=ronaut-nginx-rtmp}"
: "${NGINX_CONTAINER_HLS_PATH:=/var/www/html/hls}"

export PATH="/Users/andreabenedetti/opt/anaconda3/envs/ronaut-ffmpeg/bin:$PATH"

find_docker() {
  if command -v docker >/dev/null; then
    DOCKER_BIN="$(command -v docker)"
  elif [[ -x "/usr/local/bin/docker" ]]; then
    DOCKER_BIN="/usr/local/bin/docker"
  elif [[ -x "/Applications/Docker.app/Contents/Resources/bin/docker" ]]; then
    DOCKER_BIN="/Applications/Docker.app/Contents/Resources/bin/docker"
  else
    echo "Docker CLI not found. Install Docker Desktop 4.16.x for macOS 12 Intel and ensure Docker Desktop is running."
    echo "Try: open -a Docker"
    exit 1
  fi

  echo "Using Docker CLI at: ${DOCKER_BIN}"

  if ! "${DOCKER_BIN}" info >/dev/null 2>&1; then
    echo "Docker Desktop is not running. Start it: open -a Docker, wait until 'Docker is running', then rerun."
    exit 1
  fi
}

setup_dirs() {
  mkdir -p "$RUN_DIR" "$LOG_DIR" "$HLS_DIR"
}

check_deps() {
  if [[ ! -x "$FFMPEG_BIN" ]]; then
    echo "ffmpeg not found at: $FFMPEG_BIN"
    exit 1
  fi
  if [[ ! -x "$FFPROBE_BIN" ]]; then
    echo "ffprobe not found at: $FFPROBE_BIN"
    exit 1
  fi
  command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }

  if [[ ! -d "$MOVIES_DIR" ]]; then
    echo "Movies folder not found: $MOVIES_DIR"
    exit 1
  fi
}

render_nginx_conf() {
  sed \
    -e "s|__RTMP_PORT__|${RTMP_PORT}|g" \
    -e "s|__HTTP_PORT__|${HTTP_PORT}|g" \
    -e "s|__API_PORT__|${API_PORT}|g" \
    -e "s|__HLS_PATH__|${NGINX_CONTAINER_HLS_PATH}|g" \
    "$CONF_TEMPLATE" > "$CONF_RENDERED"

  echo "Rendered nginx config:"
  echo "  ${CONF_RENDERED}"
}

setup_python() {
  if [[ ! -d "${BASE_DIR}/.venv" ]]; then
    python3 -m venv "${BASE_DIR}/.venv"
  fi
  "${BASE_DIR}/.venv/bin/pip" install -r "${BASE_DIR}/requirements.txt"
}

start_nginx() {
  "${DOCKER_BIN}" rm -f "$DOCKER_CONTAINER" >/dev/null 2>&1 || true

  local docker_cmd
  docker_cmd="${DOCKER_BIN} run -d --name ${DOCKER_CONTAINER} \
    -p ${RTMP_PORT}:1935 -p ${HTTP_PORT}:8080 \
    -v \"${HLS_DIR}:${NGINX_CONTAINER_HLS_PATH}\" \
    -v \"${CONF_RENDERED}:/tmp/nginx.conf:ro\" \
    ${DOCKER_IMAGE} \
    nginx -g \"daemon off;\" -c /tmp/nginx.conf"

  echo "Docker run command:"
  echo "  ${docker_cmd}"

  eval "$docker_cmd" >/dev/null

  echo "Docker container status:"
  "${DOCKER_BIN}" ps --filter "name=${DOCKER_CONTAINER}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" || true

  if ! "${DOCKER_BIN}" ps --filter "name=${DOCKER_CONTAINER}" --filter "status=running" | grep -q "${DOCKER_CONTAINER}"; then
    echo "Container exited; logs:"
    "${DOCKER_BIN}" logs "${DOCKER_CONTAINER}" || true
    exit 1
  fi
}

start_api() {
  API_PORT="$API_PORT" NOW_PLAYING_FILE="$NOW_PLAYING_FILE" \
    nohup "${BASE_DIR}/run_api_local.sh" >> "${LOG_DIR}/api.log" 2>&1 &
  echo $! > "${RUN_DIR}/api.pid"
}

start_streamer() {
  MOVIES_DIR="$MOVIES_DIR" RTMP_PORT="$RTMP_PORT" \
    CLIP_SECONDS="$CLIP_SECONDS" START_OFFSET="$START_OFFSET" \
    NOW_PLAYING_FILE="$NOW_PLAYING_FILE" LOCK_FILE="$LOCK_FILE" \
    FFMPEG_BIN="$FFMPEG_BIN" FFPROBE_BIN="$FFPROBE_BIN" \
    nohup "${BASE_DIR}/start_stream_local_smart.sh" >> "${LOG_DIR}/ffmpeg.log" 2>&1 &
  echo $! > "${RUN_DIR}/streamer.pid"
}

print_urls() {
  echo "Local stack running:"
  echo "  HLS: http://localhost:${HTTP_PORT}/hls/stream.m3u8"
  echo "  API: http://localhost:${API_PORT}/now-playing"
  echo "  RTMP: rtmp://localhost:${RTMP_PORT}/live/stream"
}

case "${1:-run}" in
  setup)
    setup_dirs
    setup_python
    echo "Setup complete."
    ;;
  run|"")
    setup_dirs
    find_docker
    check_deps
    : > "$NOW_PLAYING_FILE"
    render_nginx_conf
    start_nginx
    start_api
    start_streamer
    print_urls
    ;;
  *)
    echo "Usage: $0 [setup]"
    exit 1
    ;;
esac
