# 报错排查清单

先看 Actions 失败步骤和日志，再按下表处理。不要先删 `data`、重置账号或修改其他站点。

| 现象 | 优先检查 | 处理 |
| --- | --- | --- |
| 没有推送／部署步骤 | `DEPLOY_ENABLED`、分支 | 仓库 variable 设为 `true`，使用 main；PR 只测试 |
| SSH Permission denied | 用户、公钥、私钥 secret | 核对 DEPLOY_USER、公钥安装位置和私钥匹配 |
| SSH Host key verification failed | DEPLOY_KNOWN_HOSTS、SSH 端口 | 在可信终端核对指纹，填写真实记录；不关闭 StrictHostKeyChecking |
| ACR unauthorized／denied | 控制台域名、命名空间、仓库、凭据 | 核对两个仓库已创建，用户名和 ACR 密码正确；服务器单独 docker login |
| 镜像拉取超时 | ECS 到 ACR 公网域名的 DNS/网络 | 使用该实例实际可达地址；不要把仅 VPC 可达地址填给 GitHub runner |
| manifest unknown／不支持媒体类型 | 镜像 digest 和 ACR 个人版兼容 | 保持单平台和关闭 provenance/SBOM，查看镜像 push 是否完整成功 |
| bind source path does not exist | /srv/c156 配置和目录 | 先 setup，核对 DEPLOY_ROOT；Compose 不自动创建错误路径 |
| port 28156 occupied | `ss -ltn 'sport = :28156'` | 换 APP_PORT，并同步面板代理；不停止不相关服务 |
| address pool overlaps | Docker 已有网络 | 更改 PROXY_NETWORK 为未使用的私有 IPv4 CIDR；前后端使用同一配置 |
| 数据目录 permission denied | APP_UID/GID、目录所有者、SELinux | setup 设置目录权限；SELinux 拒绝时管理员为这三个专用目录设置适合容器的持久标签，不关闭 SELinux |
| 未初始化／startup failed | 库是否存在、首次管理员是否创建 | 新库用明确 --init-db；已有数据先备份并检查，不自动 init/reset |
| Nginx duplicate proxy_pass | 面板生成代理与 include 重复 | 同一 location 只保留一套代理指令，或改 NGINX_MANAGED=0 |
| nginx not found／reload 失败 | 面板真实 nginx 路径与账号权限 | 设置 NGINX_BIN，使用该 binary 的 `-t`；核对面板进程使用的配置 |
| 502 或 backend unhealthy | 容器日志、数据库、网络 | 看下方命令；缺库不会被自动创建；不要仅重启 Nginx |
| 首页正常，API 403 | Host/Origin/HTTPS/代理头 | 域名与 SITE_DOMAIN 一致，保留 Host/Origin；公网入口覆盖 XFF/XFP |
| 登录后立刻未登录 | 浏览器是否 HTTPS、Cookie、代理域名 | 必须用配置的 HTTPS 域名；不要用 HTTP/IP 登录生产模式 |
| 静态资源 404 | 前端版本、缓存、镜像 | 检查 build-info.json 与 API SHA；清浏览器旧缓存，查看镜像构建日志 |
| 部署版本检测失败 | 两个 SHA、域名 DNS、TLS、面板代理 | 两个地址都应报告本次 SHA；部署不接受 301、错误站点或旧版本 |
| stale deployment rejected | 当前发布序号 | 老任务晚到会被拒绝；使用新一次 workflow run；回退走 rollback.sh |
| rollback failed | 原镜像可达、Nginx 校验、数据库兼容 | 发布失败不会推进 current；按日志人工修复，数据保持原样 |
| 证书过期／HTTPS 验证失败 | 面板续签任务、DNS、80/443、ACME location | 在面板修复并续签；本项目不另起 Certbot |
| 磁盘满 | backups、releases、Docker 镜像 | 先下载备份；仅清理确认不需要的旧快照/版本，保留 current/previous |

查看当前容器状态与日志：

```bash
bash -c 'source /srv/c156/current/common.sh; load_config /srv/c156; compose_for /srv/c156/current ps'
bash -c 'source /srv/c156/current/common.sh; load_config /srv/c156; compose_for /srv/c156/current logs --tail 100 backend frontend'
curl -fsS https://YOUR_DOMAIN/build-info.json
curl -fsS https://YOUR_DOMAIN/api/healthz
```

首次正式部署失败、还没有 current 时，用 `/srv/c156/prepared` 或 Actions 显示的具体 release 目录替换命令中的 current。代理和容器恢复失败会明确报错，不会假装成功。

初始化库后管理员步骤中断：确认库里还没有账号，用已准备镜像重新运行账号引导（不会修改已有账号）：

```bash
bash -c 'source /srv/c156/prepared/common.sh; load_config /srv/c156; compose_for /srv/c156/prepared run --rm --no-deps backend python -m src.identity bootstrap-admin --database /data/c156.sqlite --login-name admin --display-name 管理员'
```

数据库灾难恢复属于人工操作：停止 backend 和 frontend；把整个 data 目录移到独立事故留档位置；把已验证快照复制为新 data/c156.sqlite；设置 APP_UID/GID 所有权；用镜像内 `python -m src.storage init --database /data/c156.sqlite` 验证既有 schema 并配置 WAL（此命令不做跨版本迁移）；再启动并检查文档。资产从单独备份恢复。不要把活跃库的旧 WAL/SHM 搭配快照使用，也不要在服务运行时覆盖数据库。
