#!/usr/bin/env bash
set -euo pipefail

STAMP="$(date +%Y%m%d_%H%M)"
OUT_DIR="$HOME/server_snapshot_${STAMP}"
ARCHIVE="$HOME/server_snapshot_${STAMP}.tar.gz"

mkdir -p "$OUT_DIR"

log() {
  printf "%s\n" "$*" >> "${OUT_DIR}/_commands.log"
}

run() {
  local cmd="$1"
  log "\$ ${cmd}"
  bash -lc "${cmd}" >> "${OUT_DIR}/_output.log" 2>&1 || true
}

# --- OS info ---
run "uname -a"
if command -v lsb_release >/dev/null 2>&1; then
  run "lsb_release -a"
else
  run "cat /etc/os-release"
fi

# --- services and processes ---
run "systemctl status nginx || true"
run "systemctl list-units --type=service | egrep -i 'nginx|ffmpeg|flask|python|node|ronaut|stream' || true"
run "ps aux | egrep -i 'nginx|ffmpeg|python|flask|node|ronaut|stream' || true"

# --- ports ---
if command -v ss >/dev/null 2>&1; then
  run "ss -lntp | egrep ':(1935|80|443|5050|3000)\\b' || true"
else
  run "netstat -lntp | egrep ':(1935|80|443|5050|3000)\\b' || true"
fi

# --- versions and locations ---
run "nginx -V"
run "which nginx || true"
run "which ffmpeg || true"
run "which ffprobe || true"
run "which python || true"
run "which node || true"

# --- nginx configs (avoid keys/certs) ---
NGINX_CONF_DIR="${OUT_DIR}/nginx_conf"
mkdir -p "$NGINX_CONF_DIR"

run "nginx -T | head -n 200"

if [[ -f /etc/nginx/nginx.conf ]]; then
  cp /etc/nginx/nginx.conf "${NGINX_CONF_DIR}/nginx.conf"
fi

if [[ -d /etc/nginx/sites-enabled ]]; then
  mkdir -p "${NGINX_CONF_DIR}/sites-enabled"
  cp /etc/nginx/sites-enabled/* "${NGINX_CONF_DIR}/sites-enabled/" 2>/dev/null || true
fi

if [[ -d /etc/nginx/conf.d ]]; then
  mkdir -p "${NGINX_CONF_DIR}/conf.d"
  cp /etc/nginx/conf.d/* "${NGINX_CONF_DIR}/conf.d/" 2>/dev/null || true
fi

# remove keys and certs if copied by wildcard
find "${NGINX_CONF_DIR}" -type f \( -name "*.key" -o -name "*.pem" -o -name "*.crt" \) -delete 2>/dev/null || true

# --- runtime filesystem layout ---
run "ls -lah /var/www/html /var/www/html/hls /var/run /root || true"
run "stat /var/www/html/hls || true"
run "stat /var/run/stream.lock || true"

# --- locate repo ---
run "find /root /srv /opt -maxdepth 3 -type d -name '*ronaut*' 2>/dev/null || true"
run "find /root /srv /opt -maxdepth 4 -type f -name 'start_stream*.sh' 2>/dev/null || true"
run "find /root /srv /opt -maxdepth 4 -type f -name '*now_playing*' 2>/dev/null || true"

# --- logs (tail only) ---
LOG_DIR="${OUT_DIR}/logs"
mkdir -p "$LOG_DIR"

for f in /var/log/nginx/error.log /var/log/nginx/access.log; do
  if [[ -f "$f" ]]; then
    tail -n 200 "$f" > "${LOG_DIR}/$(basename "$f")"
  fi
done

# common ffmpeg log locations seen in repo
for f in /root/ffmpeg_random_stream.log /root/ffmpeg.log /var/log/ffmpeg.log; do
  if [[ -f "$f" ]]; then
    tail -n 200 "$f" > "${LOG_DIR}/$(basename "$f")"
  fi
done

# common API log locations (adjust if needed)
for f in /var/log/ronaut_api.log /var/log/flask.log /root/flask.log; do
  if [[ -f "$f" ]]; then
    tail -n 200 "$f" > "${LOG_DIR}/$(basename "$f")"
  fi
done

# --- package into tarball ---
tar -czf "$ARCHIVE" -C "$OUT_DIR" .
echo "Created: $ARCHIVE"
