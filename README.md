# MiCam Sync

MiCam Sync is a containerized service for syncing Xiaomi camera files from SMB inbox to a WebDAV mount, with a built-in web UI for setup and monitoring.

## Features

- SMB share for camera upload
- WebDAV mount and sync worker
- Step-by-step web setup wizard (Chinese/English)
- Queue/health/config APIs
- Docker build and GitHub Actions image publish workflow

## Project Structure

- `app/micam_sync/` - Flask API, sync scanner/worker, state DB
- `app/micam_sync/templates/index.html` - Web UI
- `scripts/` - Entrypoint and WebDAV mount scripts
- `docker/` - Samba and supervisord configs
- `tests/` - Runtime safety tests
- `Dockerfile` - Runtime image build

## Local Run (Docker)

Build image:

```bash
docker build -t micam-sync:latest .
```

Run container (test mode, bridge network):

```bash
docker run -d \
  --name micam-sync \
  --restart unless-stopped \
  --env-file .env \
  -p 8080:8080 \
  -v micam_data:/data \
  -v micam_webdav:/mnt/webdav \
  micam-sync:latest
```

UI:

- `http://localhost:8080`

## Configuration

Main env variables (see `.env.example`):

- SMB: `SMB_USER`, `SMB_PASSWORD`, `SMB_SHARE_NAME`, protocol settings
- WebDAV: `WEBDAV_URL`, `WEBDAV_USER`, `WEBDAV_PASS`, `WEBDAV_REMOTE_PATH`
- API/UI: `API_HOST`, `API_PORT`, `API_TOKEN`, `API_ALLOW_NO_TOKEN`
- Runtime: `INBOX_PATH`, `STATE_DB_PATH`, `WEBDAV_MOUNT_PATH`

## GitHub Actions Docker Publish

Workflow file: `.github/workflows/docker-publish.yml`

- Build on pull requests
- Build & push to GHCR on `main` and `v*` tags

## Notes

- For production camera subnet isolation, use macvlan/ipvlan compose variants as needed.
- `setup_completed` is persisted in backend config (not browser-only state).
