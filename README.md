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

## Troubleshooting / 常见问题排查

### 1) SMB reachable from LAN client but not from Docker host (macvlan behavior)

If you deploy camera network with `macvlan`, the Linux host usually cannot directly access the container IP on that same macvlan network. This is expected macvlan behavior, not an SMB auth issue.

如果相机网络使用 `macvlan`，Linux 宿主机通常无法直接访问同一 macvlan 网络上的容器 IP。这是 macvlan 的典型行为，不是 SMB 账号权限问题。

Quick checks / 快速检查：

```bash
# run on another LAN client (not necessarily Docker host)
smbclient //172.20.0.218/MI_CAMERA -U 'micam' --password='***' -c 'ls'
```

If you need host↔container direct connectivity, consider an ipvlan profile (`docker-compose.ipvlan.yml`) or add a host macvlan shim.

若需要宿主机与容器直接互通，可使用 ipvlan 配置（`docker-compose.ipvlan.yml`）或在宿主机增加 macvlan shim。

### 2) Xiaomi camera discovery issues on /16 networks

Observed pitfall: with `/16` camera subnet, NetBIOS broadcast scope can differ from some IoT client expectations. Windows may still work (often helped by WSD), while camera validation can fail.

实测坑点：相机网段使用 `/16` 时，NetBIOS 广播范围可能与部分 IoT 固件预期不一致。Windows 可能仍可发现（常受 WSD 帮助），但摄像头验证可能失败。

Recommended baseline / 推荐基线：
- Prefer `/24` camera subnet unless you explicitly need `/16`.
- Keep Samba interface binding aligned with real NIC/IP.
- `wsdd` mainly improves Windows discovery; Xiaomi camera may rely on NetBIOS/SMB path.

- 优先使用 `/24` 相机网段（无特殊需求不建议 `/16`）。
- Samba 绑定网卡与真实 IP 要一致。
- `wsdd` 主要改善 Windows 发现，小米相机仍可能依赖 NetBIOS/SMB 路径。

Related env / 相关环境变量（见 `.env.example`）：
- `SMB_INTERFACES`
- `SMB_BIND_INTERFACES_ONLY`
- `SMB_NETBIOS_NAME`

### 3) WebDAV sync failed with `[Errno 5] Input/output error` + `409 Conflict`

This usually means directory creation conflict on WebDAV mount path (FUSE/rclone layer), not SMB permission failure.

这通常是 WebDAV 挂载路径上的目录创建冲突（FUSE/rclone 层），不是 SMB 权限失败。

Typical logs / 典型日志：
- `Dir.Mkdir failed to create directory: Conflict: 409 Conflict`
- `OSError: [Errno 5] Input/output error: '/mnt/webdav/...'

Quick recovery steps / 快速恢复步骤：

```bash
# check target subdir in runtime DB
docker exec micam-sync python -c 'import sqlite3; c=sqlite3.connect("/data/state/state.db"); print(c.execute("select key,value from settings where key=\"target_subdir\"").fetchall()); c.close()'

# pre-create conflicted directories directly against WebDAV remote
docker exec micam-sync sh -lc 'rclone mkdir --config /run/rclone/rclone.conf "webdav:/camera/xiaomi_camera_videos/b8888000cbf7/2026021618" -vv'

# release failed retry backoff immediately
docker exec micam-sync python -c 'import sqlite3; c=sqlite3.connect("/data/state/state.db"); c.execute("update files set next_retry_at=0 where state=\"failed\""); c.commit(); c.close()'
```

### 4) SMB permission model used by this project

MiCam Sync uses a single SMB upload account by default:
- share access restricted by `valid users = ${SMB_USER}`
- files forced to `SMB_USER` via `force user/force group`

MiCam Sync 默认使用单 SMB 上传账号：
- 共享由 `valid users = ${SMB_USER}` 限定访问
- 文件通过 `force user/force group` 统一归属 `SMB_USER`

So “camera account works while another account cannot access this share” is expected under default config.

因此在默认配置下，“相机账号可用而另一个账号不能访问该共享”是预期行为。

## GitHub Actions Docker Publish

Workflow file / 工作流文件：`.github/workflows/docker-publish.yml`

- Build on pull requests / PR 触发构建（不推送）
- Build & push to GHCR on `main` and `v*` tags / `main` 与 `v*` 标签触发构建并推送 GHCR

## Notes / 备注

- `setup_completed` is persisted in backend config (not browser-only state).
- `setup_completed` 会持久化到后端配置（不是仅浏览器状态）。
