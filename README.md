# MiCam Sync / 米家相机同步服务

MiCam Sync is a containerized service for syncing Xiaomi camera files from SMB inbox to a WebDAV mount, with a built-in web UI for setup and monitoring.

MiCam Sync 是一个容器化服务，用于把小米摄像头上传到 SMB 共享目录的文件，同步到 WebDAV 挂载目录，并提供内置 Web UI 进行配置与监控。

## Features / 功能

- SMB share for camera upload / SMB 共享接收相机文件
- WebDAV mount and sync worker / WebDAV 挂载与同步任务
- Step-by-step setup wizard + dashboard (Chinese/English) / 分步向导 + 主界面（中英文）
- Queue/health/config APIs / 队列、健康检查、配置 API
- GitHub Actions Docker publish workflow / GitHub Actions 自动构建发布 Docker 镜像

## Docker Deploy (GHCR image) / Docker 部署（GHCR 镜像）

Target image / 目标镜像：

```bash
docker pull ghcr.io/aijiaobin/micam-sync:latest
```

### 1) Create networks (macvlan + management bridge) / 创建网络（macvlan + 管理桥接）

> Replace `eth0`, subnet and gateway with your actual LAN settings.
> 请将 `eth0`、子网和网关改成你的实际局域网参数。

```bash
docker network create -d bridge mgmt_net

docker network create -d macvlan \
  --subnet=192.168.50.0/24 \
  --gateway=192.168.50.1 \
  -o parent=eth0 \
  camera_net
```

### 2) Create volumes / 创建数据卷

```bash
docker volume create micam_data
docker volume create micam_webdav
```

### 3) Redeploy container / 重新部署容器

```bash
docker rm -f micam-sync 2>/dev/null || true

docker run -d \
  --name micam-sync \
  --restart unless-stopped \
  --env-file .env \
  --network mgmt_net \
  -p 8080:8080 \
  --cap-add SYS_ADMIN \
  --device /dev/fuse \
  --security-opt apparmor:unconfined \
  --security-opt no-new-privileges:true \
  -v micam_data:/data \
  -v micam_webdav:/mnt/webdav \
  ghcr.io/aijiaobin/micam-sync:latest

docker network connect --ip 192.168.50.20 camera_net micam-sync
```

UI / 管理界面：

- `http://localhost:8080`

## Configuration / 配置说明

Main env variables (see `.env.example`) / 主要环境变量（见 `.env.example`）：

- SMB: `SMB_USER`, `SMB_PASSWORD`, `SMB_SHARE_NAME`, protocol settings
- WebDAV: `WEBDAV_URL`, `WEBDAV_USER`, `WEBDAV_PASS`, `WEBDAV_REMOTE_PATH`
- API/UI: `API_HOST`, `API_PORT`, `API_TOKEN`, `API_ALLOW_NO_TOKEN`
- Runtime: `INBOX_PATH`, `STATE_DB_PATH`, `WEBDAV_MOUNT_PATH`

## GitHub Actions Docker Publish

Workflow file / 工作流文件：`.github/workflows/docker-publish.yml`

- Build on pull requests / PR 触发构建（不推送）
- Build & push to GHCR on `main` and `v*` tags / `main` 与 `v*` 标签触发构建并推送 GHCR

## Notes / 备注

- `setup_completed` is persisted in backend config (not browser-only state).
- `setup_completed` 会持久化到后端配置（不是仅浏览器状态）。
