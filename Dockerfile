FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    samba \
    samba-common-bin \
    wsdd \
    supervisor \
    rclone \
    fuse3 \
    iproute2 \
    gettext-base \
    ca-certificates \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN printf "user_allow_other\n" >> /etc/fuse.conf

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY docker/smb.conf.template /etc/samba/smb.conf.template
COPY docker/supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY scripts/entrypoint.sh /app/scripts/entrypoint.sh
COPY scripts/run-webdav-mount.sh /app/scripts/run-webdav-mount.sh

RUN chmod +x /app/scripts/entrypoint.sh /app/scripts/run-webdav-mount.sh \
    && mkdir -p /data/inbox /data/state /mnt/webdav /run/rclone /var/log/supervisor

EXPOSE 8080
EXPOSE 445
EXPOSE 139
EXPOSE 137/udp
EXPOSE 138/udp
EXPOSE 3702/udp
EXPOSE 5357

VOLUME ["/data", "/mnt/webdav"]

ENV INBOX_PATH=/data/inbox \
    STATE_DB_PATH=/data/state/state.db \
    WEBDAV_MOUNT_PATH=/mnt/webdav \
    API_HOST=0.0.0.0 \
    API_PORT=8080 \
    SYNC_DELAY_SECONDS=300

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
