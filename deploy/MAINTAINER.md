# 维护者部署准备

朋友只需看 [6 步操作手册](README.md)。本文由项目维护者处理 GitHub、ACR 和交接文件。

<a id="maintainer"></a>

## 维护者配置：交接给朋友前完成

**先确认部署文件已合并到 main。** 工作流必须已经在 main 上，朋友拿到本地文件不代表 GitHub Actions 已能运行。

**A. ACR 创建两个仓库。** 在已有个人版实例中选好命名空间，创建 `c156-backend`、`c156-frontend`。复制实际公网 registry 域名。

**B. GitHub 填参数。** Repository 参数在 **Settings → Secrets and variables → Actions**，普通参数填 Variables，凭据填 Secrets。再到 **Settings → Environments** 创建 `production`，填写对应参数。

| 位置 | 名称 | 填写内容 |
| --- | --- | --- |
| Repository variable | `DEPLOY_ENABLED` | 准备就绪后设 `true`；缺省只测试和构建 |
| Repository variable | `ACR_REGISTRY` | ACR 公网域名，不带协议或路径 |
| Repository variable | `ACR_NAMESPACE` | 已创建的命名空间 |
| Repository secret | `ACR_USERNAME` | ACR 登录用户名 |
| Repository secret | `ACR_PASSWORD` | ACR 登录密码／访问凭据 |
| production variable | `DEPLOY_HOST` | ECS 公网 IP 或 SSH 域名 |
| production variable | `DEPLOY_PORT` | SSH 端口，通常 `22` |
| production variable | `DEPLOY_USER` | 与朋友执行手册的服务器账号相同，具备 Docker、目录及 Nginx 操作权限 |
| production variable | `DEPLOY_ROOT` | `/srv/c156` |
| production secret | `DEPLOY_SSH_KEY` | 专用 SSH 私钥；对应公钥先加入服务器该账号的 authorized_keys |
| production secret | `DEPLOY_KNOWN_HOSTS` | 核对指纹后的主机公钥记录；非 22 端口用 `[host]:port` 格式 |

SSH 指纹必须在可信终端核对，不在流水线临时扫描后直接信任。ACR 密码、SSH 私钥不要放进仓库或服务器 config.env。

**C. 打包交接文件。** 在包含本次部署代码的本地仓库运行：

```bash
git archive --format=zip --output=c156-deploy.zip HEAD deploy
```

交付压缩包及开头的信息表。等朋友完成第 3 步的 Docker 登录后再运行 prepare；完成第 5 步的面板配置后再正式部署。首次安装期间避免额外推送 main，因为它也会触发正式部署。


## 技术说明

数据位置：

```text
/srv/c156/config.env        唯一服务器配置
/srv/c156/data/             整个 SQLite 目录，包含 WAL/SHM
/srv/c156/assets/           资产挂载目录
/srv/c156/backups/          经过校验的快照
/srv/c156/nginx/proxy.inc   项目代理片段
/srv/c156/releases/         每次发布文件和同版本源码
/srv/c156/current           当前成功发布
/srv/c156/previous          上一次成功发布
/srv/c156/prepared          首次准备好的发布
```

请求经过面板 Nginx（80/443）→ 本机 28156 → 前端容器 → FastAPI；后端不公开宿主机端口。默认只构建 amd64，匹配朋友的 x86_64 服务器。提供的 `6.6.47-12.oc9` 内核更接近 OpenCloudOS 9，可用 `cat /etc/os-release` 确认，不照 CentOS 7 的安装教程操作。

GitHub runner 拉官方基础镜像，ECS 拉 ACR 成品镜像。基础镜像固定 digest；阿里云 Docker Hub 加速器存在同步及范围限制，不用它给 runner 保证最新基础镜像。网络受限时，维护者将相同基础镜像同步至可访问 ACR，再改 Dockerfile 引用。[加速器说明](https://help.aliyun.com/zh/acr/user-guide/accelerate-the-pulls-of-docker-official-images)

沿用已有 ACR 个人版，其定位为开发测试、不提供 SLA；需要生产可用性保障时再选择仓库等级。[版本说明](https://help.aliyun.com/zh/acr/product-overview/differences-between-personal-edition-instances-and-enterprise-edition-instances) 为兼容个人版，构建关闭 provenance/SBOM，测试后推同一镜像，部署固定 digest。发布目录名包含序号、重跑次数和 SHA，成功发布文件不会被重跑覆盖。

发布、源码、备份目前不自动删除。定期下载备份并检查磁盘空间。数据库跨版本迁移和灾难恢复需要人工安排，不能靠镜像回退恢复旧库。

本地镜像验证（维护者执行）：

```bash
BUILD_SHA=$(git rev-parse HEAD)
docker build --build-arg BUILD_SHA="$BUILD_SHA" -f deploy/backend.Dockerfile -t c156-backend:test .
docker build --build-arg BUILD_SHA="$BUILD_SHA" -f deploy/frontend.Dockerfile -t c156-frontend:test .
BUILD_SHA="$BUILD_SHA" BACKEND_IMAGE=c156-backend:test FRONTEND_IMAGE=c156-frontend:test python3 deploy/smoke.py
```

使用临时库和随机密码，检查 API 登录、读写、重建后持久化和退出。HTTP 检查手动发送 Secure Cookie；浏览器 HTTPS 行为在第 6 步人工确认。
