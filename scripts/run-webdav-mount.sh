#!/usr/bin/env bash
set -euo pipefail

umask 077

WEBDAV_MOUNT_PATH="${WEBDAV_MOUNT_PATH:-/mnt/webdav}"
WEBDAV_URL="${WEBDAV_URL:?WEBDAV_URL is required}"
WEBDAV_USER="${WEBDAV_USER:?WEBDAV_USER is required}"
WEBDAV_PASS="${WEBDAV_PASS:?WEBDAV_PASS is required}"
WEBDAV_REMOTE_PATH="${WEBDAV_REMOTE_PATH:-/}"

if [[ -z "${WEBDAV_REMOTE_PATH}" ]]; then
  WEBDAV_REMOTE_PATH="/"
fi
WEBDAV_REMOTE_PATH="/${WEBDAV_REMOTE_PATH#/}"
if [[ "${WEBDAV_REMOTE_PATH}" == *".."* ]]; then
  echo "ERROR: WEBDAV_REMOTE_PATH must not contain '..'" >&2
  exit 1
fi

mkdir -p "${WEBDAV_MOUNT_PATH}" /run/rclone
chmod 0700 /run/rclone

if command -v mountpoint >/dev/null 2>&1 && mountpoint -q "${WEBDAV_MOUNT_PATH}"; then
  fusermount3 -u "${WEBDAV_MOUNT_PATH}" || true
fi

RCLONE_CONF="/run/rclone/rclone.conf"
OBSCURED_PASS="$(rclone obscure "${WEBDAV_PASS}")"

cat > "${RCLONE_CONF}" <<EOF
[webdav]
type = webdav
url = ${WEBDAV_URL}
vendor = other
user = ${WEBDAV_USER}
pass = ${OBSCURED_PASS}
EOF
chmod 0600 "${RCLONE_CONF}"

exec rclone mount "webdav:${WEBDAV_REMOTE_PATH}" "${WEBDAV_MOUNT_PATH}" \
  --config "${RCLONE_CONF}" \
  --allow-other \
  --vfs-cache-mode writes \
  --vfs-cache-max-age 10m \
  --vfs-cache-max-size 1G \
  --dir-cache-time 30s \
  --poll-interval 15s \
  --umask 027 \
  --uid 0 \
  --gid 0 \
  --log-level INFO
