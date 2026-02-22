#!/usr/bin/env bash
set -euo pipefail

export SMB_USER="${SMB_USER:-micam}"
export SMB_PASSWORD="${SMB_PASSWORD:-}"
export SMB_WORKGROUP="${SMB_WORKGROUP:-WORKGROUP}"
export SMB_SHARE_NAME="${SMB_SHARE_NAME:-MI_CAMERA}"
export SMB_MIN_PROTOCOL="${SMB_MIN_PROTOCOL:-NT1}"
export SMB_MAX_PROTOCOL="${SMB_MAX_PROTOCOL:-SMB3}"
export SMB_CLIENT_MIN_PROTOCOL="${SMB_CLIENT_MIN_PROTOCOL:-NT1}"
export SMB_ENABLE_LANMAN="${SMB_ENABLE_LANMAN:-no}"
export SMB_INTERFACES="${SMB_INTERFACES:-lo eth0}"
export SMB_BIND_INTERFACES_ONLY="${SMB_BIND_INTERFACES_ONLY:-yes}"
export SMB_NETBIOS_NAME="${SMB_NETBIOS_NAME:-MICAMNAS}"
export SMB_LOCAL_MASTER="${SMB_LOCAL_MASTER:-yes}"
export SMB_PREFERRED_MASTER="${SMB_PREFERRED_MASTER:-yes}"
export SMB_OS_LEVEL="${SMB_OS_LEVEL:-255}"
export SMB_DOMAIN_MASTER="${SMB_DOMAIN_MASTER:-yes}"
export SMB_WINS_SUPPORT="${SMB_WINS_SUPPORT:-yes}"
export INBOX_PATH="${INBOX_PATH:-/data/inbox}"
export WEBDAV_MOUNT_PATH="${WEBDAV_MOUNT_PATH:-/mnt/webdav}"
export WEBDAV_URL="${WEBDAV_URL:-}"
export WEBDAV_USER="${WEBDAV_USER:-}"
export WEBDAV_PASS="${WEBDAV_PASS:-}"
export WEBDAV_REMOTE_PATH="${WEBDAV_REMOTE_PATH:-/}"
export STATE_DB_PATH="${STATE_DB_PATH:-/data/state/state.db}"
export API_ALLOW_NO_TOKEN="${API_ALLOW_NO_TOKEN:-false}"
export API_TOKEN="${API_TOKEN:-}"

valid_protocols=(CORE COREPLUS LANMAN1 LANMAN2 NT1 SMB2 SMB2_02 SMB2_10 SMB3 SMB3_00 SMB3_02 SMB3_11)
protocol_order=(CORE COREPLUS LANMAN1 LANMAN2 NT1 SMB2 SMB2_02 SMB2_10 SMB3 SMB3_00 SMB3_02 SMB3_11)
protocol_ok=false
for p in "${valid_protocols[@]}"; do
  if [[ "${SMB_MIN_PROTOCOL}" == "${p}" ]]; then
    protocol_ok=true
    break
  fi
done
if [[ "${protocol_ok}" != true ]]; then
  echo "ERROR: unsupported SMB_MIN_PROTOCOL=${SMB_MIN_PROTOCOL}" >&2
  exit 1
fi
protocol_ok=false
for p in "${valid_protocols[@]}"; do
  if [[ "${SMB_MAX_PROTOCOL}" == "${p}" ]]; then
    protocol_ok=true
    break
  fi
done
if [[ "${protocol_ok}" != true ]]; then
  echo "ERROR: unsupported SMB_MAX_PROTOCOL=${SMB_MAX_PROTOCOL}" >&2
  exit 1
fi

min_idx=-1
max_idx=-1
for idx in "${!protocol_order[@]}"; do
  if [[ "${protocol_order[$idx]}" == "${SMB_MIN_PROTOCOL}" ]]; then
    min_idx=${idx}
  fi
  if [[ "${protocol_order[$idx]}" == "${SMB_MAX_PROTOCOL}" ]]; then
    max_idx=${idx}
  fi
done
if (( min_idx < 0 || max_idx < 0 || min_idx > max_idx )); then
  echo "ERROR: SMB_MIN_PROTOCOL must be less than or equal to SMB_MAX_PROTOCOL" >&2
  exit 1
fi

if [[ ! "${SMB_USER}" =~ ^[a-z_][a-z0-9_-]{0,31}$ ]]; then
  echo "ERROR: SMB_USER must match ^[a-z_][a-z0-9_-]{0,31}$" >&2
  exit 1
fi

if [[ ! "${SMB_SHARE_NAME}" =~ ^[A-Za-z0-9._\ -]{1,64}$ ]]; then
  echo "ERROR: SMB_SHARE_NAME contains unsupported characters" >&2
  exit 1
fi

if [[ "${SMB_ENABLE_LANMAN}" != "yes" && "${SMB_ENABLE_LANMAN}" != "no" ]]; then
  echo "ERROR: SMB_ENABLE_LANMAN must be yes or no" >&2
  exit 1
fi

if [[ -z "${SMB_PASSWORD}" ]]; then
  echo "ERROR: SMB_PASSWORD is required" >&2
  exit 1
fi

if [[ -z "${WEBDAV_URL}" || -z "${WEBDAV_USER}" || -z "${WEBDAV_PASS}" ]]; then
  echo "ERROR: WEBDAV_URL, WEBDAV_USER, and WEBDAV_PASS are required" >&2
  exit 1
fi

allow_no_token_normalized="$(printf '%s' "${API_ALLOW_NO_TOKEN}" | tr '[:upper:]' '[:lower:]')"
if [[ -z "${API_TOKEN}" && "${allow_no_token_normalized}" != "1" && "${allow_no_token_normalized}" != "true" && "${allow_no_token_normalized}" != "yes" && "${allow_no_token_normalized}" != "on" ]]; then
  echo "ERROR: API_TOKEN is required unless API_ALLOW_NO_TOKEN=true" >&2
  exit 1
fi

mkdir -p "${INBOX_PATH}" "$(dirname "${STATE_DB_PATH}")" "${WEBDAV_MOUNT_PATH}" /run/rclone
chmod 0770 "${INBOX_PATH}"
chmod 0700 /run/rclone

if ! id -u "${SMB_USER}" >/dev/null 2>&1; then
  useradd --system --no-create-home --shell /usr/sbin/nologin "${SMB_USER}"
fi

chown -R "${SMB_USER}:${SMB_USER}" "${INBOX_PATH}"

if pdbedit -L | cut -d: -f1 | grep -Fxq -- "${SMB_USER}"; then
  printf '%s\n%s\n' "${SMB_PASSWORD}" "${SMB_PASSWORD}" | smbpasswd -s "${SMB_USER}" >/dev/null
else
  printf '%s\n%s\n' "${SMB_PASSWORD}" "${SMB_PASSWORD}" | smbpasswd -s -a "${SMB_USER}" >/dev/null
fi
smbpasswd -e "${SMB_USER}" >/dev/null

envsubst < /etc/samba/smb.conf.template > /etc/samba/smb.conf

if [[ "${SMB_MIN_PROTOCOL}" == "NT1" ]]; then
  echo "WARNING: SMB1 (NT1) enabled. Restrict access to isolated camera subnet." >&2
fi

exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf
