#!/usr/bin/env bash
set -euo pipefail

SMB_WORKGROUP="${SMB_WORKGROUP:-WORKGROUP}"
SMB_NETBIOS_NAME="${SMB_NETBIOS_NAME:-MICAMNAS}"

if [[ ! "${SMB_WORKGROUP}" =~ ^[A-Za-z0-9._-]{1,15}$ ]]; then
  echo "ERROR: SMB_WORKGROUP must match ^[A-Za-z0-9._-]{1,15}$" >&2
  exit 1
fi

if [[ ! "${SMB_NETBIOS_NAME}" =~ ^[A-Za-z0-9._-]{1,15}$ ]]; then
  echo "ERROR: SMB_NETBIOS_NAME must match ^[A-Za-z0-9._-]{1,15}$" >&2
  exit 1
fi

if command -v wsdd >/dev/null 2>&1; then
  exec wsdd --workgroup "${SMB_WORKGROUP}" --hostname "${SMB_NETBIOS_NAME}"
fi

if command -v wsdd2 >/dev/null 2>&1; then
  echo "INFO: wsdd2 detected; starting wsdd2 (workgroup/hostname controlled by Samba and system hostname)" >&2
  exec wsdd2
fi

echo "ERROR: neither wsdd nor wsdd2 is available in this image" >&2
exit 1
